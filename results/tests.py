import base64
import gzip
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    AnalysisColumn,
    AnalysisValue,
    Artifact,
    Board,
    ElfSubmission,
    JenkinsJob,
    Status,
    TestResult,
    TestRun,
)
from .models import TestCase as ACTTestCase


class PortalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("viewer", password="safe-test-password")

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def _riscv_elf(self, name="uploaded.elf"):
        header = bytearray(64)
        header[:4] = b"\x7fELF"
        header[4] = 2
        header[5] = 1
        header[18:20] = (243).to_bytes(2, "little")
        return SimpleUploadedFile(name, bytes(header), content_type="application/x-elf")

    def test_elf_upload_requires_login(self):
        self.assertEqual(self.client.get(reverse("elf-submit")).status_code, 302)

    def test_elf_upload_queues_jenkins_job(self):
        board = Board.objects.create(slug="visionfive2", name="VisionFive 2")
        self.client.force_login(self.user)
        with (
            tempfile.TemporaryDirectory() as temporary,
            override_settings(
                MEDIA_ROOT=Path(temporary),
                PORTAL_SINGLE_ELF_BOARD_SLUGS=("visionfive2",),
            ),
            patch(
                "results.views._trigger_single_elf_job",
                return_value="https://jenkins/queue/item/7/",
            ) as trigger,
        ):
            response = self.client.post(
                reverse("elf-submit"),
                {"board": str(board.id), "elf": self._riscv_elf("case.elf")},
            )
            submission = ElfSubmission.objects.get()
            self.assertRedirects(response, submission.get_absolute_url())
            self.assertEqual(submission.original_name, "case.elf")
            self.assertEqual(submission.size_bytes, 64)
            self.assertEqual(submission.jenkins_queue_url, "https://jenkins/queue/item/7/")
            trigger.assert_called_once()

    def test_elf_upload_rejects_non_riscv_file(self):
        board = Board.objects.create(slug="visionfive2", name="VisionFive 2")
        self.client.force_login(self.user)
        with override_settings(PORTAL_SINGLE_ELF_BOARD_SLUGS=("visionfive2",)):
            response = self.client.post(
                reverse("elf-submit"),
                {"board": str(board.id), "elf": SimpleUploadedFile("bad.elf", b"no")},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not an ELF binary")
        self.assertFalse(ElfSubmission.objects.exists())

    def test_jenkins_can_download_elf_only_with_submission_token(self):
        board = Board.objects.create(slug="visionfive2", name="VisionFive 2")
        raw_token = "one-time-test-token"
        with (
            tempfile.TemporaryDirectory() as temporary,
            override_settings(MEDIA_ROOT=Path(temporary)),
        ):
            submission = ElfSubmission.objects.create(
                uploaded_by=self.user,
                board=board,
                elf=self._riscv_elf("case.elf"),
                original_name="case.elf",
                sha256="0" * 64,
                size_bytes=64,
                download_token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            )
            url = reverse("elf-download", args=[submission.id])
            self.assertEqual(self.client.get(url).status_code, 401)
            response = self.client.get(url, headers={"X-ELF-Download-Token": raw_token})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(b"".join(response.streaming_content).startswith(b"\x7fELF"))

    def test_dashboard_shows_suite_results_for_each_board(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-uart-weekly")
        run = TestRun.objects.create(
            job=job,
            build_number=1,
            expected_cases=2,
            completed_cases=2,
            passed_cases=1,
            failed_cases=1,
        )
        passing = ACTTestCase.objects.create(name="ExceptionsM-01", category="Privileged")
        failing = ACTTestCase.objects.create(name="I-add-01", category="Non-Privileged")
        TestResult.objects.create(
            run=run,
            test_case=passing,
            hardware_status=Status.PASS,
        )
        TestResult.objects.create(
            run=run,
            test_case=failing,
            hardware_status=Status.FAIL,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Privileged")
        self.assertContains(response, "Non-Privileged")
        self.assertContains(response, "1 passed · 0 failed · 1 executed")
        self.assertContains(response, "0 passed · 1 failed · 1 executed")
        self.assertContains(response, "1 / 2 passed")
        self.assertContains(response, 'style="width: 50.0%"')
        self.assertContains(response, "PASS / Total")

    def test_dashboard_remove_action_is_visible_only_to_staff(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-uart-weekly")
        TestRun.objects.create(job=job, build_number=7)

        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, "run-remove-button")

        staff = get_user_model().objects.create_user(
            "dashboard-operator", password="safe-test-password", is_staff=True
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "run-remove-button")
        self.assertContains(response, "Remove vf2-uart-weekly build #7")

    def test_staff_can_remove_run_from_dashboard_and_return_to_dashboard(self):
        staff = get_user_model().objects.create_user(
            "dashboard-operator", password="safe-test-password", is_staff=True
        )
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-uart-weekly")
        run = TestRun.objects.create(job=job, build_number=7)

        self.client.force_login(staff)
        response = self.client.post(
            reverse("run-delete", args=["vf2", "vf2-uart-weekly", 7]),
            {"return_to": "dashboard"},
        )

        self.assertRedirects(response, reverse("dashboard"))
        self.assertFalse(TestRun.objects.filter(id=run.id).exists())

    def test_dashboard_and_board_pages_show_only_uart_weekly_runs(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        weekly_job = JenkinsJob.objects.create(board=board, name="vf2-uart-weekly")
        sanity_job = JenkinsJob.objects.create(board=board, name="vf2-uart-sanity")
        single_job = JenkinsJob.objects.create(board=board, name="riscv-uart-single-elf")
        TestRun.objects.create(job=weekly_job, build_number=3, status=Status.PASS)
        TestRun.objects.create(job=sanity_job, build_number=4, status=Status.FAIL)
        TestRun.objects.create(job=single_job, build_number=5, status=Status.FAIL)

        self.client.force_login(self.user)
        dashboard = self.client.get(reverse("dashboard"))
        board_page = self.client.get(board.get_absolute_url())

        for response in (dashboard, board_page):
            self.assertContains(response, "vf2-uart-weekly")
            self.assertNotContains(response, "vf2-uart-sanity")
            self.assertNotContains(response, "riscv-uart-single-elf")
        self.assertContains(dashboard, "Total runs</span><strong>1</strong>")

    def test_single_elf_result_is_private_to_submitter_and_staff(self):
        board = Board.objects.create(slug="visionfive2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="riscv-uart-single-elf")
        run = TestRun.objects.create(job=job, build_number=15, status=Status.PASS)
        submission = ElfSubmission.objects.create(
            uploaded_by=self.user,
            board=board,
            elf="elf-uploads/private.elf",
            original_name="private.elf",
            sha256="0" * 64,
            size_bytes=64,
            download_token_hash="",
            run=run,
        )
        other_user = get_user_model().objects.create_user(
            "other-viewer", password="safe-test-password"
        )
        staff = get_user_model().objects.create_user(
            "single-elf-admin", password="safe-test-password", is_staff=True
        )

        self.client.force_login(self.user)
        self.assertEqual(self.client.get(submission.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(run.get_absolute_url()).status_code, 200)

        self.client.force_login(other_user)
        self.assertEqual(self.client.get(submission.get_absolute_url()).status_code, 403)
        self.assertEqual(self.client.get(run.get_absolute_url()).status_code, 403)

        self.client.force_login(staff)
        self.assertEqual(self.client.get(submission.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(run.get_absolute_url()).status_code, 200)

    def test_board_remove_action_is_visible_only_to_staff(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-uart-weekly")
        TestRun.objects.create(job=job, build_number=11)

        self.client.force_login(self.user)
        response = self.client.get(board.get_absolute_url())
        self.assertNotContains(response, "run-remove-button")

        staff = get_user_model().objects.create_user(
            "board-operator", password="safe-test-password", is_staff=True
        )
        self.client.force_login(staff)
        response = self.client.get(board.get_absolute_url())
        self.assertContains(response, "run-remove-button")
        self.assertContains(response, "Remove vf2-uart-weekly build #11")
        self.assertContains(
            response,
            "This removes the execution from the board page and main dashboard",
        )

    def test_run_detail_not_run_filter_includes_skipped_and_unknown(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-job")
        run = TestRun.objects.create(job=job, build_number=3)
        for name, status in (
            ("PassedM-01", Status.PASS),
            ("SkippedM-01", Status.SKIPPED),
            ("MissingM-01", Status.UNKNOWN),
        ):
            test_case = ACTTestCase.objects.create(name=name, category="Privileged")
            TestResult.objects.create(
                run=run,
                test_case=test_case,
                hardware_status=status,
            )

        self.client.force_login(self.user)
        response = self.client.get(
            reverse("run-detail", args=["vf2", "vf2-job", 3]),
            {"status": "NOT_RUN"},
        )
        self.assertContains(response, "SkippedM-01")
        self.assertContains(response, "MissingM-01")
        self.assertNotContains(response, "PassedM-01")
        self.assertContains(response, '<option value="NOT_RUN" selected>Not run</option>')

    def test_same_build_number_from_two_jobs_has_distinct_run_urls(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        sanity = JenkinsJob.objects.create(board=board, name="vf2-uart-sanity")
        weekly = JenkinsJob.objects.create(board=board, name="vf2-uart-weekly")
        sanity_run = TestRun.objects.create(job=sanity, build_number=1)
        weekly_run = TestRun.objects.create(job=weekly, build_number=1)

        self.assertNotEqual(sanity_run.get_absolute_url(), weekly_run.get_absolute_url())
        self.client.force_login(self.user)
        sanity_response = self.client.get(sanity_run.get_absolute_url())
        weekly_response = self.client.get(weekly_run.get_absolute_url())
        self.assertContains(sanity_response, "vf2-uart-sanity")
        self.assertContains(weekly_response, "vf2-uart-weekly")

        legacy = self.client.get(reverse("legacy-run-detail", args=["vf2", 1]))
        self.assertRedirects(legacy, weekly_run.get_absolute_url())

    def test_non_staff_user_cannot_delete_run(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-job")
        run = TestRun.objects.create(job=job, build_number=4)

        self.client.force_login(self.user)
        detail = self.client.get(reverse("run-detail", args=["vf2", "vf2-job", 4]))
        self.assertNotContains(detail, "Delete from portal")
        response = self.client.post(reverse("run-delete", args=["vf2", "vf2-job", 4]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(TestRun.objects.filter(id=run.id).exists())

    def test_staff_user_can_delete_run_and_portal_uart_files(self):
        staff = get_user_model().objects.create_user(
            "operator", password="safe-test-password", is_staff=True
        )
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-job")
        run = TestRun.objects.create(job=job, build_number=4)
        test_case = ACTTestCase.objects.create(name="ExceptionsM-01")
        result = TestResult.objects.create(run=run, test_case=test_case)
        Artifact.objects.create(run=run, name="summary", relative_path="summary.md")

        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary).resolve()
            uart_directory = artifact_root / "uart" / "vf2" / "vf2-job" / "4"
            uart_directory.mkdir(parents=True)
            (uart_directory / "ExceptionsM-01.log").write_text("no test run")

            self.client.force_login(staff)
            detail = self.client.get(reverse("run-detail", args=["vf2", "vf2-job", 4]))
            self.assertContains(detail, "Delete from portal")
            self.assertContains(detail, "Cancel")
            self.assertContains(detail, "OK, delete")
            with override_settings(PORTAL_ARTIFACT_ROOT=artifact_root):
                response = self.client.post(reverse("run-delete", args=["vf2", "vf2-job", 4]))

            self.assertRedirects(response, reverse("board-detail", args=["vf2"]))
            self.assertFalse(uart_directory.exists())

        self.assertFalse(TestRun.objects.filter(id=run.id).exists())
        self.assertFalse(TestResult.objects.filter(id=result.id).exists())
        self.assertFalse(Artifact.objects.filter(run_id=run.id).exists())

    def test_workbook_execution_results_are_read_only_for_viewer(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-job")
        run = TestRun.objects.create(job=job, build_number=8)
        test_case = ACTTestCase.objects.create(
            name="ExceptionsM-01", category="Privileged", extension="ExceptionsM"
        )
        TestResult.objects.create(
            run=run,
            test_case=test_case,
            sail_status=Status.PASS,
            spike_status=Status.PASS,
            hardware_status=Status.FAIL,
            failure_reason="hardware mismatch",
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("run-detail", args=["vf2", "vf2-job", 8]))

        self.assertContains(response, "ExceptionsM-01")
        self.assertNotContains(response, "hardware mismatch")  # failure reason not shown
        self.assertNotContains(response, "Add an analysis column")
        denied = self.client.post(
            reverse("analysis-column-add", args=["vf2", "vf2-job", 8]),
            {"name": "Owner"},
        )
        self.assertEqual(denied.status_code, 403)

    def test_hypervisor_suite_is_summarized_and_has_a_workbook_tab(self):
        board = Board.objects.create(slug="milkv-megrez", name="Milk-V Megrez")
        job = JenkinsJob.objects.create(board=board, name="megrez-uart-weekly")
        run = TestRun.objects.create(job=job, build_number=5, expected_cases=2, completed_cases=2)
        for name, status in (("ExceptionsH_ecall-00", Status.PASS), ("H_trap-00", Status.FAIL)):
            TestResult.objects.create(
                run=run,
                test_case=ACTTestCase.objects.create(
                    name=name, category="Hypervisor", extension=name.rsplit("-", 1)[0]
                ),
                hardware_status=status,
            )

        self.client.force_login(self.user)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "Hypervisor")
        self.assertContains(dashboard, "1 passed · 1 failed · 2 executed")
        detail = self.client.get(
            reverse("run-detail", args=["milkv-megrez", "megrez-uart-weekly", 5])
        )
        # A run with one suite shows only the "All tests" card (no repeated suite card or tab).
        self.assertContains(detail, '<p class="eyebrow">All tests</p>')
        self.assertNotContains(detail, '<p class="eyebrow">Hypervisor</p>')
        tab = self.client.get(
            reverse("run-detail", args=["milkv-megrez", "megrez-uart-weekly", 5]),
            {"suite": "Hypervisor"},
        )
        self.assertContains(tab, "H_trap-00")
        self.assertContains(tab, "ExceptionsH_ecall-00")

    @override_settings(PORTAL_INGEST_TOKEN="test-token")
    def test_ingest_creates_run_and_results(self):
        payload = {
            "board": {"slug": "vf2", "name": "VisionFive 2", "core_profile": "U74"},
            "job": {"name": "vf2-privileged-weekly"},
            "build_number": 3,
            "status": "RUNNING",
            "expected_cases": 485,
            "completed_cases": 6,
            "git_revision": "runner123",
            "act_revision": "act456",
            "metadata": {"sail_version": "0.14"},
            "results": [
                {
                    "name": "ExceptionsM-01",
                    "category": "Privileged",
                    "hardware_status": "PASS",
                    "log_path": "https://jenkins/artifact/uart.log",
                },
                {
                    "name": "I-add-01",
                    "category": "Non-Privileged",
                    "hardware_status": "PASS",
                },
            ],
            "artifacts": [
                {
                    "name": "summary.md",
                    "relative_path": "/agent/results/summary.md",
                    "external_url": "https://jenkins/artifact/summary.md",
                }
            ],
        }
        response = self.client.post(
            reverse("api-ingest-run"),
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-Portal-Token": "test-token"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(TestRun.objects.get().expected_cases, 485)
        self.assertEqual(TestRun.objects.get().test_results.count(), 2)

        self.client.force_login(self.user)
        detail = self.client.get(reverse("run-detail", args=["vf2", "vf2-privileged-weekly", 3]))
        self.assertContains(detail, "Sail version")
        self.assertContains(detail, "0.14")
        self.assertContains(detail, "runner123")
        self.assertContains(detail, "ExceptionsM-01")
        self.assertContains(detail, "I-add-01")
        self.assertContains(detail, "https://jenkins/artifact/uart.log")
        self.assertContains(detail, "https://jenkins/artifact/summary.md")

        payload["completed_cases"] = 7
        payload["results"] = payload["results"][:1]
        response = self.client.post(
            reverse("api-ingest-run"),
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-Portal-Token": "test-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["created"])
        self.assertEqual(TestRun.objects.get().completed_cases, 7)
        self.assertEqual(TestRun.objects.get().test_results.count(), 1)
        self.assertFalse(
            TestRun.objects.get().test_results.filter(test_case__name="I-add-01").exists()
        )

    @override_settings(PORTAL_INGEST_TOKEN="test-token")
    def test_ingest_links_single_elf_submission_to_run(self):
        board = Board.objects.create(slug="visionfive2", name="VisionFive 2")
        with (
            tempfile.TemporaryDirectory() as temporary,
            override_settings(MEDIA_ROOT=Path(temporary)),
        ):
            submission = ElfSubmission.objects.create(
                uploaded_by=self.user,
                board=board,
                elf=self._riscv_elf(),
                original_name="uploaded.elf",
                sha256="0" * 64,
                size_bytes=64,
                download_token_hash="0" * 64,
            )
            response = self.client.post(
                reverse("api-ingest-run"),
                data=json.dumps(
                    {
                        "board": {"slug": "visionfive2", "name": "VisionFive 2"},
                        "job": {"name": "riscv-uart-single-elf"},
                        "build_number": 12,
                        "status": "PASS",
                        "metadata": {"submission_id": str(submission.id)},
                        "results": [{"name": "uploaded.elf", "hardware_status": "PASS"}],
                    }
                ),
                content_type="application/json",
                headers={"X-Portal-Token": "test-token"},
            )
            self.assertEqual(response.status_code, 201)
            submission.refresh_from_db()
            self.assertEqual(submission.status, Status.PASS)
            self.assertEqual(submission.run.build_number, 12)
            self.assertEqual(submission.download_token_hash, "")

    def test_artifact_cannot_escape_root(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-job")
        run = TestRun.objects.create(job=job, build_number=1)
        artifact = Artifact.objects.create(run=run, name="secret", relative_path="../secret")
        self.client.force_login(self.user)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary, "artifacts")
            root.mkdir()
            Path(temporary, "secret").write_text("no")
            with override_settings(PORTAL_ARTIFACT_ROOT=root.resolve()):
                response = self.client.get(reverse("artifact-download", args=[artifact.id]))
        self.assertEqual(response.status_code, 404)

    @override_settings(PORTAL_INGEST_TOKEN="test-token")
    def test_ingested_uart_log_is_served_by_authenticated_portal(self):
        uart_content = b"Booting VF2\nPASS ExceptionsM-01\n"
        payload = {
            "board": {"slug": "vf2", "name": "VisionFive 2"},
            "job": {"name": "vf2-privileged-weekly"},
            "build_number": 3,
            "results": [
                {
                    "name": "ExceptionsM-01",
                    "category": "Privileged",
                    "hardware_status": "PASS",
                    "uart_log_gzip_b64": base64.b64encode(gzip.compress(uart_content)).decode(
                        "ascii"
                    ),
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary).resolve()
            with override_settings(PORTAL_ARTIFACT_ROOT=artifact_root):
                response = self.client.post(
                    reverse("api-ingest-run"),
                    data=json.dumps(payload),
                    content_type="application/json",
                    headers={"X-Portal-Token": "test-token"},
                )
                self.assertEqual(response.status_code, 201)
                result = TestResult.objects.get()
                self.assertTrue(result.log_path.startswith("uart/vf2/"))

                anonymous = self.client.get(reverse("test-uart-download", args=[result.id]))
                self.assertEqual(anonymous.status_code, 302)
                self.client.force_login(self.user)
                downloaded = self.client.get(reverse("test-uart-download", args=[result.id]))
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(b"".join(downloaded.streaming_content), uart_content)

    def test_board_info_page_shows_isa_specs_and_boot_flow(self):
        board = Board.objects.create(
            slug="milkv-megrez",
            name="Milk-V Megrez",
            profile={
                "sections": [
                    {
                        "title": "ISA",
                        "rows": [
                            {
                                "label": "Board ISA",
                                "value": "rv64imafdchx",
                                "status": "confirmed",
                                "source": "OpenSBI boot log",
                            },
                            {"label": "Hypervisor tests", "value": "H 1.0", "status": "deviation"},
                        ],
                    }
                ],
                "extensions": {"source": "ACT config", "items": [["I", "2.1"], ["Sv48", "1.0.0"]]},
                "boot_flows": [
                    {
                        "name": "CI boot",
                        "status": "confirmed",
                        "steps": ["ESWIN ROM", "UART runner"],
                    }
                ],
                "notes": ["USB-C debug port must be unplugged."],
            },
        )

        self.assertEqual(self.client.get(reverse("board-info", args=[board.slug])).status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(reverse("board-info", args=[board.slug]))

        self.assertContains(response, "rv64imafdchx")
        self.assertContains(response, "OpenSBI boot log")
        self.assertContains(response, '<span class="badge status-pass">confirmed</span>', html=True)
        self.assertContains(
            response, '<span class="badge status-unstable">deviation</span>', html=True
        )
        self.assertContains(response, "<li><strong>Sv48</strong><span>1.0.0</span></li>", html=True)
        self.assertContains(response, "<li>ESWIN ROM</li>", html=True)
        self.assertContains(response, "USB-C debug port must be unplugged.")
        self.assertNotContains(response, "Edit profile")
        board_page = self.client.get(board.get_absolute_url())
        self.assertContains(board_page, reverse("board-info", args=[board.slug]))

    def test_board_info_page_without_profile(self):
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        staff = get_user_model().objects.create_user(
            "profile-editor", password="safe-test-password", is_staff=True
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("board-info", args=[board.slug]))
        self.assertContains(response, "No board profile has been recorded yet.")
        self.assertContains(response, "Edit profile")

    def test_seed_migration_fills_only_empty_profiles(self):
        from importlib import import_module

        from django.apps import apps

        seed = import_module("results.migrations.0006_seed_board_profiles")
        empty = Board.objects.create(slug="milkv-megrez", name="Milk-V Megrez")
        edited = Board.objects.create(
            slug="visionfive2", name="VisionFive 2", profile={"notes": ["x"]}
        )
        seed.seed(apps, None)
        empty.refresh_from_db()
        edited.refresh_from_db()
        self.assertEqual(empty.profile["sections"][0]["title"], "Identity")
        self.assertEqual(edited.profile, {"notes": ["x"]})

    def test_platform_name_appears_in_header_title_and_login(self):
        name = "RISC-V Architectural Compliance &amp; Post-Silicon Regression Platform"
        login = self.client.get(reverse("login"))
        self.assertContains(login, f"<title>Sign in · {name}</title>", html=False)
        self.assertContains(login, f"<h1>{name}</h1>", html=False)
        self.client.force_login(self.user)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, f'<span class="brand-name">{name}</span>', html=False)
        self.assertContains(dashboard, f"<title>Dashboard · {name}</title>", html=False)
        self.assertNotContains(dashboard, "RISC-V CI Portal")

    def test_board_names_link_to_board_info(self):
        board = Board.objects.create(slug="milkv-megrez", name="Milk-V Megrez")
        job = JenkinsJob.objects.create(board=board, name="megrez-uart-weekly")
        run = TestRun.objects.create(job=job, build_number=5)
        submission = ElfSubmission.objects.create(
            uploaded_by=self.user,
            board=board,
            elf="elf-uploads/case.elf",
            original_name="case.elf",
            sha256="0" * 64,
            size_bytes=64,
            download_token_hash="",
        )
        info_url = reverse("board-info", args=[board.slug])
        link = f'<a class="board-link" href="{info_url}"'
        self.client.force_login(self.user)

        dashboard = self.client.get(reverse("dashboard"))
        # Card name and recent-runs row link to board info; the card still opens the runs page.
        self.assertGreaterEqual(dashboard.content.decode().count(link), 2)
        cover = f'class="board-card-cover" href="{board.get_absolute_url()}"'
        self.assertContains(dashboard, cover)
        self.assertNotContains(dashboard, '<a class="board-card"')
        pages = [
            board.get_absolute_url(),
            run.get_absolute_url(),
            reverse("elf-submission-detail", args=[submission.id]),
            reverse("elf-submit"),
        ]
        for url in pages:
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), link)

    def test_login_and_header_show_10xengineers_logo(self):
        login = self.client.get(reverse("login"))
        self.assertContains(login, 'class="brand-logo large-logo"')
        self.assertContains(login, "results/10xengineers-logo-white.png")
        self.assertNotContains(login, "Apollo validation services")
        self.assertNotContains(login, ">RV<")
        self.client.force_login(self.user)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, 'alt="10xEngineers"')
        self.assertNotContains(dashboard, "Apollo")


def _elf_loading_at(address, memsz=0x1000, name="case.elf"):
    """Minimal RISC-V ELF64 with one PT_LOAD segment at address."""
    import struct

    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4], header[5] = 2, 1
    header[18:20] = (243).to_bytes(2, "little")
    struct.pack_into("<Q", header, 0x20, 64)  # e_phoff
    struct.pack_into("<HH", header, 0x36, 56, 1)  # e_phentsize, e_phnum
    phdr = struct.pack("<IIQQQQQQ", 1, 5, 0x1000, address, address, 16, memsz, 0x1000)
    return SimpleUploadedFile(
        name, bytes(header) + phdr + b"\0" * 16, content_type="application/x-elf"
    )


MEGREZ_RULES = {"elf_load_rules": {"window": ["0x90000000", "0xB0000000"], "reserved": []}}
VF2_RULES = {"elf_load_rules": {"window": ["0x80000000", "0x88000000"], "reserved": []}}


class RunElfChecksTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("viewer", password="safe-test-password")
        self.client.force_login(self.user)
        self.megrez = Board.objects.create(
            slug="milkv-megrez", name="Milk-V Megrez", profile=MEGREZ_RULES
        )
        self.vf2 = Board.objects.create(slug="visionfive2", name="VisionFive 2", profile=VF2_RULES)
        self.settings_ctx = override_settings(
            PORTAL_SINGLE_ELF_BOARD_SLUGS=("milkv-megrez", "visionfive2")
        )
        self.settings_ctx.enable()
        self.addCleanup(self.settings_ctx.disable)

    def _report(self, **boards):
        from django.utils import timezone

        payload = {
            "checked_at": timezone.now().isoformat(),
            "boards": [
                {"slug": s, "online": on, "reason": "" if on else "no smart plug"}
                for s, on in boards.items()
            ],
        }
        with override_settings(PORTAL_INGEST_TOKEN="test-token"):
            return self.client.post(
                reverse("api-board-health"),
                data=json.dumps(payload),
                content_type="application/json",
                headers={"X-Portal-Token": "test-token"},
            )

    def test_health_endpoint_requires_token_and_updates_boards(self):
        with override_settings(PORTAL_INGEST_TOKEN="test-token"):
            denied = self.client.post(
                reverse("api-board-health"), data="{}", content_type="application/json"
            )
        self.assertEqual(denied.status_code, 401)
        response = self._report(**{"milkv-megrez": True, "visionfive2": False})
        self.assertEqual(sorted(response.json()["updated"]), ["milkv-megrez", "visionfive2"])
        self.megrez.refresh_from_db()
        self.vf2.refresh_from_db()
        self.assertEqual(self.megrez.availability()["state"], "online")
        self.assertEqual(self.vf2.availability()["state"], "offline")
        self.assertEqual(self.vf2.availability()["reason"], "no smart plug")

    def test_stale_or_missing_report_is_unknown(self):
        self.assertEqual(self.megrez.availability()["state"], "unknown")
        self.megrez.health = {"online": True, "checked_at": "2020-01-01T00:00:00+00:00"}
        self.assertEqual(self.megrez.availability()["state"], "unknown")

    def test_page_shows_status_and_disables_offline_boards(self):
        self._report(**{"milkv-megrez": True, "visionfive2": False})
        page = self.client.get(reverse("elf-submit"))
        self.assertContains(page, "Milk-V Megrez")
        self.assertContains(page, f'<option value="{self.vf2.id}" disabled>')
        self.assertContains(page, "no smart plug")
        self.assertContains(page, 'id="elf-board-rules"')
        self.assertContains(page, "0x90000000")

    def test_offline_board_is_rejected_before_upload(self):
        self._report(**{"milkv-megrez": True, "visionfive2": False})
        with patch("results.views._trigger_single_elf_job") as trigger:
            response = self.client.post(
                reverse("elf-submit"),
                {"board": str(self.vf2.id), "elf": _elf_loading_at(0x80000000)},
            )
        self.assertContains(response, "VisionFive 2 is offline")
        self.assertFalse(ElfSubmission.objects.exists())
        trigger.assert_not_called()

    def test_wrong_board_elf_is_rejected_before_upload(self):
        self._report(**{"milkv-megrez": True, "visionfive2": True})
        with patch("results.views._trigger_single_elf_job") as trigger:
            response = self.client.post(
                reverse("elf-submit"),
                {"board": str(self.megrez.id), "elf": _elf_loading_at(0x80000000)},
            )
        self.assertContains(response, "does not fit Milk-V Megrez")
        self.assertContains(response, "It looks built for VisionFive 2")
        self.assertFalse(ElfSubmission.objects.exists())
        trigger.assert_not_called()

    def test_matching_elf_is_queued(self):
        self._report(**{"milkv-megrez": True})
        with (
            tempfile.TemporaryDirectory() as temporary,
            override_settings(MEDIA_ROOT=Path(temporary)),
            patch(
                "results.views._trigger_single_elf_job", return_value="https://jenkins/queue/1/"
            ) as trigger,
        ):
            response = self.client.post(
                reverse("elf-submit"),
                {"board": str(self.megrez.id), "elf": _elf_loading_at(0x90000000)},
            )
            submission = ElfSubmission.objects.get()
            self.assertRedirects(response, submission.get_absolute_url())
        trigger.assert_called_once()


class WorkbookTests(TestCase):
    def setUp(self):
        self.board = Board.objects.create(slug="milkv-megrez", name="Milk-V Megrez")
        self.job = JenkinsJob.objects.create(board=self.board, name="megrez-uart-weekly")
        self.run = TestRun.objects.create(job=self.job, build_number=5)
        self.results = {}
        for name, category, status in (
            ("H_trap-00", "Hypervisor", Status.FAIL),
            ("ExceptionsH_ecall-00", "Hypervisor", Status.PASS),
            ("ExceptionsS-00", "Privileged", Status.FAIL),
        ):
            self.results[name] = TestResult.objects.create(
                run=self.run,
                test_case=ACTTestCase.objects.create(name=name, category=category),
                sail_status=Status.PASS,
                hardware_status=status,
            )
        self.viewer = get_user_model().objects.create_user("viewer", password="safe-test-password")
        self.editor = get_user_model().objects.create_user("editor", password="safe-test-password")
        self.editor.user_permissions.add(Permission.objects.get(codename="manage_failure_analysis"))
        self.args = ["milkv-megrez", "megrez-uart-weekly", 5]

    def _triage(self, **extra):
        payload = {
            "job_name": "megrez-uart-weekly",
            "build_number": 5,
            "results": [
                {
                    "name": "H_trap-00",
                    "triage_category": "trap_cause_mismatch",
                    "triage_owner": "Needs architectural review",
                    "triage_explanation": "Expected cause 0x14, observed 0x2.",
                    "triage_evidence": {"mcause": "0x2", "mepc": "0x90001234", "satp": ""},
                    "ai_analysis": "",
                },
                {"name": "ExceptionsS-00", "triage_explanation": "No coherent tuple."},
                {"name": "NotInThisRun-00", "triage_explanation": "x"},
            ],
            **extra,
        }
        with override_settings(PORTAL_INGEST_TOKEN="test-token"):
            return self.client.post(
                reverse("api-ingest-triage"),
                data=json.dumps(payload),
                content_type="application/json",
                headers={"X-Portal-Token": "test-token"},
            )

    def _value(self, key, test):
        return AnalysisValue.objects.get(
            column__run=self.run, column__key=key, test_result=self.results[test]
        )

    def _save_cell(self, column, test, value):
        return self.client.post(
            reverse("analysis-cell-save", args=self.args),
            {"column": column.id, "result": self.results[test].id, "value": value},
        )

    def test_triage_fills_columns_after_verdict_and_reports_unknown_tests(self):
        with override_settings(PORTAL_INGEST_TOKEN="test-token"):
            denied = self.client.post(
                reverse("api-ingest-triage"), data="{}", content_type="application/json"
            )
        self.assertEqual(denied.status_code, 401)
        AnalysisColumn.objects.create(run=self.run, name="Notes", position=1)

        response = self._triage()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unknown_tests"], ["NotInThisRun-00"])
        names = list(self.run.analysis_columns.order_by("position").values_list("name", flat=True))
        self.assertEqual(
            names,
            [
                "Verdict",
                "Root cause (triage)",
                "Owner (triage)",
                "Evidence (triage)",
                "Notes",
            ],
        )
        self.assertEqual(
            self._value("triage_root_cause", "H_trap-00").value,
            "Expected cause 0x14, observed 0x2.",
        )
        self.assertEqual(
            self._value("triage_evidence", "H_trap-00").value, "mcause: 0x2\nmepc: 0x90001234"
        )
        self.assertEqual(self._value("verdict", "H_trap-00").value, "Needs investigation")
        self.assertEqual(self._value("verdict", "H_trap-00").source, "triage")
        self.run.refresh_from_db()
        self.assertEqual(self.run.metadata["triage"]["failures"], 2)

    def test_republished_triage_keeps_person_edits(self):
        self._triage()
        self.client.force_login(self.editor)
        root_cause = self.run.analysis_columns.get(key="triage_root_cause")
        verdict = self.run.analysis_columns.get(key="verdict")
        self.assertEqual(
            self._save_cell(root_cause, "H_trap-00", "Missing htimedelta guard").status_code, 200
        )
        self._save_cell(verdict, "H_trap-00", "Test or ACT issue")

        response = self._triage()

        self.assertEqual(response.json()["kept_person_edits"], 2)
        self.assertEqual(
            self._value("triage_root_cause", "H_trap-00").value, "Missing htimedelta guard"
        )
        self.assertEqual(self._value("triage_root_cause", "H_trap-00").updated_by, self.editor)
        self.assertEqual(self._value("verdict", "H_trap-00").value, "Test or ACT issue")
        self.assertEqual(
            self._value("triage_owner", "H_trap-00").value, "Needs architectural review"
        )

    def test_editor_saves_cells_but_results_stay_locked(self):
        self.client.force_login(self.editor)
        page = self.client.get(reverse("run-detail", args=self.args))
        self.assertContains(page, "data-save-url")
        verdict = self.run.analysis_columns.get(key="verdict")

        bad = self._save_cell(verdict, "H_trap-00", "Definitely fine")
        self.assertEqual(bad.status_code, 400)
        ok = self._save_cell(verdict, "H_trap-00", "Known deviation")
        self.assertEqual(ok.json()["verdict_class"], "known-deviation")
        cleared = self._save_cell(verdict, "H_trap-00", "")
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(AnalysisValue.objects.filter(column=verdict).exists())
        result = self.results["H_trap-00"]
        result.refresh_from_db()
        self.assertEqual(result.hardware_status, Status.FAIL)

    def test_viewer_cannot_edit(self):
        self.client.force_login(self.viewer)
        page = self.client.get(reverse("run-detail", args=self.args))
        self.assertContains(page, "H_trap-00")
        self.assertNotContains(page, "Add column")
        self.assertNotContains(page, "data-save-url")
        verdict = self.run.analysis_columns.get(key="verdict")
        self.assertEqual(self._save_cell(verdict, "H_trap-00", "Waived").status_code, 403)
        denied = self.client.post(reverse("analysis-column-add", args=self.args), {"name": "X"})
        self.assertEqual(denied.status_code, 403)

    def test_columns_can_be_added_renamed_moved_and_deleted(self):
        self.client.force_login(self.editor)
        self.client.get(reverse("run-detail", args=self.args))
        added = self.client.post(
            reverse("analysis-column-add", args=self.args), {"name": "Fix PR", "status": "FAIL"}
        )
        self.assertRedirects(added, reverse("run-detail", args=self.args) + "?status=FAIL")
        column = self.run.analysis_columns.get(name="Fix PR")
        update = reverse("analysis-column-update", args=[*self.args, column.id])
        self.client.post(update, {"action": "rename", "name": "Fix link"})
        self.client.post(update, {"action": "left"})
        names = list(self.run.analysis_columns.order_by("position").values_list("name", flat=True))
        self.assertEqual(names, ["Fix link", "Verdict"])
        verdict = self.run.analysis_columns.get(key="verdict")
        self.client.post(
            reverse("analysis-column-update", args=[*self.args, verdict.id]), {"action": "delete"}
        )
        self.assertTrue(self.run.analysis_columns.filter(key="verdict").exists())
        self.client.post(update, {"action": "delete"})
        self.assertFalse(self.run.analysis_columns.filter(name="Fix link").exists())

    def test_failures_only_filter(self):
        self.client.force_login(self.viewer)
        page = self.client.get(reverse("run-detail", args=self.args) + "?status=FAIL")
        self.assertContains(page, "H_trap-00")
        self.assertNotContains(page, "ExceptionsH_ecall-00")
        hyp = self.client.get(
            reverse("run-detail", args=self.args) + "?suite=Hypervisor&status=FAIL"
        )
        self.assertContains(hyp, "H_trap-00")
        self.assertNotContains(hyp, "ExceptionsS-00")

    def test_excel_export_contains_sheets_results_and_analysis(self):
        import io
        import xml.etree.ElementTree as ET
        import zipfile

        self._triage()
        self.client.force_login(self.editor)
        verdict = self.run.analysis_columns.get(key="verdict")
        self._save_cell(verdict, "ExceptionsS-00", "Confirmed hardware bug")

        response = self.client.get(reverse("run-workbook-xlsx", args=self.args))

        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("megrez-uart-weekly-5-triage-report.xlsx", response["Content-Disposition"])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = [sheet.get("name") for sheet in workbook.find("m:sheets", ns)]
        self.assertEqual(
            sheets,
            ["Summary", "Test Status", "Failures", "Privileged Tests", "Hypervisor Tests"],
        )
        text = archive.read("xl/worksheets/sheet3.xml").decode()
        self.assertIn("Confirmed hardware bug", text)
        self.assertIn("Expected cause 0x14, observed 0x2.", text)
        self.assertNotIn("ExceptionsH_ecall-00", text)
        for name in archive.namelist():
            if name.endswith(".xml"):
                ET.fromstring(archive.read(name))

    def test_triage_without_failures_adds_no_columns(self):
        response = self._triage(results=[])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(self.run.analysis_columns.values_list("name", flat=True)), ["Verdict"]
        )
        self.run.refresh_from_db()
        self.assertEqual(self.run.metadata["triage"]["failures"], 0)

    def test_report_has_no_extension_failure_reason_or_category_columns(self):
        self._triage()
        self.client.force_login(self.viewer)
        page = self.client.get(reverse("run-detail", args=self.args))
        for removed in ("Extension", "Failure reason", "Category (triage)", "trap_cause_mismatch"):
            self.assertNotContains(page, removed)
        self.assertContains(page, "Download Triage Report")
        self.assertFalse(self.run.analysis_columns.filter(key="triage_category").exists())

    def test_ai_analysis_column_follows_verdict(self):
        payload_ai = "Failure reason: hedeleg bit 18 read back 0.\nRoot cause: H draft 0.6."
        with override_settings(PORTAL_INGEST_TOKEN="test-token"):
            self.client.post(
                reverse("api-ingest-triage"),
                data=json.dumps(
                    {
                        "job_name": "megrez-uart-weekly",
                        "build_number": 5,
                        "results": [
                            {
                                "name": "H_trap-00",
                                "triage_explanation": "x",
                                "ai_analysis": payload_ai,
                            }
                        ],
                    }
                ),
                content_type="application/json",
                headers={"X-Portal-Token": "test-token"},
            )
        names = list(self.run.analysis_columns.order_by("position").values_list("name", flat=True))
        self.assertEqual(names[:3], ["Verdict", "AI analysis", "Root cause (triage)"])
        self.assertEqual(self._value("ai_analysis", "H_trap-00").value, payload_ai)


class RunElfAnalysisTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="safe-test-password")
        self.other = get_user_model().objects.create_user("other", password="safe-test-password")
        board = Board.objects.create(slug="milkv-megrez", name="Milk-V Megrez")
        # Jenkins publishes single-ELF runs as "<job>-<board slug>".
        job = JenkinsJob.objects.create(board=board, name="riscv-uart-single-elf-milkv-megrez")
        self.run = TestRun.objects.create(job=job, build_number=7, status=Status.FAIL)
        self.result = TestResult.objects.create(
            run=self.run,
            test_case=ACTTestCase.objects.create(name="my_test.elf"),
            hardware_status=Status.FAIL,
        )
        self.submission = ElfSubmission.objects.create(
            uploaded_by=self.owner,
            board=board,
            elf="elf-uploads/my_test.elf",
            original_name="my_test.elf",
            sha256="0" * 64,
            size_bytes=64,
            download_token_hash="",
            run=self.run,
        )

    def test_board_suffixed_single_elf_run_is_private(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.run.get_absolute_url()).status_code, 403)
        workbook = reverse("run-workbook", args=["milkv-megrez", self.run.job.name, 7])
        self.assertEqual(self.client.get(workbook).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.run.get_absolute_url()).status_code, 200)

    def test_submission_page_waits_for_and_then_shows_ai_analysis(self):
        self.client.force_login(self.owner)
        waiting = self.client.get(self.submission.get_absolute_url())
        self.assertContains(waiting, "The AI analysis is being prepared")

        payload = {
            "job_name": "riscv-uart-single-elf-milkv-megrez",
            "build_number": 7,
            "results": [
                {
                    "name": "my_test.elf",
                    "triage_explanation": "Expected cause 0x14, observed 0x2.",
                    "ai_analysis": "Failure reason: htinst was 0.\nRoot cause: H draft 0.6.",
                }
            ],
        }
        with override_settings(PORTAL_INGEST_TOKEN="test-token"):
            response = self.client.post(
                reverse("api-ingest-triage"),
                data=json.dumps(payload),
                content_type="application/json",
                headers={"X-Portal-Token": "test-token"},
            )
        self.assertEqual(response.status_code, 200)
        page = self.client.get(self.submission.get_absolute_url())
        self.assertContains(page, "Failure reason: htinst was 0.")
        self.assertContains(page, "Expected cause 0x14, observed 0x2.")
        self.assertNotContains(page, "being prepared")


class UnifiedRunPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("viewer", password="safe-test-password")
        board = Board.objects.create(slug="vf2", name="VisionFive 2")
        job = JenkinsJob.objects.create(board=board, name="vf2-uart-weekly")
        self.run = TestRun.objects.create(
            job=job,
            build_number=9,
            status=Status.FAIL,
            metadata={
                "sail_version": "0.14.1",
                "build_url": "https://jenkins/job/vf2-uart-weekly/9/",
            },
        )
        for name, status in (("ExceptionsS-00", Status.FAIL), ("I-add-01", Status.PASS)):
            TestResult.objects.create(
                run=self.run,
                test_case=ACTTestCase.objects.create(name=name, category="Privileged"),
                hardware_status=status,
                duration_seconds=12.34,
                log_path="https://jenkins/artifact/uart.log",
            )
        Artifact.objects.create(run=self.run, name="summary.md", relative_path="summary.md")
        self.url = reverse("run-detail", args=["vf2", "vf2-uart-weekly", 9])
        self.client.force_login(self.user)

    def test_one_page_has_details_results_logs_and_analysis(self):
        page = self.client.get(self.url)
        for text in (
            "Run details &amp; artifacts",
            "summary.md",
            "0.14.1",
            'href="/job/vf2-uart-weekly/9/"',
            "Download Triage Report",
            "ExceptionsS-00",
            "https://jenkins/artifact/uart.log",
            "12.3 s",
            "Verdict",
        ):
            with self.subTest(text=text):
                self.assertContains(page, text)

    def test_old_workbook_links_redirect_with_their_filters(self):
        old = reverse("run-workbook", args=["vf2", "vf2-uart-weekly", 9])
        self.assertRedirects(self.client.get(old), self.url)
        self.assertRedirects(
            self.client.get(old, {"suite": "Privileged", "show": "failed"}),
            self.url + "?suite=Privileged&status=FAIL",
        )

    def test_search_and_status_filters(self):
        failed = self.client.get(self.url, {"status": "FAIL"})
        self.assertContains(failed, "ExceptionsS-00")
        self.assertNotContains(failed, "I-add-01")
        searched = self.client.get(self.url, {"q": "add"})
        self.assertContains(searched, "I-add-01")
        self.assertNotContains(searched, "ExceptionsS-00")
        self.assertContains(searched, "Showing 1 of 2 tests")


class JenkinsLinkTests(TestCase):
    def test_stored_jenkins_links_follow_the_current_address(self):
        from .models import jenkins_link

        old = "https://192.168.50.95/job/megrez-uart-weekly/5/"
        self.assertEqual(jenkins_link(old), "/job/megrez-uart-weekly/5/")
        self.assertEqual(
            jenkins_link("https://apollo/job/x/3/artifact/logs/a%20b.log?raw=1"),
            "/job/x/3/artifact/logs/a%20b.log?raw=1",
        )
        self.assertEqual(jenkins_link("https://apollo/queue/item/42/"), "/queue/item/42/")
        self.assertEqual(jenkins_link("https://github.com/org/repo"), "https://github.com/org/repo")
        self.assertEqual(jenkins_link(""), "")
        with override_settings(JENKINS_PUBLIC_URL="https://110.93.227.10:9123/"):
            self.assertEqual(
                jenkins_link(old), "https://110.93.227.10:9123/job/megrez-uart-weekly/5/"
            )

    def test_run_page_rewrites_build_artifact_and_log_links(self):
        user = get_user_model().objects.create_user("viewer", password="safe-test-password")
        board = Board.objects.create(slug="milkv-megrez", name="Milk-V Megrez")
        job = JenkinsJob.objects.create(board=board, name="megrez-uart-weekly")
        run = TestRun.objects.create(
            job=job,
            build_number=5,
            metadata={"build_url": "https://192.168.50.95/job/megrez-uart-weekly/5/"},
        )
        TestResult.objects.create(
            run=run,
            test_case=ACTTestCase.objects.create(name="H_trap-00"),
            log_path="https://192.168.50.95/job/megrez-uart-weekly/5/artifact/h.log",
        )
        Artifact.objects.create(
            run=run,
            name="summary.md",
            relative_path="summary.md",
            external_url="https://192.168.50.95/job/megrez-uart-weekly/5/artifact/summary.md",
        )
        self.client.force_login(user)
        page = self.client.get(run.get_absolute_url())
        self.assertNotContains(page, "192.168.50.95")
        self.assertContains(page, 'href="/job/megrez-uart-weekly/5/"')
        self.assertContains(page, 'href="/job/megrez-uart-weekly/5/artifact/h.log"')
        self.assertContains(page, 'href="/job/megrez-uart-weekly/5/artifact/summary.md"')
