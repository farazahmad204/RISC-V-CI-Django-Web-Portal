"""Build run page: run details, test results, logs and the failure-analysis workbook.

One page per run (the run-detail URL; the old workbook URL redirects to it). Sail, Spike and
hardware results are what Jenkins measured and are never edited here.
Analysis columns (the Verdict, the triage columns and any column a person adds) are
stored separately in AnalysisColumn/AnalysisValue.
"""

import json
from collections import Counter
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import (
    TRIAGE_COLUMNS,
    VERDICT_CHOICES,
    VERDICT_KEY,
    AnalysisColumn,
    AnalysisValue,
    Status,
    TestResult,
    TestRun,
    jenkins_link,
)
from .views import SUITE_CATEGORIES, _authorized, _require_run_access, _run_for_job
from .xlsx import build_xlsx

MAX_VALUE_CHARS = 4000


def _can_manage_analysis(user):
    return user.is_staff or user.has_perm("results.manage_failure_analysis")


def _column_for_key(run, key, name):
    """Return the run's column with this key, adopting a same-named column a person added."""
    column = run.analysis_columns.filter(key=key).first()
    if column:
        return column, False
    column = run.analysis_columns.filter(name__iexact=name).first()
    if column:
        column.key = key
        column.save(update_fields=["key"])
        return column, False
    position = (run.analysis_columns.aggregate(value=Max("position"))["value"] or 0) + 1
    return AnalysisColumn.objects.create(run=run, key=key, name=name, position=position), True


def _place_after_verdict(run, new_columns):
    """Renumber positions: Verdict first, then new_columns, then everything else in order."""
    ordered = list(run.analysis_columns.order_by("position", "id"))
    new_ids = {column.id for column in new_columns}
    verdict = [column for column in ordered if column.key == VERDICT_KEY]
    rest = [column for column in ordered if column.id not in new_ids and column.key != VERDICT_KEY]
    for position, column in enumerate(verdict + list(new_columns) + rest):
        if column.position != position:
            column.position = position
            column.save(update_fields=["position"])


def ensure_verdict_column(run):
    column, created = _column_for_key(run, VERDICT_KEY, "Verdict")
    if created:
        _place_after_verdict(run, [])
    return column


# Hardware-status filter of the test table: value -> label.
STATUS_FILTERS = {
    "": "All statuses",
    "FAIL": "Failed",
    "PASS": "Passed",
    "NOT_RUN": "Not run",
}


def _page_query(suite, status, query):
    params = {}
    if suite != "All":
        params["suite"] = suite
    if status:
        params["status"] = status
    if query:
        params["q"] = query
    return f"?{urlencode(params)}" if params else ""


def _selection(request):
    """Suite tab, status filter and search text, from the query string or a posted form."""
    data = request.POST if request.method == "POST" else request.GET
    suite = data.get("suite") or "All"
    if suite not in ("All", *SUITE_CATEGORIES):
        suite = "All"
    status = str(data.get("status", "")).upper()
    if data.get("show") == "failed":  # links to the old workbook page
        status = "FAIL"
    if status not in STATUS_FILTERS:
        status = ""
    return suite, status, str(data.get("q", "")).strip()[:100]


def _matches_status(result, status):
    if status == "NOT_RUN":
        return result.hardware_status not in (Status.PASS, Status.FAIL)
    return not status or result.hardware_status == status


def _suite_summary(results):
    counts = Counter(result.hardware_status for result in results)
    executed = counts[Status.PASS] + counts[Status.FAIL]
    return {
        "total": len(results),
        "executed": executed,
        "passed": counts[Status.PASS],
        "failed": counts[Status.FAIL],
        "not_run": len(results) - executed,
        "pass_percent": round(counts[Status.PASS] * 100 / executed, 1) if executed else 0,
    }


def _matrix(results, columns):
    rows = []
    for result in results:
        stored = {value.column_id: value for value in result.analysis_values.all()}
        cells = []
        for column in columns:
            value = stored.get(column.id)
            cells.append(
                {
                    "column": column,
                    "value": value.value if value else "",
                    "source": value.source if value else "",
                    "updated_by": value.updated_by if value else None,
                    "updated_at": value.updated_at if value else None,
                }
            )
        rows.append({"result": result, "cells": cells})
    return rows


def _results(run):
    return list(
        run.test_results.select_related("test_case").prefetch_related("analysis_values__updated_by")
    )


@login_required
def run_page(request, slug, job_name, build_number):
    run = _run_for_job(slug, job_name, build_number)
    _require_run_access(request.user, run)
    selected_suite, selected_status, query = _selection(request)
    ensure_verdict_column(run)

    results = _results(run)
    present = [
        suite for suite in SUITE_CATEGORIES if any(r.test_case.category == suite for r in results)
    ]
    if len(present) < 2:
        present = []  # one suite: its card and tab would repeat "All"
    suites = ("All", *present)
    if selected_suite not in suites:
        selected_suite = "All"
    summary_rows = [{"name": "All", **_suite_summary(results)}] + [
        {"name": suite, **_suite_summary([r for r in results if r.test_case.category == suite])}
        for suite in present
    ]
    shown = [
        r
        for r in results
        if (selected_suite == "All" or r.test_case.category == selected_suite)
        and _matches_status(r, selected_status)
        and (not query or query.lower() in r.test_case.name.lower())
    ]

    columns = list(run.analysis_columns.order_by("position", "id"))
    metadata = run.metadata or {}
    triage = metadata.get("triage") or {}
    return render(
        request,
        "results/run_detail.html",
        {
            "run": run,
            "matrix_rows": _matrix(shown, columns),
            "shown_count": len(shown),
            "total_count": len(results),
            "columns": columns,
            "can_edit": _can_manage_analysis(request.user),
            "selected_suite": selected_suite,
            "selected_status": selected_status,
            "query": query,
            "status_filters": STATUS_FILTERS.items(),
            "suites": suites,
            "summary_rows": summary_rows,
            "artifacts": list(run.artifacts.all()),
            "build_url": jenkins_link(str(metadata.get("build_url") or "")),
            "verdict_choices": list(VERDICT_CHOICES),
            "triage_published_at": parse_datetime(str(triage.get("published_at", ""))),
            "triage_failures": triage.get("failures"),
            "triage_ai_model": triage.get("ai_model", ""),
        },
    )


@login_required
def run_workbook(request, slug, job_name, build_number):
    """The workbook is part of the run page now; keep old links working."""
    run = _run_for_job(slug, job_name, build_number)
    _require_run_access(request.user, run)
    suite, status, query = _selection(request)
    return redirect(run.get_absolute_url() + _page_query(suite, status, query))


def _workbook_redirect(request, run):
    suite, status, query = _selection(request)
    return redirect(run.get_absolute_url() + _page_query(suite, status, query))


def _editable_run(request, slug, job_name, build_number):
    if not _can_manage_analysis(request.user):
        raise PermissionDenied("You cannot edit failure analysis")
    run = _run_for_job(slug, job_name, build_number)
    _require_run_access(request.user, run)
    return run


def _clean_column_name(raw):
    return " ".join(str(raw or "").split())


@login_required
@require_POST
def add_analysis_column(request, slug, job_name, build_number):
    run = _editable_run(request, slug, job_name, build_number)
    name = _clean_column_name(request.POST.get("name"))
    if not name or len(name) > 80:
        messages.error(request, "Column name must contain 1–80 characters.")
    elif run.analysis_columns.filter(name__iexact=name).exists():
        messages.error(request, f'Analysis column "{name}" already exists.')
    else:
        position = (run.analysis_columns.aggregate(value=Max("position"))["value"] or 0) + 1
        AnalysisColumn.objects.create(
            run=run, name=name, position=position, created_by=request.user
        )
        messages.success(request, f'Column "{name}" was added. Click a cell to fill it in.')
    return _workbook_redirect(request, run)


@login_required
@require_POST
def update_analysis_column(request, slug, job_name, build_number, column_id):
    """Rename, move or delete one analysis column."""
    run = _editable_run(request, slug, job_name, build_number)
    column = get_object_or_404(AnalysisColumn, id=column_id, run=run)
    action = request.POST.get("action", "")
    if action == "rename":
        name = _clean_column_name(request.POST.get("name"))
        if not name or len(name) > 80:
            messages.error(request, "Column name must contain 1–80 characters.")
        elif run.analysis_columns.filter(name__iexact=name).exclude(id=column.id).exists():
            messages.error(request, f'Analysis column "{name}" already exists.')
        else:
            column.name = name
            column.save(update_fields=["name"])
            messages.success(request, f'Column renamed to "{name}".')
    elif action in {"left", "right"}:
        ordered = list(run.analysis_columns.order_by("position", "id"))
        index = ordered.index(column)
        target = index - 1 if action == "left" else index + 1
        if 0 <= target < len(ordered):
            ordered[index], ordered[target] = ordered[target], ordered[index]
            with transaction.atomic():
                for position, item in enumerate(ordered):
                    if item.position != position:
                        item.position = position
                        item.save(update_fields=["position"])
    elif action == "delete":
        if column.key == VERDICT_KEY:
            messages.error(request, "The Verdict column cannot be deleted.")
        else:
            column.delete()
            messages.success(request, f'Column "{column.name}" and its values were deleted.')
    else:
        messages.error(request, "Unknown column action.")
    return _workbook_redirect(request, run)


@login_required
@require_POST
def save_analysis_cell(request, slug, job_name, build_number):
    """Save one analysis cell; answers JSON for the workbook's in-place editor."""
    if not _can_manage_analysis(request.user):
        return JsonResponse({"error": "You cannot edit failure analysis."}, status=403)
    run = _run_for_job(slug, job_name, build_number)
    _require_run_access(request.user, run)
    try:
        column = run.analysis_columns.get(id=int(request.POST.get("column", "")))
        result = run.test_results.get(id=int(request.POST.get("result", "")))
    except (ValueError, AnalysisColumn.DoesNotExist, TestResult.DoesNotExist):
        return JsonResponse({"error": "Unknown cell."}, status=404)
    value = str(request.POST.get("value", "")).strip()
    if len(value) > MAX_VALUE_CHARS:
        return JsonResponse({"error": f"Use at most {MAX_VALUE_CHARS} characters."}, status=400)
    if column.key == VERDICT_KEY and value and value not in VERDICT_CHOICES:
        return JsonResponse({"error": "Choose one of the listed verdicts."}, status=400)
    if value:
        AnalysisValue.objects.update_or_create(
            column=column,
            test_result=result,
            defaults={"value": value, "source": "person", "updated_by": request.user},
        )
    else:
        AnalysisValue.objects.filter(column=column, test_result=result).delete()
    return JsonResponse(
        {
            "value": value,
            "updated_by": request.user.get_username(),
            "verdict_class": slugify(value) if column.key == VERDICT_KEY else "",
        }
    )


def _status_cell(status):
    style = {"PASS": "pass", "FAIL": "fail"}.get(status)
    return (status, style) if style else status


@login_required
def export_workbook_xlsx(request, slug, job_name, build_number):
    run = _run_for_job(slug, job_name, build_number)
    _require_run_access(request.user, run)
    results = _results(run)
    columns = list(run.analysis_columns.order_by("position", "id"))
    board = run.job.board
    header = [
        "Test Name",
        "Suite",
        "Sail",
        "Spike",
        f"{board.name} (hardware)",
        *[column.name for column in columns],
    ]
    widths = [40, 15, 10, 10, 14]
    for column in columns:
        widths.append(24 if column.key == VERDICT_KEY else 60 if column.key else 32)

    def table(selected):
        rows = [[(name, "header") for name in header]]
        for row in _matrix(selected, columns):
            result = row["result"]
            rows.append(
                [
                    result.test_case.name,
                    result.test_case.category,
                    _status_cell(result.sail_status),
                    _status_cell(result.spike_status),
                    _status_cell(result.hardware_status),
                    *[cell["value"] for cell in row["cells"]],
                ]
            )
        return rows

    summary = [
        ["Board", board.name],
        ["Job", run.job.name],
        ["Build", run.build_number],
        ["Status", run.status],
        ["Exported", timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")],
        [],
        [
            (name, "header")
            for name in ("Suite", "Total", "Executed", "Passed", "Failed", "Not run", "Pass %")
        ],
    ]
    sheets = []
    for suite in ("All", *SUITE_CATEGORIES):
        in_suite = (
            results if suite == "All" else [r for r in results if r.test_case.category == suite]
        )
        if suite != "All" and not in_suite:
            continue
        counts = _suite_summary(in_suite)
        summary.append(
            [
                suite,
                counts["total"],
                counts["executed"],
                counts["passed"],
                counts["failed"],
                counts["not_run"],
                counts["pass_percent"],
            ]
        )
        title = "Test Status" if suite == "All" else f"{suite} Tests"
        sheets.append((title, table(in_suite), {"widths": widths, "freeze": True}))
    failed = [r for r in results if r.hardware_status == Status.FAIL]
    if failed:
        sheets.insert(1, ("Failures", table(failed), {"widths": widths, "freeze": True}))
    sheets.insert(0, ("Summary", summary, {"widths": [16, 22, 10, 10, 10, 10, 10]}))

    response = HttpResponse(
        build_xlsx(sheets),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    filename = f"{slugify(run.job.name)}-{run.build_number}-triage-report.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _evidence_text(evidence):
    if not isinstance(evidence, dict):
        return str(evidence or "")
    lines = []
    for key, value in evidence.items():
        if key == "evidence_lines" or value in (None, "", [], {}):
            continue  # evidence_lines is the raw log excerpt; the UART log link has it all
        if isinstance(value, list):
            value = "; ".join(
                json.dumps(item) if isinstance(item, dict) else str(item) for item in value[:5]
            )
        elif isinstance(value, dict):
            value = json.dumps(value)
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


@csrf_exempt
@require_POST
def ingest_triage(request):
    """Advisory triage from a weekly job; fills the triage columns of an existing run.

    Values a person has edited are never overwritten. Results are not touched.
    """
    if not _authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        payload = json.loads(request.body)
        job_name = str(payload["job_name"])
        build_number = int(payload["build_number"])
        items = payload.get("results", [])
        if not isinstance(items, list):
            raise TypeError
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"error": "invalid payload"}, status=400)
    run = (
        TestRun.objects.select_related("job", "job__board")
        .filter(job__name=job_name, build_number=build_number)
        .first()
    )
    if run is None:
        return JsonResponse({"error": "run not found; publish the run first"}, status=404)

    items = [item for item in items if isinstance(item, dict) and item.get("name")]
    has_ai = any(str(item.get("ai_analysis") or "").strip() for item in items)
    updated = kept = 0
    unknown = []
    with transaction.atomic():
        verdict = ensure_verdict_column(run)
        columns, created = {}, []
        for key, name in TRIAGE_COLUMNS if items else ():  # no failures: no empty columns
            if key == "ai_analysis" and not has_ai:
                continue
            columns[key], is_new = _column_for_key(run, key, name)
            if is_new:
                created.append(columns[key])
        if created:
            _place_after_verdict(run, created)
        results = {
            result.test_case.name: result for result in run.test_results.select_related("test_case")
        }
        for item in items:
            result = results.get(str(item["name"]))
            if result is None:
                unknown.append(str(item["name"]))
                continue
            values = {
                "triage_root_cause": item.get("triage_explanation", ""),
                "triage_owner": item.get("triage_owner", ""),
                "triage_evidence": _evidence_text(item.get("triage_evidence")),
                "ai_analysis": item.get("ai_analysis", ""),
            }
            targets = [(columns[key], values[key]) for key in columns]
            if result.hardware_status == Status.FAIL:
                targets.append((verdict, "Needs investigation"))
            for column, text in targets:
                text = str(text or "").strip()[:MAX_VALUE_CHARS]
                existing = AnalysisValue.objects.filter(column=column, test_result=result).first()
                if existing and existing.source != "triage":
                    kept += 1
                    continue
                if not text:
                    if existing:
                        existing.delete()
                    continue
                AnalysisValue.objects.update_or_create(
                    column=column,
                    test_result=result,
                    defaults={"value": text, "source": "triage", "updated_by": None},
                )
                updated += 1
        metadata = dict(run.metadata or {})
        metadata["triage"] = {
            "published_at": timezone.now().isoformat(),
            "failures": len(items) - len(unknown),
            "ai_model": next((str(i.get("ai_model")) for i in items if i.get("ai_model")), ""),
        }
        run.metadata = metadata
        run.save(update_fields=["metadata"])
    return JsonResponse(
        {
            "url": run.get_absolute_url(),
            "updated_cells": updated,
            "kept_person_edits": kept,
            "unknown_tests": unknown[:50],
        }
    )
