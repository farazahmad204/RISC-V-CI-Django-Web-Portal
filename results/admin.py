from django.contrib import admin

from .models import (
    AnalysisColumn,
    AnalysisValue,
    Artifact,
    Board,
    BoardRequest,
    ElfSubmission,
    JenkinsJob,
    TestCase,
    TestResult,
    TestRun,
)


@admin.register(ElfSubmission)
class ElfSubmissionAdmin(admin.ModelAdmin):
    list_display = ("original_name", "board", "uploaded_by", "status", "created_at")
    list_filter = ("status", "board")
    search_fields = ("original_name", "sha256", "uploaded_by__username")
    readonly_fields = ("sha256", "size_bytes", "download_token_hash", "created_at", "updated_at")


@admin.register(BoardRequest)
class BoardRequestAdmin(admin.ModelAdmin):
    list_display = ("board_name", "board_id", "requested_by", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("board_name", "board_id", "soc", "requested_by__username")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "core_profile", "enabled")
    list_filter = ("enabled",)
    search_fields = ("name", "slug", "core_profile")


@admin.register(JenkinsJob)
class JenkinsJobAdmin(admin.ModelAdmin):
    list_display = ("name", "board", "enabled")
    list_filter = ("board", "enabled")
    search_fields = ("name",)


@admin.register(TestRun)
class TestRunAdmin(admin.ModelAdmin):
    list_display = (
        "job",
        "build_number",
        "status",
        "completed_cases",
        "expected_cases",
        "updated_at",
    )
    list_filter = ("status", "job__board")
    search_fields = ("job__name", "git_revision", "act_revision")


@admin.register(TestCase)
class TestCaseAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "extension")
    list_filter = ("category", "extension")
    search_fields = ("name",)


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ("test_case", "run", "sail_status", "spike_status", "hardware_status")
    list_filter = ("hardware_status", "sail_status", "spike_status", "run__job__board")
    search_fields = ("test_case__name", "failure_reason")


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ("name", "run", "kind", "size_bytes")
    search_fields = ("name", "relative_path", "external_url")


@admin.register(AnalysisColumn)
class AnalysisColumnAdmin(admin.ModelAdmin):
    list_display = ("name", "run", "position", "created_by", "created_at")
    search_fields = ("name", "run__job__name")


@admin.register(AnalysisValue)
class AnalysisValueAdmin(admin.ModelAdmin):
    list_display = ("column", "test_result", "updated_by", "updated_at")
    search_fields = ("column__name", "test_result__test_case__name", "value")
