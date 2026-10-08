import base64
import binascii
import gzip
import hashlib
import json
import re
import secrets
import shutil
import ssl
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .elf_check import ElfCheckError, board_rules, check_elf_for_board
from .models import (
    PROFILE_STATUSES,
    Artifact,
    Board,
    ElfSubmission,
    JenkinsJob,
    Status,
    TestCase,
    TestResult,
    TestRun,
)

# Suite categories in display order; TestCase.category holds one of these.
SUITE_CATEGORIES = ("Privileged", "Hypervisor", "Non-Privileged", "Vector")


def _uploaded_elf_details(upload):
    max_bytes = settings.PORTAL_ELF_UPLOAD_MAX_BYTES
    if not upload or upload.size <= 0:
        raise ValueError("Select a non-empty ELF file.")
    if upload.size > max_bytes:
        raise ValueError(f"ELF exceeds the {max_bytes // (1024 * 1024)} MiB upload limit.")

    header = upload.read(20)
    upload.seek(0)
    if len(header) < 20 or header[:4] != b"\x7fELF":
        raise ValueError("The uploaded file is not an ELF binary.")
    if header[4] != 2 or header[5] != 1:
        raise ValueError("Only little-endian ELF64 binaries are supported.")
    if int.from_bytes(header[18:20], "little") != 243:
        raise ValueError("The uploaded ELF is not for RISC-V.")

    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return Path(upload.name).name[:255], digest.hexdigest()


def _jenkins_ssl_context():
    ca_file = settings.JENKINS_CA_FILE.strip()
    return ssl.create_default_context(cafile=ca_file or None)


def _jenkins_request(url, authorization, *, data=None, headers=None):
    request_headers = {"Authorization": authorization}
    request_headers.update(headers or {})
    request = Request(url, data=data, headers=request_headers)
    return urlopen(request, timeout=30, context=_jenkins_ssl_context())


def _trigger_single_elf_job(request, submission, raw_download_token):
    base = settings.JENKINS_TRIGGER_URL.rstrip("/")
    user = settings.JENKINS_TRIGGER_USER
    token = settings.JENKINS_TRIGGER_TOKEN
    if not base or not user or not token:
        raise RuntimeError("Jenkins triggering is not configured on the portal.")

    authorization = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()
    crumb_headers = {}
    try:
        with _jenkins_request(f"{base}/crumbIssuer/api/json", authorization) as response:
            crumb = json.loads(response.read())
            crumb_headers[crumb["crumbRequestField"]] = crumb["crumb"]
    except HTTPError as exc:
        if exc.code != 404:
            raise

    download_path = reverse("elf-download", args=[submission.id])
    if settings.PORTAL_EXTERNAL_URL:
        download_url = settings.PORTAL_EXTERNAL_URL.rstrip("/") + download_path
    else:
        download_url = request.build_absolute_uri(download_path)
    parameters = urlencode(
        {
            "SUBMISSION_ID": str(submission.id),
            "TARGET_BOARD": submission.board.slug,
            "ELF_DOWNLOAD_URL": download_url,
            "ELF_DOWNLOAD_TOKEN": raw_download_token,
            "ELF_SHA256": submission.sha256,
            "ELF_NAME": submission.original_name,
        }
    ).encode()
    job = quote(settings.JENKINS_SINGLE_ELF_JOB, safe="")
    trigger_url = f"{base}/job/{job}/buildWithParameters"
    try:
        with _jenkins_request(
            trigger_url,
            authorization,
            data=parameters,
            headers={"Content-Type": "application/x-www-form-urlencoded", **crumb_headers},
        ) as response:
            return urljoin(base + "/", response.headers.get("Location", ""))
    except (HTTPError, URLError) as exc:
        detail = ""
        if isinstance(exc, HTTPError):
            detail = exc.read(500).decode(errors="replace").strip()
        raise RuntimeError(f"Jenkins rejected the request: {exc}. {detail}".strip()) from exc


@login_required
def elf_submit(request):
    allowed = settings.PORTAL_SINGLE_ELF_BOARD_SLUGS
    boards = Board.objects.filter(enabled=True, slug__in=allowed).order_by("name")
    if request.method == "POST":
        board = boards.filter(pk=request.POST.get("board")).first()
        upload = request.FILES.get("elf")
        try:
            if board is None:
                raise ValueError("Select a supported board.")
            availability = board.availability()
            if availability["state"] == "offline":
                raise ValueError(
                    f"{board.name} is offline ({availability['reason']}); nothing was uploaded."
                )
            original_name, sha256 = _uploaded_elf_details(upload)
            check_elf_for_board(upload.read(), board, boards)
            upload.seek(0)
            raw_token = secrets.token_urlsafe(32)
            submission = ElfSubmission.objects.create(
                uploaded_by=request.user,
                board=board,
                elf=upload,
                original_name=original_name,
                sha256=sha256,
                size_bytes=upload.size,
                download_token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            )
            try:
                submission.jenkins_queue_url = _trigger_single_elf_job(
                    request, submission, raw_token
                )
                submission.save(update_fields=["jenkins_queue_url", "updated_at"])
            except Exception:
                submission.elf.delete(save=False)
                submission.delete()
                raise
        except (ValueError, ElfCheckError, RuntimeError, HTTPError, URLError, OSError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "ELF queued for execution.")
            return redirect(submission)

    recent = ElfSubmission.objects.filter(uploaded_by=request.user).select_related("board", "run")[
        :20
    ]
    board_rows = []
    client_rules = {}
    for board in boards:
        availability = board.availability()
        board_rows.append({"board": board, **availability})
        rules = board_rules(board)
        if rules:
            client_rules[str(board.id)] = {
                "name": board.name,
                "window": [f"0x{v:08X}" for v in rules["window"]],
                "reserved": [
                    [f"0x{a:08X}", f"0x{b:08X}", label] for a, b, label in rules["reserved"]
                ],
                "state": availability["state"],
            }
    return render(
        request,
        "results/elf_submit.html",
        {
            "boards": boards,
            "board_rows": board_rows,
            "client_rules": client_rules,
            "recent_submissions": recent,
            "max_upload_mib": settings.PORTAL_ELF_UPLOAD_MAX_BYTES // (1024 * 1024),
        },
    )


@login_required
def elf_submission_detail(request, submission_id):
    submission = get_object_or_404(
        ElfSubmission.objects.select_related("board", "run", "run__job"),
        id=submission_id,
    )
    if submission.uploaded_by_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied
    analysis = []
    triage_pending = False
    run = submission.run
    result = run.test_results.first() if run else None
    if result is not None:
        values = {
            value.column.key: value.value
            for value in result.analysis_values.select_related("column")
        }
        for key, label in (("ai_analysis", "AI analysis"), ("triage_root_cause", "Root cause")):
            if values.get(key):
                analysis.append({"label": label, "value": values[key]})
        # Triage is published a few seconds after the result; wait for it briefly.
        triage_pending = (
            result.hardware_status != Status.PASS
            and not (run.metadata or {}).get("triage")
            and timezone.now() - run.updated_at < timedelta(minutes=10)
        )
    return render(
        request,
        "results/elf_submission_detail.html",
        {"submission": submission, "analysis": analysis, "triage_pending": triage_pending},
    )


def elf_download(request, submission_id):
    submission = get_object_or_404(ElfSubmission, id=submission_id)
    supplied = request.headers.get("X-ELF-Download-Token", "")
    supplied_hash = hashlib.sha256(supplied.encode()).hexdigest()
    if not supplied or not secrets.compare_digest(submission.download_token_hash, supplied_hash):
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        handle = submission.elf.open("rb")
    except OSError as exc:
        raise Http404("ELF is no longer available") from exc
    return FileResponse(
        handle,
        as_attachment=True,
        filename=submission.original_name,
        content_type="application/x-elf",
    )


@login_required
def dashboard(request):
    boards = list(Board.objects.filter(enabled=True).prefetch_related("jobs"))
    weekly_runs = TestRun.objects.filter(job__name__endswith="-uart-weekly")
    for board in boards:
        board.latest_run = (
            weekly_runs.filter(job__board=board)
            .select_related("job")
            .order_by("-updated_at", "-id")
            .first()
        )
        board.suite_summaries = []
        if board.latest_run:
            summaries = {
                item["test_case__category"]: item
                for item in board.latest_run.test_results.values("test_case__category").annotate(
                    executed=Count(
                        "id",
                        filter=Q(hardware_status__in=[Status.PASS, Status.FAIL, Status.SKIPPED]),
                    ),
                    passed=Count("id", filter=Q(hardware_status=Status.PASS)),
                    failed=Count("id", filter=Q(hardware_status=Status.FAIL)),
                )
            }
            for category in SUITE_CATEGORIES:
                values = summaries.get(category, {})
                if not values:
                    continue
                executed = values.get("executed", 0)
                passed = values.get("passed", 0)
                board.suite_summaries.append(
                    {
                        "name": category,
                        "executed": executed,
                        "passed": passed,
                        "failed": values.get("failed", 0),
                        "pass_percent": round(passed * 100 / executed, 1) if executed else 0,
                    }
                )
    recent_runs = weekly_runs.select_related("job", "job__board").order_by(
        "-updated_at", "-id"
    )[:12]
    totals = weekly_runs.aggregate(
        runs=Count("id"),
        passing=Count("id", filter=Q(status=Status.PASS)),
        failing=Count("id", filter=Q(status__in=[Status.FAIL, Status.UNSTABLE])),
        running=Count("id", filter=Q(status=Status.RUNNING)),
    )
    return render(
        request,
        "results/dashboard.html",
        {"boards": boards, "recent_runs": recent_runs, "totals": totals},
    )


@login_required
def board_detail(request, slug):
    board = get_object_or_404(Board, slug=slug)
    runs = (
        TestRun.objects.filter(job__board=board, job__name__endswith="-uart-weekly")
        .select_related("job")
        .order_by("-updated_at", "-id")
    )
    status = request.GET.get("status", "").upper()
    if status in Status.values:
        runs = runs.filter(status=status)
    return render(
        request,
        "results/board_detail.html",
        {"board": board, "runs": runs[:100], "selected_status": status},
    )


@login_required
def board_info(request, slug):
    board = get_object_or_404(Board, slug=slug)
    profile = board.profile if isinstance(board.profile, dict) else {}

    def badge(item):
        status = str(item.get("status", "") or "unknown")
        return {**item, "status": status, "badge": PROFILE_STATUSES.get(status, "skipped")}

    sections = [
        {"title": section.get("title", ""), "rows": [badge(row) for row in section.get("rows", [])]}
        for section in profile.get("sections", [])
    ]
    boot_flows = [badge(flow) for flow in profile.get("boot_flows", [])]
    return render(
        request,
        "results/board_info.html",
        {
            "board": board,
            "sections": sections,
            "extensions": profile.get("extensions", {}),
            "boot_flows": boot_flows,
            "notes": profile.get("notes", []),
            "statuses": PROFILE_STATUSES,
        },
    )


def _run_for_job(slug, job_name, build_number):
    return get_object_or_404(
        TestRun.objects.select_related("job", "job__board", "elf_submission"),
        job__board__slug=slug,
        job__name=job_name,
        build_number=build_number,
    )


def _is_single_elf_job(job_name):
    base = settings.JENKINS_SINGLE_ELF_JOB
    return job_name == base or job_name.startswith(f"{base}-")


def _require_run_access(user, run):
    try:
        submission = run.elf_submission
    except ElfSubmission.DoesNotExist:
        submission = None
    if submission is None and not _is_single_elf_job(run.job.name):
        return
    if not user.is_staff and (submission is None or submission.uploaded_by_id != user.id):
        raise PermissionDenied("This single-ELF result belongs to another user")


@login_required
def legacy_run_detail(request, slug, build_number):
    run = (
        TestRun.objects.filter(
            job__board__slug=slug,
            job__name__endswith="-uart-weekly",
            build_number=build_number,
        )
        .select_related("job", "job__board")
        .order_by("-updated_at", "-id")
        .first()
    )
    if run is None:
        raise Http404
    return redirect(run.get_absolute_url())


@login_required
def run_detail(request, slug, job_name, build_number):
    run = _run_for_job(slug, job_name, build_number)
    _require_run_access(request.user, run)
    results = run.test_results.select_related("test_case")
    status = request.GET.get("status", "").upper()
    query = request.GET.get("q", "").strip()
    if status == "NOT_RUN":
        results = results.filter(hardware_status__in=[Status.SKIPPED, Status.UNKNOWN])
    elif status in Status.values:
        results = results.filter(hardware_status=status)
    if query:
        results = results.filter(test_case__name__icontains=query)
    suite_summaries = []
    for category in SUITE_CATEGORIES:
        summary = run.test_results.filter(test_case__category=category).aggregate(
            expected=Count("id"),
            completed=Count(
                "id",
                filter=Q(hardware_status__in=[Status.PASS, Status.FAIL]),
            ),
            passed=Count("id", filter=Q(hardware_status=Status.PASS)),
            failed=Count("id", filter=Q(hardware_status=Status.FAIL)),
        )
        if not summary["expected"]:
            continue
        decided = summary["passed"] + summary["failed"]
        summary.update(
            name=category,
            not_run=summary["expected"] - summary["completed"],
            pass_percent=round(summary["passed"] * 100 / decided, 1) if decided else 0,
        )
        suite_summaries.append(summary)
    return render(
        request,
        "results/run_detail.html",
        {
            "run": run,
            "results": results[:2000],
            "suite_summaries": suite_summaries,
            "selected_status": status,
            "query": query,
        },
    )


@login_required
@require_POST
def delete_run(request, slug, job_name, build_number):
    if not request.user.is_staff:
        raise PermissionDenied("Only portal administrators can delete runs")

    run = _run_for_job(slug, job_name, build_number)
    run_label = f"{run.job.name} build #{run.build_number}"
    board_url = run.job.board.get_absolute_url()
    uart_directory = (
        settings.PORTAL_ARTIFACT_ROOT
        / "uart"
        / _safe_component(run.job.board.slug)
        / _safe_component(run.job.name)
        / str(run.build_number)
    ).resolve()
    artifact_root = settings.PORTAL_ARTIFACT_ROOT.resolve()

    run.delete()

    try:
        uart_directory.relative_to(artifact_root)
    except ValueError:
        pass
    else:
        if uart_directory.is_dir():
            shutil.rmtree(uart_directory)

    messages.success(request, f"{run_label} was removed from the portal.")
    if request.POST.get("return_to") == "dashboard":
        return redirect("dashboard")
    return redirect(board_url)


@login_required
def artifact_download(request, artifact_id):
    artifact = get_object_or_404(
        Artifact.objects.select_related("run__job", "run__elf_submission"), id=artifact_id
    )
    _require_run_access(request.user, artifact.run)
    root = settings.PORTAL_ARTIFACT_ROOT
    candidate = (root / artifact.relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise Http404("Invalid artifact path") from exc
    if not candidate.is_file():
        raise Http404("Artifact is not available")
    return FileResponse(candidate.open("rb"), as_attachment=True, filename=artifact.name)


@login_required
def test_uart_download(request, result_id):
    result = get_object_or_404(
        TestResult.objects.select_related(
            "test_case", "run__job", "run__elf_submission"
        ),
        id=result_id,
    )
    _require_run_access(request.user, result.run)
    if not result.log_path or result.log_path.startswith(("http://", "https://")):
        raise Http404("UART log is not stored by the portal")
    root = settings.PORTAL_ARTIFACT_ROOT
    candidate = (root / result.log_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise Http404("Invalid UART log path") from exc
    if not candidate.is_file():
        raise Http404("UART log is not available")
    return FileResponse(
        candidate.open("rb"),
        as_attachment=False,
        filename=f"{result.test_case.name}-uart.log",
        content_type="text/plain",
    )


def _safe_component(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._") or "unnamed"


def _store_uart_log(board_slug, job_name, build_number, test_name, encoded):
    try:
        compressed = base64.b64decode(encoded, validate=True)
        content = gzip.decompress(compressed)
    except (binascii.Error, gzip.BadGzipFile, OSError, ValueError, TypeError) as exc:
        raise ValueError("invalid UART log encoding") from exc
    if len(content) > 2 * 1024 * 1024:
        raise ValueError("UART log exceeds the 2 MiB per-test limit")
    relative = (
        "uart"
        f"/{_safe_component(board_slug)}"
        f"/{_safe_component(job_name)}"
        f"/{int(build_number)}"
        f"/{_safe_component(test_name)}.log"
    )
    destination = (settings.PORTAL_ARTIFACT_ROOT / relative).resolve()
    destination.relative_to(settings.PORTAL_ARTIFACT_ROOT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return relative


def _authorized(request):
    configured = settings.PORTAL_INGEST_TOKEN
    supplied = request.headers.get("X-Portal-Token", "")
    return bool(configured and supplied and secrets.compare_digest(configured, supplied))


@csrf_exempt
@require_POST
def ingest_board_health(request):
    """Board availability reported by the board-health job on the hardware agent."""
    if not _authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        payload = json.loads(request.body)
        checked_at = str(payload.get("checked_at") or timezone.now().isoformat())
        reports = payload["boards"]
        if not isinstance(reports, list):
            raise TypeError
    except (json.JSONDecodeError, KeyError, TypeError):
        return JsonResponse({"error": "invalid payload"}, status=400)
    updated = []
    for item in reports:
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        board = Board.objects.filter(slug=str(item["slug"])).first()
        if board is None:
            continue
        board.health = {
            "online": bool(item.get("online")),
            "reason": str(item.get("reason", ""))[:300],
            "checks": item.get("checks", {}) if isinstance(item.get("checks"), dict) else {},
            "checked_at": checked_at,
        }
        board.save(update_fields=["health"])
        updated.append(board.slug)
    return JsonResponse({"updated": updated})


@csrf_exempt
@require_POST
def ingest_run(request):
    if not _authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        payload = json.loads(request.body)
        board_data = payload["board"]
        job_data = payload["job"]
        build_number = int(payload["build_number"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"error": "invalid payload"}, status=400)

    with transaction.atomic():
        board, _ = Board.objects.update_or_create(
            slug=board_data["slug"],
            defaults={
                "name": board_data.get("name", board_data["slug"]),
                "core_profile": board_data.get("core_profile", ""),
                "description": board_data.get("description", ""),
            },
        )
        job, _ = JenkinsJob.objects.update_or_create(
            name=job_data["name"],
            defaults={"board": board, "jenkins_url": job_data.get("jenkins_url", "")},
        )
        run_defaults = {
            "status": payload.get("status", Status.UNKNOWN),
            "started_at": (
                parse_datetime(payload["started_at"]) if payload.get("started_at") else None
            ),
            "finished_at": (
                parse_datetime(payload["finished_at"]) if payload.get("finished_at") else None
            ),
            "expected_cases": int(payload.get("expected_cases", 0)),
            "completed_cases": int(payload.get("completed_cases", 0)),
            "passed_cases": int(payload.get("passed_cases", 0)),
            "failed_cases": int(payload.get("failed_cases", 0)),
            "skipped_cases": int(payload.get("skipped_cases", 0)),
            "git_revision": payload.get("git_revision", ""),
            "act_revision": payload.get("act_revision", ""),
            "parameters": payload.get("parameters", {}),
            "metadata": payload.get("metadata", {}),
        }
        run, created = TestRun.objects.update_or_create(
            job=job, build_number=build_number, defaults=run_defaults
        )
        incoming_results = payload.get("results", [])
        incoming_test_names = {
            str(item.get("name", "")) for item in incoming_results if item.get("name")
        }
        run.test_results.exclude(test_case__name__in=incoming_test_names).delete()
        for item in incoming_results:
            test_case, _ = TestCase.objects.update_or_create(
                name=item["name"],
                defaults={
                    "category": item.get("category", ""),
                    "extension": item.get("extension", ""),
                },
            )
            log_path = item.get("log_path", "")
            if item.get("uart_log_gzip_b64"):
                try:
                    log_path = _store_uart_log(
                        board.slug,
                        job.name,
                        build_number,
                        item["name"],
                        item["uart_log_gzip_b64"],
                    )
                except ValueError as exc:
                    return JsonResponse({"error": str(exc)}, status=400)
            TestResult.objects.update_or_create(
                run=run,
                test_case=test_case,
                defaults={
                    "sail_status": item.get("sail_status", Status.UNKNOWN),
                    "spike_status": item.get("spike_status", Status.UNKNOWN),
                    "hardware_status": item.get("hardware_status", Status.UNKNOWN),
                    "duration_seconds": item.get("duration_seconds"),
                    "failure_reason": item.get("failure_reason", ""),
                    "log_path": log_path,
                },
            )
        for item in payload.get("artifacts", []):
            Artifact.objects.update_or_create(
                run=run,
                name=item["name"],
                defaults={
                    "relative_path": item["relative_path"],
                    "external_url": item.get("external_url", ""),
                    "kind": item.get("kind", ""),
                    "size_bytes": int(item.get("size_bytes", 0)),
                    "sha256": item.get("sha256", ""),
                },
            )
        submission_id = payload.get("metadata", {}).get("submission_id")
        if submission_id:
            submission = ElfSubmission.objects.filter(id=submission_id).first()
            if submission is not None:
                submission.run = run
                submission.status = run.status
                submission.download_token_hash = ""
                submission.save(
                    update_fields=["run", "status", "download_token_hash", "updated_at"]
                )
    return JsonResponse(
        {"id": run.id, "url": run.get_absolute_url(), "created": created},
        status=201 if created else 200,
    )
