import argparse
import base64
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from tools.publish_jenkins_results import (
    build_payload,
    normalize_status,
    read_artifact_categories,
    read_xlsx_categories,
)


def write_inventory_workbook(path, privileged, non_privileged=()):
    workbook_xml = """<?xml version="1.0"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets>
        <sheet name="Privileged Tests" sheetId="1" r:id="rId1"/>
        <sheet name="Non-Privileged Tests" sheetId="2" r:id="rId2"/>
      </sheets>
    </workbook>"""
    relationships_xml = """<?xml version="1.0"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
      <Relationship Id="rId2" Target="worksheets/sheet2.xml"/>
    </Relationships>"""

    def sheet_xml(test_names):
        rows = [
            '<row r="1"><c r="A1" t="inlineStr"><is><t>Test Name</t></is></c></row>'
        ]
        rows.extend(
            f'<row r="{number}"><c r="A{number}" t="inlineStr"><is><t>{name}</t></is></c></row>'
            for number, name in enumerate(test_names, 2)
        )
        return (
            '<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(rows)}</sheetData></worksheet>"
        )

    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml(privileged))
        archive.writestr("xl/worksheets/sheet2.xml", sheet_xml(non_privileged))


class PublisherTests(unittest.TestCase):
    def test_status_aliases(self):
        self.assertEqual(normalize_status("SUCCESS"), "PASS")
        self.assertEqual(normalize_status("not run"), "SKIPPED")
        self.assertEqual(normalize_status("unexpected"), "UNKNOWN")

    def test_merges_reference_and_hardware_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_root = root / "state"
            run_root = root / "run"
            artifact_root = root / "artifacts"
            state_root.mkdir()
            run_root.mkdir()
            (state_root / "state.env").write_text(
                "RUN_ID=jenkins_weekly_7\nEXPECTED_CASES=2\nACT_REVISION=abc123\n"
                f"ARTIFACT_ROOT={artifact_root}\n"
            )
            (state_root / "sail_reference_status.tsv").write_text(
                "test_name\tsail_status\thardware_elf\nExceptionsM-01\tPASS\tyes\n"
            )
            (state_root / "spike_status.tsv").write_text("ExceptionsM-01\tPASS\n")
            (run_root / "cases.json").write_text(
                json.dumps(
                    [
                        {
                            "test_name": "ExceptionsM-01",
                            "status": "FAIL",
                            "root_cause": "Signature mismatch",
                            "report": "per_case/ExceptionsM-01/report.md",
                        }
                    ]
                )
            )
            uart_path = run_root / "per_case" / "ExceptionsM-01" / "uart.log"
            uart_path.parent.mkdir(parents=True)
            uart_path.write_bytes(b"UART output\n")
            triage_path = run_root / "triage" / "per_case" / "ExceptionsM-01"
            triage_path.mkdir(parents=True)
            (run_root / "triage" / "summary.json").write_text(
                json.dumps({"ai_model": "test-model"})
            )
            (triage_path / "evidence.json").write_text(
                json.dumps(
                    {
                        "deterministic_category": "trap_cause_mismatch",
                        "deterministic_owner": "Needs architectural review",
                        "deterministic_explanation": "Expected 15, observed 6.",
                        "extracted": {"expected_value": "0xf", "actual_value": "0x6"},
                    }
                )
            )
            (triage_path / "ai_analysis.md").write_text("## Finding\nNeeds review.\n")
            elf_path = artifact_root / "priv" / "ExceptionsM" / "ExceptionsM-01.sig.elf"
            elf_path.parent.mkdir(parents=True)
            elf_path.touch()
            inventory_path = root / "inventory.xlsx"
            write_inventory_workbook(
                inventory_path,
                ["ExceptionsM-01", "InterruptsM-01"],
                ["I-add-01"],
            )
            args = argparse.Namespace(
                state_root=state_root,
                run_root=run_root,
                board_slug="vf2",
                board_name="VisionFive 2",
                core_profile="SiFive U74",
                job_name="vf2-privileged-weekly",
                build_number=7,
                build_url="https://jenkins/job/vf2-privileged-weekly/7/",
                status="AUTO",
                started_at="",
                finished_at="",
                suite_inventory_xlsx=inventory_path,
            )
            payload = build_payload(args)
        self.assertEqual(payload["status"], "RUNNING")
        self.assertEqual(payload["failed_cases"], 1)
        self.assertEqual(payload["results"][0]["sail_status"], "PASS")
        self.assertEqual(payload["results"][0]["spike_status"], "PASS")
        self.assertEqual(payload["results"][0]["hardware_status"], "FAIL")
        self.assertEqual(payload["results"][0]["category"], "Privileged")
        self.assertEqual(payload["results"][0]["triage_category"], "trap_cause_mismatch")
        self.assertEqual(payload["results"][0]["ai_status"], "SUCCESS")
        self.assertEqual(payload["results"][0]["ai_model"], "test-model")
        self.assertEqual(
            gzip.decompress(base64.b64decode(payload["results"][0]["uart_log_gzip_b64"])),
            b"UART output\n",
        )
        by_name = {item["name"]: item for item in payload["results"]}
        self.assertEqual(by_name["InterruptsM-01"]["hardware_status"], "UNKNOWN")
        self.assertEqual(by_name["InterruptsM-01"]["category"], "Privileged")
        self.assertEqual(by_name["I-add-01"]["category"], "Non-Privileged")

    def test_reads_suite_membership_from_status_workbook(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "matrix.xlsx")
            write_inventory_workbook(path, ["ExceptionsM-01"], ["I-add-01"])
            categories = read_xlsx_categories(path)

        self.assertEqual(categories["ExceptionsM-01"], "Privileged")
        self.assertEqual(categories["I-add-01"], "Non-Privileged")

    def test_reads_suite_membership_from_act_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = {
                "priv/ExceptionsM/ExceptionsM-01.sig.elf": "Privileged",
                "rv64i/I/I-add-01.sig.elf": "Non-Privileged",
                "rv64v/V/V-add-01.sig.elf": "Vector",
            }
            for relative_path in artifacts:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            categories = read_artifact_categories(root)

        self.assertEqual(
            categories,
            {
                "ExceptionsM-01": "Privileged",
                "I-add-01": "Non-Privileged",
                "V-add-01": "Vector",
            },
        )

    def test_priv_scope_excludes_full_inventory_and_forces_privileged_category(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_root = root / "state"
            run_root = root / "run"
            artifact_root = root / "build" / "priv"
            state_root.mkdir()
            run_root.mkdir()
            elf = artifact_root / "ExceptionsM" / "ExceptionsM-01.sig.elf"
            elf.parent.mkdir(parents=True)
            elf.touch()
            excluded_elf = artifact_root / "InterruptsM" / "InterruptsM-01.sig.elf"
            excluded_elf.parent.mkdir(parents=True)
            excluded_elf.touch()
            (state_root / "state.env").write_text(
                "RUN_ID=priv_run\nTEST_SCOPE=priv\nEXPECTED_CASES=1\n"
                f"ARTIFACT_ROOT={artifact_root}\n"
            )
            (state_root / "sail_reference_status.tsv").write_text(
                "test_name\tsail_status\nExceptionsM-01\tPASS\n"
            )
            (run_root / "cases.json").write_text(
                json.dumps([{"test_name": "ExceptionsM-01", "status": "PASS"}])
            )
            inventory_path = root / "inventory.xlsx"
            write_inventory_workbook(
                inventory_path,
                ["ExceptionsM-01", "InterruptsM-01"],
                ["I-add-01"],
            )
            args = argparse.Namespace(
                state_root=state_root,
                run_root=run_root,
                board_slug="vf2",
                board_name="VisionFive 2",
                core_profile="SiFive U74",
                job_name="vf2-uart-weekly",
                build_number=1,
                build_url="https://jenkins/job/vf2-uart-weekly/1/",
                status="AUTO",
                started_at="",
                finished_at="",
                suite_inventory_xlsx=inventory_path,
            )
            payload = build_payload(args)

        self.assertEqual([item["name"] for item in payload["results"]], ["ExceptionsM-01"])
        self.assertEqual(payload["results"][0]["category"], "Privileged")
        self.assertEqual(payload["passed_cases"], 1)


if __name__ == "__main__":
    unittest.main()
