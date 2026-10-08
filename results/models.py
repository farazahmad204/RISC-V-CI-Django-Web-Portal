import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse


class Status(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    PASS = "PASS", "Pass"
    FAIL = "FAIL", "Fail"
    UNSTABLE = "UNSTABLE", "Unstable"
    SKIPPED = "SKIPPED", "Skipped"
    ABORTED = "ABORTED", "Aborted"
    UNKNOWN = "UNKNOWN", "Unknown"


class Board(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    core_profile = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    # Board info page content: {"sections": [{"title", "rows": [{"label", "value", "status",
    # "source"}]}], "extensions": {"source", "items": [[name, version]]}, "boot_flows": [{"name",
    # "status", "source", "steps": [...]}], "notes": [...]}. status is one of
    # PROFILE_STATUSES. Edited by staff in the admin. "elf_load_rules" ({"window": [start, end],
    # "reserved": [[start, end, label]]}) is what Run ELF accepts for this board.
    profile = models.JSONField(default=dict, blank=True)
    # Last report of the board-health job on the hardware agent:
    # {"online": bool, "reason": str, "checks": {...}, "checked_at": ISO-8601}.
    health = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("board-detail", kwargs={"slug": self.slug})

    def availability(self, stale_after_minutes=30):
        """Online/offline for Run ELF; "unknown" when no recent health report exists."""
        import datetime

        from django.utils import timezone
        from django.utils.dateparse import parse_datetime

        health = self.health if isinstance(self.health, dict) else {}
        checked = parse_datetime(str(health.get("checked_at", ""))) if health else None
        if checked is None:
            return {"state": "unknown", "reason": "No health report yet", "checked_at": None}
        if timezone.is_naive(checked):
            checked = timezone.make_aware(checked, datetime.timezone.utc)
        if timezone.now() - checked > datetime.timedelta(minutes=stale_after_minutes):
            return {"state": "unknown", "reason": "No recent health report", "checked_at": checked}
        state = "online" if health.get("online") else "offline"
        return {"state": state, "reason": str(health.get("reason", "")), "checked_at": checked}


# Evidence status of a board profile value, mapped to an existing badge style.
PROFILE_STATUSES = {
    "confirmed": "pass",  # vendor documentation or measured on the board
    "configured": "running",  # what CI declares and tests, not a vendor claim
    "deviation": "unstable",  # CI deliberately declares something the board does not claim
    "unverified": "skipped",  # summary not yet checked by CI
    "unknown": "skipped",
}


def elf_upload_path(instance, filename):
    return f"elf-uploads/{instance.id}/{filename}"


class ElfSubmission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="elf_submissions",
    )
    board = models.ForeignKey(Board, on_delete=models.PROTECT, related_name="elf_submissions")
    elf = models.FileField(upload_to=elf_upload_path)
    original_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    download_token_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    jenkins_queue_url = models.URLField(max_length=1000, blank=True)
    run = models.OneToOneField(
        "TestRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="elf_submission",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_name} on {self.board}"

    def get_absolute_url(self):
        return reverse("elf-submission-detail", kwargs={"submission_id": self.id})


class JenkinsJob(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="jobs")
    name = models.CharField(max_length=160, unique=True)
    jenkins_url = models.URLField(blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TestRun(models.Model):
    job = models.ForeignKey(JenkinsJob, on_delete=models.CASCADE, related_name="runs")
    build_number = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNKNOWN)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expected_cases = models.PositiveIntegerField(default=0)
    completed_cases = models.PositiveIntegerField(default=0)
    passed_cases = models.PositiveIntegerField(default=0)
    failed_cases = models.PositiveIntegerField(default=0)
    skipped_cases = models.PositiveIntegerField(default=0)
    git_revision = models.CharField(max_length=64, blank=True)
    act_revision = models.CharField(max_length=64, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-build_number"]
        constraints = [
            models.UniqueConstraint(fields=["job", "build_number"], name="unique_job_build")
        ]

    def __str__(self):
        return f"{self.job.name} #{self.build_number}"

    def get_absolute_url(self):
        return reverse(
            "run-detail",
            kwargs={
                "slug": self.job.board.slug,
                "job_name": self.job.name,
                "build_number": self.build_number,
            },
        )

    @property
    def progress_percent(self):
        if not self.expected_cases:
            return 0
        return min(100, round(self.completed_cases * 100 / self.expected_cases))

    @property
    def passed_percent_of_total(self):
        if not self.expected_cases:
            return 0
        return min(100, round(self.passed_cases * 100 / self.expected_cases, 1))

    @property
    def pass_percent(self):
        decided = self.passed_cases + self.failed_cases
        return round(self.passed_cases * 100 / decided, 1) if decided else 0


class TestCase(models.Model):
    name = models.CharField(max_length=300, unique=True)
    category = models.CharField(max_length=80, blank=True, db_index=True)
    extension = models.CharField(max_length=80, blank=True, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TestResult(models.Model):
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="test_results")
    test_case = models.ForeignKey(TestCase, on_delete=models.CASCADE, related_name="results")
    sail_status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNKNOWN)
    spike_status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNKNOWN)
    hardware_status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.UNKNOWN,
    )
    duration_seconds = models.FloatField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    log_path = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["test_case__name"]
        constraints = [
            models.UniqueConstraint(fields=["run", "test_case"], name="unique_result_per_run")
        ]

    def __str__(self):
        return f"{self.run}: {self.test_case.name}"

    @property
    def log_url(self):
        if not self.log_path:
            return ""
        if self.log_path.startswith(("http://", "https://")):
            return self.log_path
        return reverse("test-uart-download", args=[self.id])


class Artifact(models.Model):
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="artifacts")
    name = models.CharField(max_length=200)
    relative_path = models.CharField(max_length=500)
    external_url = models.URLField(max_length=1000, blank=True)
    kind = models.CharField(max_length=40, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["run", "name"], name="unique_artifact_name_per_run")
        ]

    def __str__(self):
        return self.name


# Workbook columns the portal creates itself, keyed by AnalysisColumn.key. People can rename,
# move or (except the verdict) delete them; triage re-publishes find them by key.
VERDICT_KEY = "verdict"
VERDICT_CHOICES = (
    "Needs investigation",
    "Confirmed hardware bug",
    "Known deviation",
    "Test or ACT issue",
    "Reference model issue",
    "Runner or CI issue",
    "Waived",
)
TRIAGE_COLUMNS = (
    ("triage_root_cause", "Root cause (triage)"),
    ("triage_owner", "Owner (triage)"),
    ("triage_evidence", "Evidence (triage)"),
    ("ai_analysis", "AI analysis"),
)


class AnalysisColumn(models.Model):
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="analysis_columns")
    name = models.CharField(max_length=80)
    position = models.PositiveIntegerField(default=0)
    # "" for a column a person added; VERDICT_KEY or a TRIAGE_COLUMNS key otherwise.
    key = models.CharField(max_length=40, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_analysis_columns",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["run", "name"], name="unique_analysis_column_per_run"),
            models.UniqueConstraint(
                fields=["run", "key"],
                condition=~models.Q(key=""),
                name="unique_analysis_column_key_per_run",
            ),
        ]
        permissions = [
            ("manage_failure_analysis", "Can manage build failure-analysis columns"),
        ]

    def __str__(self):
        return f"{self.run}: {self.name}"

    @property
    def is_verdict(self):
        return self.key == VERDICT_KEY


class AnalysisValue(models.Model):
    column = models.ForeignKey(
        AnalysisColumn,
        on_delete=models.CASCADE,
        related_name="values",
    )
    test_result = models.ForeignKey(
        TestResult,
        on_delete=models.CASCADE,
        related_name="analysis_values",
    )
    value = models.TextField(blank=True)
    # "triage" for a value the triage job wrote; it is replaced when triage is re-published.
    # Any edit by a person makes it "person", and re-publishes never overwrite it.
    source = models.CharField(max_length=16, default="person")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_analysis_values",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["column__position", "column_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["column", "test_result"],
                name="unique_analysis_value_per_result",
            )
        ]

    def __str__(self):
        return f"{self.test_result}: {self.column.name}"
