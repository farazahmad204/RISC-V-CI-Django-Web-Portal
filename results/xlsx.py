# ruff: noqa: E501  (fixed OOXML strings read better unwrapped)
"""Minimal XLSX writer (standard library only) for the run workbook export.

A sheet is (name, rows, options). A row is a list of cells; a cell is a value
(str, int, float or None) or (value, style). Styles: "header", "pass", "fail".
Options: "widths" (list of column widths), "freeze" (freeze the first row and add
an autofilter on it).
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

_STYLE_IDS = {None: 0, "header": 1, "pass": 2, "fail": 3}
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHEET_NAME = re.compile(r"[\[\]:*?/\\]")

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>\
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
<fills count="5"><fill><patternFill patternType="none"/></fill>\
<fill><patternFill patternType="gray125"/></fill>\
<fill><patternFill patternType="solid"><fgColor rgb="FF1F3A5F"/><bgColor indexed="64"/></patternFill></fill>\
<fill><patternFill patternType="solid"><fgColor rgb="FFC6EFCE"/><bgColor indexed="64"/></patternFill></fill>\
<fill><patternFill patternType="solid"><fgColor rgb="FFFFC7CE"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4">\
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>\
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>\
<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top"/></xf>\
<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top"/></xf>\
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _cell_xml(ref: str, cell) -> str:
    value, style = cell if isinstance(cell, tuple) else (cell, None)
    s = f' s="{_STYLE_IDS[style]}"' if _STYLE_IDS[style] else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        text = escape(_ILLEGAL.sub("", str(value))[:32000])
        return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
    return f'<c r="{ref}"{s}><v>{value}</v></c>'


def _sheet_xml(rows: list[list], options: dict) -> str:
    width = max((len(row) for row in rows), default=1)
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    ]
    if options.get("freeze"):
        parts.append(
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
            'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        )
    widths = options.get("widths") or []
    if widths:
        parts.append("<cols>")
        for index, column_width in enumerate(widths, 1):
            parts.append(
                f'<col min="{index}" max="{index}" width="{column_width}" customWidth="1"/>'
            )
        parts.append("</cols>")
    parts.append("<sheetData>")
    for row_index, row in enumerate(rows, 1):
        cells = "".join(
            _cell_xml(f"{_column_letter(i)}{row_index}", cell) for i, cell in enumerate(row)
        )
        parts.append(f'<row r="{row_index}">{cells}</row>')
    parts.append("</sheetData>")
    if options.get("freeze") and rows:
        parts.append(f'<autoFilter ref="A1:{_column_letter(width - 1)}{len(rows)}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


def _sheet_names(sheets) -> list[str]:
    names, seen = [], set()
    for name, _rows, _options in sheets:
        base = _SHEET_NAME.sub(" ", name).strip()[:31] or "Sheet"
        candidate, counter = base, 2
        while candidate.lower() in seen:
            suffix = f" ({counter})"
            candidate, counter = base[: 31 - len(suffix)] + suffix, counter + 1
        seen.add(candidate.lower())
        names.append(candidate)
    return names


def build_xlsx(sheets: list[tuple[str, list[list], dict]]) -> bytes:
    names = _sheet_names(sheets)
    sheet_entries = "".join(
        f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
        for i, name in enumerate(names, 1)
    )
    defined = "".join(
        f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
        f"'{escape(name)}'!$A$1:${_column_letter(max(len(r) for r in rows) - 1)}${len(rows)}"
        "</definedName>"
        for i, (name, (_n, rows, options)) in enumerate(zip(names, sheets))
        if options.get("freeze") and rows
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheet_entries}</sheets>"
        + (f"<definedNames>{defined}</definedNames>" if defined else "")
        + "</workbook>"
    )
    relationships = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    ) + (
        f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        + "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{relationships}</Relationships>",
        )
        archive.writestr("xl/styles.xml", _STYLES)
        for i, (_name, rows, options) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows, options))
    return buffer.getvalue()
