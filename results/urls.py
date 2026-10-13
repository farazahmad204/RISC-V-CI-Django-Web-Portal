from django.urls import path

from . import board_requests, views, workbook

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("run-elf/", views.elf_submit, name="elf-submit"),
    path("run-elf/history/", views.elf_history, name="elf-history"),
    path(
        "run-elf/<uuid:submission_id>/",
        views.elf_submission_detail,
        name="elf-submission-detail",
    ),
    path(
        "run-elf/<uuid:submission_id>/delete/",
        views.delete_elf_submission,
        name="elf-submission-delete",
    ),
    path("onboard/", board_requests.board_request_new, name="board-request-new"),
    path("onboard/<uuid:request_id>/", board_requests.board_request_detail, name="board-request-detail"),
    path("onboard/<uuid:request_id>/delete/", board_requests.board_request_delete, name="board-request-delete"),
    path("boards/<slug:slug>/", views.board_detail, name="board-detail"),
    path("boards/<slug:slug>/info/", views.board_info, name="board-info"),
    path(
        "boards/<slug:slug>/jobs/<str:job_name>/runs/<int:build_number>/",
        workbook.run_page,
        name="run-detail",
    ),
    path(
        "boards/<slug:slug>/jobs/<str:job_name>/runs/<int:build_number>/workbook/",
        workbook.run_workbook,
        name="run-workbook",
    ),
    path(
        "boards/<slug:slug>/jobs/<str:job_name>/runs/<int:build_number>/triage/",
        workbook.triage_report,
        name="run-triage-report",
    ),
    path(
        "boards/<slug:slug>/jobs/<str:job_name>/runs/<int:build_number>/workbook/export.xlsx",
        workbook.export_workbook_xlsx,
        name="run-workbook-xlsx",
    ),
    path(
        "boards/<slug:slug>/jobs/<str:job_name>/runs/<int:build_number>/workbook/cells/",
        workbook.save_analysis_cell,
        name="analysis-cell-save",
    ),
    path(
        "boards/<slug:slug>/jobs/<str:job_name>/runs/<int:build_number>/workbook/columns/add/",
        workbook.add_analysis_column,
        name="analysis-column-add",
    ),
    path(
        "boards/<slug:slug>/jobs/<str:job_name>/runs/<int:build_number>/workbook/columns/<int:column_id>/",
        workbook.update_analysis_column,
        name="analysis-column-update",
    ),
    path(
        "boards/<slug:slug>/jobs/<str:job_name>/runs/<int:build_number>/delete/",
        views.delete_run,
        name="run-delete",
    ),
    path(
        "boards/<slug:slug>/runs/<int:build_number>/",
        views.legacy_run_detail,
        name="legacy-run-detail",
    ),
    path(
        "artifacts/<int:artifact_id>/download/",
        views.artifact_download,
        name="artifact-download",
    ),
    path(
        "results/<int:result_id>/uart/",
        views.test_uart_download,
        name="test-uart-download",
    ),
    path("api/v1/runs/", views.ingest_run, name="api-ingest-run"),
    path("api/v1/triage/", workbook.ingest_triage, name="api-ingest-triage"),
    path("api/v1/triage/feedback/", workbook.triage_feedback, name="api-triage-feedback"),
    path("api/v1/boards/health/", views.ingest_board_health, name="api-board-health"),
    path(
        "api/v1/elf/<uuid:submission_id>/status/",
        views.elf_submission_status,
        name="api-elf-status",
    ),
    path(
        "api/v1/board-requests/<uuid:request_id>/status/",
        board_requests.board_request_status,
        name="api-board-request-status",
    ),
    path(
        "api/v1/board-requests/<uuid:request_id>/result/",
        board_requests.board_request_result,
        name="api-board-request-result",
    ),
    path(
        "api/v1/elf/<uuid:submission_id>/download/",
        views.elf_download,
        name="elf-download",
    ),
]
