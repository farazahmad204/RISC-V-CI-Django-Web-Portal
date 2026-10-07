"""Check that an uploaded ELF can be loaded by the runner on a given board.

The runner (act_core.c) accepts a PT_LOAD segment only if its load address
(p_paddr, or p_vaddr when p_paddr is 0) and its end lie inside the board's
payload window, and it does not overlap the runner image or the UART staging
buffer. Each board's rules live in Board.profile["elf_load_rules"]:

    {"window": ["0x90000000", "0xB0000000"],
     "reserved": [["0x80000000", "0x80080000", "runner"], ...]}
"""

from __future__ import annotations

import struct

PT_LOAD = 1


class ElfCheckError(ValueError):
    """The ELF cannot run on the selected board."""


def _int(value) -> int:
    return value if isinstance(value, int) else int(str(value), 0)


def load_segments(data: bytes) -> list[tuple[int, int]]:
    """Return (start, end) of every non-empty PT_LOAD segment of an ELF64 LE file."""
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ElfCheckError("The uploaded file is not a little-endian ELF64 binary.")
    (phoff,) = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    if phentsize < 56 or phoff + phentsize * phnum > len(data):
        raise ElfCheckError("The ELF program header table is truncated.")
    segments = []
    for i in range(phnum):
        base = phoff + i * phentsize
        (p_type,) = struct.unpack_from("<I", data, base)
        if p_type != PT_LOAD:
            continue
        vaddr, paddr, filesz, memsz = struct.unpack_from("<QQQQ", data, base + 0x10)
        if filesz == 0 and memsz == 0:
            continue
        start = paddr or vaddr
        segments.append((start, start + memsz))
    if not segments:
        raise ElfCheckError("The ELF has no loadable segments.")
    return segments


def board_rules(board) -> dict | None:
    rules = (board.profile or {}).get("elf_load_rules") if isinstance(board.profile, dict) else None
    if not rules or "window" not in rules:
        return None
    window = tuple(_int(v) for v in rules["window"])
    reserved = [
        (_int(r[0]), _int(r[1]), str(r[2]) if len(r) > 2 else "reserved")
        for r in rules.get("reserved", [])
    ]
    return {"window": window, "reserved": reserved}


def problems_for(segments: list[tuple[int, int]], rules: dict) -> list[str]:
    low, high = rules["window"]
    issues = []
    for start, end in segments:
        if start < low or end > high:
            issues.append(f"segment 0x{start:x}-0x{end:x} is outside 0x{low:x}-0x{high:x}")
            continue
        for r_start, r_end, label in rules["reserved"]:
            if start < r_end and r_start < end:
                issues.append(
                    f"segment 0x{start:x}-0x{end:x} overlaps the {label} "
                    f"at 0x{r_start:x}-0x{r_end:x}"
                )
    return issues


def check_elf_for_board(data: bytes, board, other_boards=()) -> list[tuple[int, int]]:
    """Raise ElfCheckError with a readable reason if the ELF cannot run on board."""
    rules = board_rules(board)
    if rules is None:
        return []  # no window recorded for this board: nothing to check against
    segments = load_segments(data)
    issues = problems_for(segments, rules)
    if issues:
        fits = [
            b.name
            for b in other_boards
            if b.pk != board.pk and (r := board_rules(b)) and not problems_for(segments, r)
        ]
        hint = f" It looks built for {', '.join(fits)}." if fits else ""
        low, high = rules["window"]
        raise ElfCheckError(
            f"This ELF does not fit {board.name}: {issues[0]}. {board.name} runs ELFs linked "
            f"inside 0x{low:x}-0x{high:x}.{hint} Nothing was uploaded to the board."
        )
    return segments
