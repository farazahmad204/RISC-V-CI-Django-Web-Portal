// Run ELF: check the chosen ELF against the selected board before anything is uploaded.
// Same rules as results/elf_check.py (the server repeats the check): every non-empty
// PT_LOAD segment (p_paddr, or p_vaddr when 0) must lie inside the board's ELF window and
// must not overlap a reserved range.
(() => {
  const rulesNode = document.getElementById("elf-board-rules");
  const boardSelect = document.querySelector("[data-elf-board]");
  const fileInput = document.querySelector("[data-elf-file]");
  const result = document.querySelector("[data-elf-result]");
  const submit = document.querySelector("[data-elf-submit]");
  if (!rulesNode || !boardSelect || !fileInput || !result || !submit) return;
  const rules = JSON.parse(rulesNode.textContent || "{}");
  let segments = null;
  let parseError = "";

  const hex = (v) => "0x" + v.toString(16);

  function readSegments(buffer) {
    const view = new DataView(buffer);
    const magic = buffer.byteLength >= 64 &&
      view.getUint8(0) === 0x7f && view.getUint8(1) === 0x45 && view.getUint8(2) === 0x4c && view.getUint8(3) === 0x46;
    if (!magic || view.getUint8(4) !== 2 || view.getUint8(5) !== 1) {
      throw new Error("This is not a little-endian ELF64 file.");
    }
    if (view.getUint16(18, true) !== 243) throw new Error("This ELF is not for RISC-V.");
    const phoff = Number(view.getBigUint64(0x20, true));
    const phentsize = view.getUint16(0x36, true);
    const phnum = view.getUint16(0x38, true);
    if (phentsize < 56 || phoff + phentsize * phnum > buffer.byteLength) {
      throw new Error("The ELF program header table is truncated.");
    }
    const found = [];
    for (let i = 0; i < phnum; i++) {
      const base = phoff + i * phentsize;
      if (view.getUint32(base, true) !== 1) continue;  // PT_LOAD
      const vaddr = view.getBigUint64(base + 0x10, true);
      const paddr = view.getBigUint64(base + 0x18, true);
      const filesz = view.getBigUint64(base + 0x20, true);
      const memsz = view.getBigUint64(base + 0x28, true);
      if (filesz === 0n && memsz === 0n) continue;
      const start = paddr !== 0n ? paddr : vaddr;
      found.push([start, start + memsz]);
    }
    if (!found.length) throw new Error("The ELF has no loadable segments.");
    return found;
  }

  function problems(segs, rule) {
    const low = BigInt(rule.window[0]);
    const high = BigInt(rule.window[1]);
    const issues = [];
    for (const [start, end] of segs) {
      if (start < low || end > high) {
        issues.push(`segment ${hex(start)}-${hex(end)} is outside ${rule.window[0]}-${rule.window[1]}`);
        continue;
      }
      for (const [rs, re, label] of rule.reserved || []) {
        if (start < BigInt(re) && BigInt(rs) < end) {
          issues.push(`segment ${hex(start)}-${hex(end)} overlaps the ${label} at ${rs}-${re}`);
        }
      }
    }
    return issues;
  }

  function show(message, ok) {
    result.hidden = !message;
    result.textContent = message;
    result.className = "elf-check " + (ok ? "elf-check-ok" : "elf-check-error");
    submit.disabled = !ok;
  }

  function evaluate() {
    const boardId = boardSelect.value;
    if (parseError) return show(parseError, false);
    if (!segments || !boardId) { result.hidden = true; submit.disabled = false; return; }
    const rule = rules[boardId];
    if (!rule) return show("", true);  // no rules recorded for this board: the server decides
    if (rule.state === "offline") return show(`${rule.name} is offline; choose another board.`, false);
    const issues = problems(segments, rule);
    if (!issues.length) {
      const lo = segments.reduce((a, s) => (s[0] < a ? s[0] : a), segments[0][0]);
      const hi = segments.reduce((a, s) => (s[1] > a ? s[1] : a), segments[0][1]);
      return show(`ELF loads at ${hex(lo)}-${hex(hi)}: fits ${rule.name}.`, true);
    }
    const fits = Object.entries(rules)
      .filter(([id, r]) => id !== boardId && !problems(segments, r).length)
      .map(([, r]) => r.name);
    const hint = fits.length ? ` It looks built for ${fits.join(", ")}.` : "";
    show(`This ELF does not fit ${rule.name}: ${issues[0]}.${hint} Choose the right board or ELF.`, false);
  }

  fileInput.addEventListener("change", () => {
    segments = null;
    parseError = "";
    const file = fileInput.files && fileInput.files[0];
    if (!file) return evaluate();
    file.arrayBuffer().then((buffer) => {
      try { segments = readSegments(buffer); } catch (err) { parseError = err.message; }
      evaluate();
    });
  });
  boardSelect.addEventListener("change", evaluate);
})();
