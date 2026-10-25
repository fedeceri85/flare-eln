document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("[data-litter-procedure-form]");
    const stored = document.getElementById("id_procedure_drafts");
    if (!form || !stored) return;
    const importing = form.dataset?.procedureScope === "import";
    const scope = importing ? "import" : "litter";
    const names = ["procedure_type", "date", "lab_member", "lab_book_page"];
    const fields = Object.fromEntries(names.map(name => [name, form.elements.namedItem("new_procedure-" + name)]));
    const rows = document.querySelector("[data-litter-procedures-rows]");
    const table = document.querySelector("[data-litter-procedures-table]");
    const empty = document.querySelector("[data-litter-procedures-empty]");
    const add = form.querySelector("[data-litter-procedure-add]");
    const cancel = form.querySelector("[data-litter-procedure-cancel]");
    const status = form.querySelector("[data-litter-procedure-status]");
    let drafts = [];
    let editing = null;
    try {
        const parsed = JSON.parse(stored.value || "[]");
        if (Array.isArray(parsed) && parsed.every(item => item && typeof item === "object" && !Array.isArray(item))) drafts = parsed;
        else status.textContent = "The procedure list could not be loaded. Add the procedures again.";
    } catch (_) {
        status.textContent = "The procedure list could not be loaded. Add the procedures again.";
    }
    function reset() {
        form.reset();
        editing = null;
        add.textContent = "Add procedure";
        cancel.hidden = true;
    }
    function action(label, callback) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = label === "Remove" ? "table-row-action table-row-action--danger" : "table-row-action";
        button.textContent = label;
        button.addEventListener("click", callback);
        return button;
    }
    function render(notify = false) {
        rows.replaceChildren();
        drafts.forEach((draft, index) => {
            const row = document.createElement("tr");
            names.forEach(name => {
                const cell = document.createElement("td");
                const option = name === "procedure_type" && Array.from(fields.procedure_type.options).find(option => option.value === String(draft[name]));
                cell.textContent = name === "procedure_type" ? (option ? option.textContent : "Unavailable procedure type") : (draft[name] || "—");
                row.appendChild(cell);
            });
            const actions = document.createElement("td");
            actions.className = "action-cell";
            actions.appendChild(action("Edit", () => {
                names.forEach(name => { fields[name].value = draft[name] || ""; });
                editing = index;
                add.textContent = "Update procedure";
                cancel.hidden = false;
                status.textContent = "Editing procedure " + (index + 1) + ". Update it to apply your changes.";
                fields.procedure_type.focus();
            }));
            actions.appendChild(action("Remove", () => {
                if (!window.confirm("Remove this procedure from the " + scope + "?")) return;
                drafts.splice(index, 1);
                reset();
                render(true);
                status.textContent = "Procedure removed from the list.";
            }));
            row.appendChild(actions);
            rows.appendChild(row);
        });
        stored.value = JSON.stringify(drafts);
        table.hidden = drafts.length === 0;
        empty.hidden = drafts.length !== 0;
        if (notify && importing) stored.dispatchEvent(new Event("input", {bubbles: true}));
    }
    form.addEventListener("submit", event => {
        event.preventDefault();
        if (form.querySelector("[data-procedure-fieldset]").disabled || !form.reportValidity()) return;
        if (editing === null && drafts.length >= 100) {
            status.textContent = "You can add up to 100 procedures per " + scope + ".";
            return;
        }
        const draft = Object.fromEntries(names.map(name => [name, fields[name].value]));
        const updated = editing !== null;
        if (updated) drafts[editing] = draft;
        else drafts.push(draft);
        reset();
        render(true);
        const save = importing ? "complete the import" : "save the litter";
        status.textContent = updated ? "Procedure updated. " + save[0].toUpperCase() + save.slice(1) + " to create these records." : "Procedure added. Add another, or " + save + " to create these records.";
    });
    cancel.querySelector("button").addEventListener("click", () => { reset(); status.textContent = "Editing cancelled."; });
    render();
});
