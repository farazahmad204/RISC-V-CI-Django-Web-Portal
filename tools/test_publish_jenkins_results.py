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

    def test_hypervisor_suites_get_their_own_category(self):
        artifacts = {
            "priv/ExceptionsH/ExceptionsH_ecall-00.sig.elf": "Hypervisor",
            "priv/ExceptionsHSm/ExceptionsHSm_tsr-00.sig.elf": "Hypervisor",
            "priv/H/H_trap-00.sig.elf": "Hypervisor",
            "priv/SvHZicbo/SvH_sv39x4_cbo-00.sig.elf": "Hypervisor",
            "priv/InterruptsHGei/InterruptsHGei_m-00.sig.elf": "Hypervisor",
            "priv/Shcounterenw/Shcounterenw-00.sig.elf": "Hypervisor",
            "priv/ExceptionsSm/ExceptionsSm-00.sig.elf": "Privileged",
            "priv/ExceptionsSvSm/ExceptionsSvSm-00.sig.elf": "Privileged",
            "priv/Smstateen/Smstateen-00.sig.elf": "Privileged",
            "rv64i/I/I-add-01.sig.elf": "Non-Privileged",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "build"
            for relative_path in artifacts:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            whole_build = read_artifact_categories(root)
            priv_root = read_artifact_categories(root / "priv")

        expected = {
            Path(path).name.removesuffix(".sig.elf"): category
            for path, category in artifacts.items()
        }
        self.assertEqual(whole_build, expected)
        # Privileged-only scopes pass build/priv itself as ARTIFACT_ROOT.
        self.assertEqual(
            priv_root, {k: v for k, v in expected.items() if v != "Non-Privileged"}
        )

    def test_hypervisor_scope_publishes_selected_cases_with_uart_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_root = root / "state"
            run_root = root / "run"
            artifact_root = root / "build" / "priv"
            state_root.mkdir()
            for suite, name in (("ExceptionsH", "ExceptionsH_ecall-00"), ("SvH", "SvH_blocked-00")):
                elf = artifact_root / suite / f"{name}.sig.elf"
                elf.parent.mkdir(parents=True, exist_ok=True)
                elf.touch()
            (state_root / "state.env").write_text(
                "RUN_ID=hyp_run\nTEST_SCOPE=hypervisor\nEXPECTED_CASES=1\n"
                f"ARTIFACT_ROOT={artifact_root}\n"
            )
            (state_root / "sail_reference_status.tsv").write_text(
                "test_name\tsail_status\nExceptionsH_ecall-00\tPASS\n"
            )
            uart_log = run_root / "per_case" / "ExceptionsH_ecall-00" / "uart.log"
            uart_log.parent.mkdir(parents=True)
            uart_log.write_text("[UART_STREAM] DONE name=ExceptionsH_ecall-00 status=PASS\n")
            (run_root / "cases.json").write_text(
                json.dumps([{"test_name": "ExceptionsH_ecall-00", "status": "PASS"}])
            )
            args = argparse.Namespace(
                state_root=state_root,
                run_root=run_root,
                board_slug="milkv-megrez",
                board_name="Milk-V Megrez",
                core_profile="ESWIN EIC7700X/SiFive P550",
                job_name="megrez-uart-weekly",
                build_number=5,
                build_url="https://jenkins/job/megrez-uart-weekly/5/",
                status="AUTO",
                started_at="",
                finished_at="",
                suite_inventory_xlsx=None,
            )
            payload = build_payload(args)

        self.assertEqual([item["name"] for item in payload["results"]], ["ExceptionsH_ecall-00"])
        result = payload["results"][0]
        self.assertEqual(result["category"], "Hypervisor")
        self.assertIn("uart_log_gzip_b64", result)


if __name__ == "__main__":
    unittest.main()
