"""New board onboarding (admin only): a board name and model in, every runner requirement out.

The form queues the riscv-board-pipeline Jenkins job, which asks Codex to find the board's
official documents, extracts the runner requirements from them (each value with its source),
decides the memory layout and builds the runner. The job reports RUNNING when it starts and
publishes its result.json when it ends; the request page shows it.
"""

import json
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import BoardRequest, Status
from .views import _authorized, _external_url, _trigger_jenkins_job

BOARD_ID = re.compile(r"^[a-z0-9][a-z0-9_]{1,62}$")
ISA = re.compile(r"^rv(32|64)[a-z][a-z0-9_]*$")
FACT_ID = re.compile(r"^[a-z]+\.[a-z0-9_]+$")
# Requirement groups in the order a firmware port needs them.
GROUPS = [
    ("boot", "Boot chain and image"),
    ("harts", "Harts"),
    ("uart", "Console UART"),
    ("timer", "Timer (CLINT / ACLINT)"),
    ("interrupt", "Interrupts (PLIC)"),
    ("memory", "Memory map"),
    ("platform", "Platform"),
    ("identity", "ISA and spec versions"),
    ("bench", "Lab setup"),
    ("act", "ACT configuration"),
]
REQUIREMENT_BADGE = {"resolved": "pass", "decided": "pass", "default": "unstable",
                     "conflict": "fail", "open": "skipped", "choice_open": "skipped"}
STEP_BADGE = {"ok": "pass", "warn": "unstable", "incomplete": "unstable", "blocked": "fail",
              "skipped": "skipped", "not_run": "skipped"}


def _admin(user):
    if not user.is_staff:
        raise PermissionDenied("Board onboarding is for administrators.")


def parse_known_facts(text):
    """'boot.medium: SD card' lines -> {id: value}; requirement ids from runner-requirements.yaml."""
    facts, errors = {}, []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not FACT_ID.match(key) or not value:
            errors.append(f"line {number}: expected 'requirement.id: value'")
            continue
        facts[key] = value
    return facts, errors


def board_request_yaml(board_request):
    """The pipeline's board request (JSON is YAML). Only facts, never agent paths."""
    request = {
        "board_id": board_request.board_id,
        "board_name": board_request.board_name,
        "soc": board_request.soc,
        "hint": board_request.hint,
        "find_docs": True,
        "docs_quality": "none",
        "model": {"enabled": True, "backend": "codex"},
        "package": False,
    }
    if board_request.isa:
        request["isa"] = board_request.isa
    if board_request.runner_hart is not None:
        request["runner_hart"] = board_request.runner_hart
    if board_request.known_facts:
        request["user_answers"] = board_request.known_facts
    return json.dumps(request, indent=2)


@login_required
def board_request_new(request):
    _admin(request.user)
    form = {}
    if request.method == "POST":
        form = {key: request.POST.get(key, "").strip() for key in
                ("board_name", "board_id", "soc", "isa", "runner_hart", "hint", "known_facts")}
        errors = []
        if not form["board_name"]:
            errors.append("Board name is required.")
        if not BOARD_ID.match(form["board_id"]):
            errors.append("Board id: lower-case letters, digits and '_', 2-63 characters (e.g. vendor_soc).")
        isa = form["isa"].lower()
        if isa and not ISA.match(isa):
            errors.append("ISA string: e.g. rv64imafdc_zicsr_zifencei, or leave it empty.")
        hart = None
        if form["runner_hart"]:
            if not form["runner_hart"].isdigit():
                errors.append("Runner hart: a hart number, or leave it empty.")
            else:
                hart = int(form["runner_hart"])
        facts, fact_errors = parse_known_facts(form["known_facts"])
        errors += [f"Known facts, {e}" for e in fact_errors]
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            board_request = BoardRequest.objects.create(
                requested_by=request.user, board_name=form["board_name"][:120],
                board_id=form["board_id"], soc=form["soc"][:120], isa=isa, runner_hart=hart,
                hint=form["hint"][:4000], known_facts=facts,
            )
            try:
                board_request.jenkins_queue_url = _trigger_jenkins_job(
                    settings.JENKINS_BOARD_PIPELINE_JOB,
                    {
                        "REQUEST_ID": str(board_request.id),
                        "BOARD_REQUEST_YAML": board_request_yaml(board_request),
                        "PORTAL_USER": request.user.get_username(),
                        "ALLOW_MODEL_CALLS": "true",
                        "PUBLISH_RESULT": "true",
                    },
                )
            except Exception as exc:
                board_request.status = Status.ERROR
                board_request.status_reason = str(exc)[:500]
                board_request.save(update_fields=["status", "status_reason", "updated_at"])
                messages.error(request, f"The request was saved but Jenkins did not accept it: {exc}")
            else:
                board_request.save(update_fields=["jenkins_queue_url", "updated_at"])
                messages.success(request, "Board request queued. The search and analysis take a few minutes.")
            return redirect(board_request)
    return render(request, "results/board_request_new.html", {
        "form": form,
        "board_requests": BoardRequest.objects.select_related("requested_by")[:50],
    })


def _display(value):
    """Addresses and sizes as hex (YAML gives them as integers), structures as compact JSON."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return f"0x{value:x}" if value >= 0x1000 else value
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return value


def _summary(result):
    """What the request page shows from result.json."""
    if not result:
        return {}
    requirements = result.get("requirements") or []
    by_group = {}
    for row in requirements:
        row = {**row, "badge": REQUIREMENT_BADGE.get(row.get("status"), "skipped"),
               "build_input": "offline_build" in (row.get("needed_for") or [])}
        row["value"] = _display(row.get("value"))
        by_group.setdefault(row.get("group"), []).append(row)
    groups = [{"key": key, "title": title, "rows": by_group.pop(key)}
              for key, title in GROUPS if key in by_group]
    groups += [{"key": key, "title": key, "rows": rows} for key, rows in by_group.items()]
    for group in groups:
        group["answered"] = sum(1 for r in group["rows"] if r["badge"] in ("pass", "unstable"))
    build_inputs = [r for g in groups for r in g["rows"] if r["build_input"]]
    return {
        "groups": groups,
        "answered": sum(g["answered"] for g in groups),
        "total": len(requirements),
        "build_answered": sum(1 for r in build_inputs if r["badge"] in ("pass", "unstable")),
        "build_total": len(build_inputs),
        "steps": [{"name": name, "status": info.get("status", "not_run"),
                   "badge": STEP_BADGE.get(info.get("status"), "skipped"), "seconds": info.get("seconds")}
                  for name, info in (result.get("steps") or {}).items()],
        "documents": result.get("documents_found") or [],
        "needs_person": result.get("needs_person") or [],
        "runner": result.get("runner") or {},
        "completion_state": result.get("completion_state"),
        "stop_reason": result.get("stop_reason"),
    }


@login_required
def board_request_detail(request, request_id):
    _admin(request.user)
    board_request = get_object_or_404(BoardRequest, id=request_id)
    build = board_request.jenkins_build_url.rstrip("/")
    artifacts = f"{build}/artifact/board-pipeline-export" if build else ""
    return render(request, "results/board_request_detail.html", {
        "board_request": board_request,
        "summary": _summary(board_request.result),
        "artifacts": artifacts,
        "pending": board_request.status in (Status.QUEUED, Status.RUNNING),
    })


REPORTABLE = {Status.RUNNING, Status.ERROR, Status.ABORTED}


def _load(request):
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@csrf_exempt
@require_POST
def board_request_status(request, request_id):
    """The job reports RUNNING with its build URL, or ERROR/ABORTED when it ends without a result."""
    if not _authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    board_request = BoardRequest.objects.filter(id=request_id).first()
    if board_request is None:
        return JsonResponse({"error": "request not found"}, status=404)
    payload = _load(request)
    if payload is None or payload.get("status") not in REPORTABLE:
        return JsonResponse({"error": f"status must be one of {sorted(REPORTABLE)}"}, status=400)
    build_url = str(payload.get("build_url", ""))[:1000]
    if build_url.startswith(("http://", "https://")):
        board_request.jenkins_build_url = build_url
    if board_request.result is None:
        board_request.status = payload["status"]
        board_request.status_reason = str(payload.get("reason", ""))[:500]
    board_request.save()
    return JsonResponse({"status": board_request.status})


@csrf_exempt
@require_POST
def board_request_result(request, request_id):
    """The job's result.json (plus build_url). Each publish replaces the previous one."""
    if not _authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    board_request = BoardRequest.objects.filter(id=request_id).first()
    if board_request is None:
        return JsonResponse({"error": "request not found"}, status=404)
    payload = _load(request)
    if payload is None or not isinstance(payload.get("requirements", []), list):
        return JsonResponse({"error": "invalid result"}, status=400)
    build_url = str(payload.get("build_url", ""))[:1000]
    if build_url.startswith(("http://", "https://")):
        board_request.jenkins_build_url = build_url
    board_request.result = payload
    state = payload.get("completion_state")
    board_request.status = Status.PASS if state == "offline_prepared" else Status.UNSTABLE
    board_request.status_reason = str(payload.get("stop_reason") or "")[:500]
    board_request.save()
    return JsonResponse({"status": board_request.status})
