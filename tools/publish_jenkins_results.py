#!/usr/bin/env python3
"""Merge ACT Jenkins outputs and publish one build to the results portal."""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import ssl
import sys
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

STATUS_MAP = {
    "SUCCESS": "PASS",
    "PASSED": "PASS",
    "PASS": "PASS",
    "FAILURE": "FAIL",
    "FAILED": "FAIL",
    "FAIL": "FAIL",
    "UNSTABLE": "UNSTABLE",
    "ABORTED": "ABORTED",
    "RUNNING": "RUNNING",
    "QUEUED": "QUEUED",
    "SKIP": "SKIPPED",
    "SKIPPED": "SKIPPED",
    "NOT RUN": "SKIPPED",
    "NOT_RUN": "SKIPPED",
}

SUITE_SHEETS = {
    "Privileged Tests": "Privileged",
    "Non-Privileged Tests": "Non-Privileged",
    "Vector Tests": "Vector",
    "Hypervisor Tests": "Hypervisor",
}
# Privileged suites that exercise the H extension (H*, *H, *H<suffix>) and the
# Sh* profile extensions. Kept in step with HYPERVISOR_SUITE_REGEX in the
# runner's ci/jenkins/weekly_vf2.sh (TEST_SCOPE=hypervisor).
HYPERVISOR_SUITE = re.compile(r"^(H|Sh)|H(F|V|Gei|Sm|Zicbo|ZicboSm)?$")
PRIVILEGED_CATEGORIES = {"Privileged", "Hypervisor"}
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--board-slug", required=True)
    parser.add_argument("--board-name", required=True)
    parser.add_argument("--core-profile", default="")
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--build-number", type=int, required=True)
    parser.add_argument("--build-url", default="")
    parser.add_argument("--status", default="AUTO")
    parser.add_argument("--started-at", default="")
    parser.add_argument("--finished-at", default="")
    parser.add_argument("--portal-url", default="https://192.168.100.150/portal/")
    parser.add_argument("--token", default=os.getenv("PORTAL_INGEST_TOKEN", ""))
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument(
        "--suite-inventory-xlsx",
        type=Path,
        help="Optional baseline workbook whose suite tabs define the expected test inventory",
    )
    parser.add_argument(
        "--artifact-store",
        type=Path,
        help="Optional permanent directory on the agent for compact report artifacts",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-post", action="store_true")
    return parser.parse_args()


def normalize_status(value: object, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return STATUS_MAP.get(text, text if text in set(STATUS_MAP.values()) else default)


def read_state(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip("'\"")
    return values


def read_status_tsv(path: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    if not path.is_file():
        return statuses
    with path.open(newline="", encoding="utf-8", errors="replace") as stream:
        for row in csv.reader(stream, delimiter="\t"):
            if len(row) < 2 or not row[0] or row[0] == "test_name":
                continue
            statuses[row[0]] = normalize_status(row[1])
    return statuses


def read_cases(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError(f"{path} must contain a JSON list")
    return {
        str(item["test_name"]): item
        for item in loaded
        if isinstance(item, dict) and item.get("test_name")
    }


def read_xlsx_categories(path: Path) -> dict[str, str]:
    """Read test membership from the report workbook without an Excel dependency."""
    if not path.is_file():
        return {}
    namespaces = {"main": SPREADSHEET_NS}
    categories: dict[str, str] = {}
    try:
        with ZipFile(path) as workbook_zip:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in workbook_zip.namelist():
                shared_root = ElementTree.fromstring(workbook_zip.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(node.text or "" for node in item.iter(f"{{{SPREADSHEET_NS}}}t"))
                    for item in shared_root.findall(f"{{{SPREADSHEET_NS}}}si")
                ]

            workbook = ElementTree.fromstring(workbook_zip.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(
                workbook_zip.read("xl/_rels/workbook.xml.rels")
            )
            targets = {
                relationship.attrib["Id"]: relationship.attrib["Target"]
                for relationship in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
            }

            for sheet in workbook.findall(".//main:sheet", namespaces):
                category = SUITE_SHEETS.get(sheet.attrib.get("name", ""))
                if not category:
                    continue
                relation_id = sheet.attrib[f"{{{OFFICE_REL_NS}}}id"]
                target = targets[relation_id].lstrip("/")
                sheet_path = target if target.startswith("xl/") else f"xl/{target}"
                sheet_root = ElementTree.fromstring(workbook_zip.read(sheet_path))
                for row in sheet_root.findall(".//main:row", namespaces)[1:]:
                    first_cell = next(
                        (
                            cell
                            for cell in row.findall("main:c", namespaces)
                            if cell.attrib.get("r", "").startswith("A")
                        ),
                        None,
                    )
                    if first_cell is None:
                        continue
                    value = ""
                    if first_cell.attrib.get("t") == "inlineStr":
                        value = "".join(
                            node.text or ""
                            for node in first_cell.iter(f"{{{SPREADSHEET_NS}}}t")
                        )
                    else:
                        value_node = first_cell.find("main:v", namespaces)
                        if value_node is not None and value_node.text:
                            value = value_node.text
                            if first_cell.attrib.get("t") == "s":
                                value = shared_strings[int(value)]
                    if value:
                        categories[value] = category
    except (BadZipFile, ElementTree.ParseError, KeyError, OSError, ValueError):
        return {}
    return categories


def privileged_category(suite_dir: str) -> str:
    return "Hypervisor" if HYPERVISOR_SUITE.search(suite_dir) else "Privileged"


def read_artifact_categories(path: Path) -> dict[str, str]:
    """Infer suite membership from ACT's build/<suite>/<extension> layout.

    ARTIFACT_ROOT is either the whole build tree or, for privileged-only scopes,
    build/priv itself; in both cases priv/<extension>/ is privileged.
    """
    if not path.is_dir():
        return {}
    root_is_priv = path.name.lower() == "priv"
    categories: dict[str, str] = {}
    for artifact in path.rglob("*.sig.elf"):
        try:
            relative = artifact.relative_to(path)
        except ValueError:
            continue
        if not relative.parts:
            continue
        suite = relative.parts[0].lower()
        if root_is_priv or suite == "priv":
            category = privileged_category(artifact.parent.name)
        elif suite in {"rv32v", "rv64v", "vector"} or suite.endswith("v"):
            category = "Vector"
        else:
            category = "Non-Privileged"
        categories[artifact.name.removesuffix(".sig.elf")] = category
    return categories


def extension_for(name: str) -> str:
    return re.sub(r"-\d+$", "", name)


def workspace_root_for(state_root: Path, run_root: Path) -> Path | None:
    for candidate in state_root.parents:
        try:
            state_root.relative_to(candidate / "logs")
            run_root.relative_to(candidate / "logs")
            return candidate
        except ValueError:
            continue
    return None


def jenkins_artifact_url(build_url: str, relative_path: Path) -> str:
    encoded = "/".join(quote(part, safe="") for part in relative_path.parts)
    return f"{build_url.rstrip('/')}/artifact/{encoded}" if build_url else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_artifacts(
    args: argparse.Namespace,
    state_root: Path,
    run_root: Path,
    run_id: str,
) -> list[dict]:
    workspace = workspace_root_for(state_root, run_root)
    candidates = [
        (state_root / "test_status_matrix.xlsx", "excel"),
        (state_root / "tracking_sheet_comparison.csv", "comparison"),
        (state_root / "site" / "downloads" / f"{run_id}-complete.zip", "archive"),
        (run_root / "summary.md", "summary"),
        (run_root / "cases.csv", "results"),
        (run_root / "cases.json", "results"),
        (run_root / "uart_capture.log", "uart"),
    ]
    destination_root = None
    artifact_store = getattr(args, "artifact_store", None)
    if artifact_store:
        safe_job = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.job_name)
        destination_root = artifact_store.resolve() / safe_job / str(args.build_number)
        destination_root.mkdir(parents=True, exist_ok=True)

    artifacts = []
    for source, kind in candidates:
        if not source.is_file():
            continue
        stored_path = ""
        if destination_root:
            destination = destination_root / source.name
            shutil.copy2(source, destination)
            stored_path = str(destination)
        relative = None
        if workspace:
            try:
                relative = source.relative_to(workspace)
            except ValueError:
                pass
        artifacts.append(
            {
                "name": source.name,
                "relative_path": stored_path or str(relative or source.name),
                "external_url": (
                    jenkins_artifact_url(args.build_url, relative) if relative else ""
                ),
                "kind": kind,
                "size_bytes": source.stat().st_size,
                "sha256": sha256(source),
            }
        )
    return artifacts


def retain_case_uart_logs(args: argparse.Namespace, run_root: Path, cases: dict[str, dict]):
    artifact_store = getattr(args, "artifact_store", None)
    if not artifact_store:
        return
    safe_job = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.job_name)
    destination_root = (
        artifact_store.resolve() / safe_job / str(args.build_number) / "per_case"
    )
    for name in cases:
        source = run_root / "per_case" / name / "uart.log"
        if not source.is_file():
            continue
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        destination = destination_root / safe_name / "uart.log"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def build_payload(args: argparse.Namespace) -> dict:
    state_root = args.state_root.resolve()
    run_root = args.run_root.resolve()
    state = read_state(state_root / "state.env")
    manifest = read_state(state_root / "jenkins_manifest.txt")
    sail = read_status_tsv(state_root / "sail_reference_status.tsv")
    spike = read_status_tsv(state_root / "spike_status.tsv")
    cases = read_cases(run_root / "cases.json")
    retain_case_uart_logs(args, run_root, cases)
    artifact_root = state.get("ARTIFACT_ROOT", "")
    artifact_categories = read_artifact_categories(Path(artifact_root)) if artifact_root else {}
    inventory_categories = (
        read_xlsx_categories(args.suite_inventory_xlsx.resolve())
        if getattr(args, "suite_inventory_xlsx", None)
        else {}
    )
    categories = {
        **inventory_categories,
        **artifact_categories,
        **read_xlsx_categories(state_root / "test_status_matrix.xlsx"),
    }
    observed_names = set(sail) | set(spike) | set(cases) | set(artifact_categories)
    test_scope = state.get("TEST_SCOPE", "").strip().lower()
    if test_scope in {"priv", "all", "unpriv", "hypervisor"}:
        # A job may be given a broader inventory than its Sail-runnable hardware
        # plan. Use inventories and unselected artifacts only as metadata; they
        # must not add tests that were excluded from this execution.
        selected_names = (
            set(spike)
            | set(cases)
            | {name for name, status in sail.items() if status != "UNKNOWN"}
        )
        names = sorted(selected_names or set(artifact_categories), key=str.casefold)
        if test_scope == "priv":
            categories.update(
                {
                    name: privileged_category(extension_for(name))
                    if categories.get(name) not in PRIVILEGED_CATEGORIES
                    else categories[name]
                    for name in names
                }
            )
        elif test_scope == "hypervisor":
            categories.update({name: "Hypervisor" for name in names})
    else:
        names = sorted(observed_names | set(categories), key=str.casefold)
    run_id = state.get("RUN_ID", run_root.name)

    results = []
    hardware_counts: Counter[str] = Counter()
    for name in names:
        case = cases.get(name, {})
        category = categories.get(name, "")
        uart_path = run_root / "per_case" / name / "uart.log"
        hardware_status = normalize_status(case.get("status"))
        if name in cases:
            hardware_counts[hardware_status] += 1
        result = {
            "name": name,
            "category": category,
            "extension": extension_for(name),
            "sail_status": sail.get(name, normalize_status(case.get("sail_status"))),
            "spike_status": spike.get(name, "UNKNOWN"),
            "hardware_status": hardware_status,
            "failure_reason": (
                str(case.get("root_cause", "")) if hardware_status == "FAIL" else ""
            ),
            "log_path": (
                jenkins_artifact_url(
                    args.build_url,
                    Path("logs") / "runs" / run_id / "per_case" / name / "uart.log",
                )
                if uart_path.is_file()
                else ""
            ),
        }
        if category in PRIVILEGED_CATEGORIES and uart_path.is_file():
            result["uart_log_gzip_b64"] = base64.b64encode(
                gzip.compress(uart_path.read_bytes())
            ).decode("ascii")
        results.append(result)

    expected = int(state.get("EXPECTED_CASES", "0") or 0)
    completed = len(cases)
    requested_status = args.status.strip().upper()
    if requested_status == "AUTO":
        if not cases or (expected and completed < expected):
            run_status = "RUNNING"
        elif hardware_counts["FAIL"]:
            run_status = "UNSTABLE"
        else:
            run_status = "PASS"
    else:
        run_status = normalize_status(requested_status)

    payload = {
        "board": {
            "slug": args.board_slug,
            "name": args.board_name,
            "core_profile": args.core_profile,
        },
        "job": {
            "name": args.job_name,
            "jenkins_url": args.build_url.rsplit("/", 2)[0] + "/" if args.build_url else "",
        },
        "build_number": args.build_number,
        "status": run_status,
        "expected_cases": expected or len(names),
        "completed_cases": completed,
        "passed_cases": hardware_counts["PASS"],
        "failed_cases": hardware_counts["FAIL"],
        "skipped_cases": hardware_counts["SKIPPED"],
        "git_revision": manifest.get("git_head", ""),
        "act_revision": state.get("ACT_REVISION", ""),
        "parameters": {"test_scope": state.get("TEST_SCOPE", "")},
        "metadata": {
            "run_id": run_id,
            "run_kind": state.get("RUN_KIND", ""),
            "build_url": args.build_url,
            "sail_version": manifest.get("sail_version", ""),
            "persistent_agent_root": str(args.artifact_store.resolve())
            if getattr(args, "artifact_store", None)
            else "",
        },
        "results": results,
        "artifacts": collect_artifacts(args, state_root, run_root, run_id),
    }
    if args.started_at:
        payload["started_at"] = args.started_at
    if args.finished_at:
        payload["finished_at"] = args.finished_at
    elif manifest.get("completed_utc"):
        payload["finished_at"] = manifest["completed_utc"]
    return payload


def publish(payload: dict, args: argparse.Namespace) -> dict:
    if not args.token:
        raise ValueError("No token supplied; use --token or PORTAL_INGEST_TOKEN")
    endpoint = args.portal_url.rstrip("/") + "/api/v1/runs/"
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Portal-Token": args.token},
        method="POST",
    )
    context = ssl.create_default_context(cafile=str(args.ca_file) if args.ca_file else None)
    try:
        with urlopen(request, context=context, timeout=60) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Portal returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach portal: {exc.reason}") from exc


def main() -> int:
    args = parse_args()
    try:
        payload = build_payload(args)
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        if args.no_post:
            print(rendered)
        else:
            response = publish(payload, args)
            print(json.dumps(response, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
