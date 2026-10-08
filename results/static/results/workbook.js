// Run page: filters, and in-place editing of Verdict and analysis cells (editors only).
// Execution results are not editable.
(() => {
  const table = document.querySelector("table[data-workbook][data-save-url]");
  // A second horizontal scrollbar above the table, kept in step with the table's own one.
  const mirror = document.querySelector("[data-scroll-mirror]");
  const source = document.querySelector("[data-scroll-source]");
  if (mirror && source) {
    const spacer = mirror.firstElementChild;
    const size = () => {
      spacer.style.width = `${source.scrollWidth}px`;
      mirror.hidden = source.scrollWidth <= source.clientWidth + 1;
    };
    const follow = (from, to) => () => {
      if (to.scrollLeft !== from.scrollLeft) to.scrollLeft = from.scrollLeft;
    };
    mirror.addEventListener("scroll", follow(mirror, source));
    source.addEventListener("scroll", follow(source, mirror));
    window.addEventListener("resize", size);
    if (window.ResizeObserver) new ResizeObserver(size).observe(source.querySelector("table") || source);
    size();
  }
  document.querySelectorAll("select[data-autosubmit]").forEach((select) => {
    select.addEventListener("change", () => select.form.submit());
  });
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  document.querySelectorAll("details.column-menu").forEach((menu) => {
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      document.querySelectorAll("details.column-menu[open]").forEach((other) => {
        if (other !== menu) other.open = false;
      });
      const body = menu.querySelector(".column-menu-body");
      if (body) body.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
  });
  if (!table) return;
  const saveUrl = table.dataset.saveUrl;
  const csrfInput = document.querySelector("input[name=csrfmiddlewaretoken]");
  const csrf = csrfInput ? csrfInput.value : "";
  const choicesNode = document.getElementById("verdict-choices");
  const verdicts = choicesNode ? JSON.parse(choicesNode.textContent) : [];
  let openCell = null;

  const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  function display(cell, value) {
    cell.replaceChildren();
    if (!value) {
      const empty = document.createElement("span");
      empty.className = "cell-empty";
      empty.textContent = "—";
      cell.append(empty);
    } else if ("verdict" in cell.dataset) {
      const badge = document.createElement("span");
      badge.className = `verdict verdict-${slug(value)}`;
      badge.textContent = value;
      cell.append(badge);
    } else {
      const text = document.createElement("div");
      text.className = "cell-text";
      text.textContent = value;
      cell.append(text);
    }
  }

  function close() {
    if (!openCell) return;
    display(openCell, openCell.dataset.value || "");
    openCell = null;
  }

  async function save(cell, value, errorNode, buttons) {
    buttons.forEach((b) => { b.disabled = true; });
    errorNode.textContent = "";
    try {
      const response = await fetch(saveUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf },
        body: new URLSearchParams({ column: cell.dataset.column, result: cell.dataset.result, value }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `Save failed (HTTP ${response.status}).`);
      cell.dataset.value = data.value;
      cell.classList.remove("source-triage");
      cell.classList.toggle("source-person", Boolean(data.value));
      cell.title = data.value ? `Edited by ${data.updated_by} just now` : "Click to edit";
      openCell = null;
      display(cell, data.value);
    } catch (error) {
      errorNode.textContent = error.message;
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  function edit(cell) {
    close();
    openCell = cell;
    const current = cell.dataset.value || "";
    const editor = document.createElement("div");
    editor.className = "cell-editor";
    let input;
    if ("verdict" in cell.dataset) {
      input = document.createElement("select");
      for (const option of ["", ...verdicts]) {
        const node = document.createElement("option");
        node.value = option;
        node.textContent = option || "— no verdict —";
        node.selected = option === current;
        input.append(node);
      }
    } else {
      input = document.createElement("textarea");
      input.rows = Math.min(8, Math.max(3, current.split("\n").length));
      input.maxLength = 4000;
      input.value = current;
    }
    const actions = document.createElement("div");
    actions.className = "cell-editor-actions";
    const saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.className = "button";
    saveButton.textContent = "Save";
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.className = "button secondary";
    cancelButton.textContent = "Cancel";
    const errorNode = document.createElement("span");
    errorNode.className = "cell-editor-error";
    actions.append(saveButton, cancelButton);
    editor.append(input, actions, errorNode);
    cell.replaceChildren(editor);
    input.focus();

    const buttons = [saveButton, cancelButton];
    saveButton.addEventListener("click", () => save(cell, input.value.trim(), errorNode, buttons));
    cancelButton.addEventListener("click", close);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        save(cell, input.value.trim(), errorNode, buttons);
      }
    });
  }

  table.addEventListener("click", (event) => {
    const cell = event.target.closest("td.editable");
    if (!cell || cell === openCell) return;
    edit(cell);
  });
})();
