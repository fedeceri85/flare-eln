document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("[data-import-form]");
    if (!form) return;
    form.querySelectorAll("[data-import-mapping-row]").forEach(row => {
        const mode = row.querySelector("[data-import-mapping-mode]");
        const options = Array.from(row.querySelectorAll("[data-import-mapping-option]"));
        function updateMappingMode() {
            options.forEach(option => {
                const active = option.dataset.importMappingOption === mode.value;
                option.dataset.importActive = String(active);
                if (!active) option.querySelectorAll("select, textarea, input").forEach(input => { input.value = ""; });
            });
        }
        options.forEach(option => {
            function activate() {
                if (mode.value === option.dataset.importMappingOption) return;
                mode.value = option.dataset.importMappingOption;
                updateMappingMode();
                mode.dispatchEvent(new Event("input", {bubbles: true}));
            }
            ["pointerdown", "input"].forEach(event => option.addEventListener(event, activate));
        });
        updateMappingMode();
    });
    const rows = Array.from(form.querySelectorAll("[data-import-row]"));
    const count = form.querySelector("[data-import-row-count]");
    function updateCount() {
        if (count) count.textContent = `${rows.filter(row => row.checked).length} selected rows`;
    }
    rows.forEach(row => row.addEventListener("change", updateCount));
    form.querySelectorAll("[data-import-select]").forEach(button => button.addEventListener("click", () => {
        rows.forEach(row => { row.checked = button.dataset.importSelect === "data" && row.dataset.flagged !== "true"; });
        updateCount();
    }));
    form.querySelectorAll("select[data-import-crossing-select]").forEach(select => {
        const search = document.createElement("input");
        search.type = "search";
        search.autocomplete = "off";
        search.placeholder = "Type to search crossings";
        search.value = select.value ? select.selectedOptions[0].textContent.trim() : "";
        search.id = select.id;
        select.id += "__value";
        select.hidden = true;
        const results = document.createElement("div");
        results.className = "lookup-results import-crossing-results";
        results.id = `${search.id}__results`;
        results.setAttribute("role", "listbox");
        results.setAttribute("aria-label", "Matching crossings");
        const status = document.createElement("div");
        status.className = "import-crossing-status";
        status.setAttribute("role", "status");
        search.setAttribute("role", "combobox");
        search.setAttribute("aria-autocomplete", "list");
        search.setAttribute("aria-controls", results.id);
        search.setAttribute("aria-expanded", "false");
        ["aria-describedby", "aria-invalid"].forEach(name => {
            if (select.hasAttribute(name)) search.setAttribute(name, select.getAttribute(name));
        });
        select.before(search);
        select.after(results, status);
        let timeout, controller, buttons = [], active = -1, generation = 0;
        function closeResults() {
            results.replaceChildren();
            status.textContent = "";
            buttons = [];
            active = -1;
            search.setAttribute("aria-expanded", "false");
            search.removeAttribute("aria-activedescendant");
        }
        function cancelSearch() {
            generation += 1;
            clearTimeout(timeout);
            if (controller) controller.abort();
        }
        function notifySelection() {
            select.dispatchEvent(new Event("change", {bubbles: true}));
            select.dispatchEvent(new Event("input", {bubbles: true}));
        }
        search.addEventListener("input", () => {
            cancelSearch();
            closeResults();
            select.value = "";
            notifySelection();
            const query = search.value.trim();
            search.setCustomValidity(query ? "Choose a crossing from the suggestions." : "");
            if (!query) return;
            const requestGeneration = generation;
            timeout = setTimeout(async () => {
                controller = new AbortController();
                try {
                    const response = await fetch(`${select.dataset.importCrossingSelect}?q=${encodeURIComponent(query)}`, {signal: controller.signal});
                    if (!response.ok) throw new Error("Search failed");
                    const data = await response.json();
                    if (requestGeneration !== generation) return;
                    closeResults();
                    data.results.forEach((item, index) => {
                        const button = document.createElement("button");
                        button.type = "button";
                        button.className = "button";
                        button.textContent = item.label;
                        button.id = `${results.id}_${index}`;
                        button.tabIndex = -1;
                        button.setAttribute("role", "option");
                        button.setAttribute("aria-selected", "false");
                        // Keep focus in the combobox so pointer selection survives blur.
                        button.addEventListener("pointerdown", event => event.preventDefault());
                        button.addEventListener("click", () => {
                            cancelSearch();
                            let option = Array.from(select.options).find(candidate => candidate.value === String(item.id));
                            if (!option) {
                                option = document.createElement("option");
                                option.value = String(item.id);
                                option.textContent = item.label;
                                select.appendChild(option);
                            }
                            select.value = option.value;
                            search.value = item.label;
                            search.setCustomValidity("");
                            closeResults();
                            notifySelection();
                        });
                        buttons.push(button);
                        results.appendChild(button);
                    });
                    search.setAttribute("aria-expanded", buttons.length ? "true" : "false");
                    status.textContent = buttons.length ? `${buttons.length} matching crossings` : "No matching crossings.";
                } catch (error) {
                    if (error.name !== "AbortError" && requestGeneration === generation) status.textContent = "Crossing search is unavailable. Try again.";
                }
            }, 200);
        });
        search.addEventListener("keydown", event => {
            if (event.key === "Escape") {
                cancelSearch();
                closeResults();
            } else if (buttons.length && ["ArrowDown", "ArrowUp"].includes(event.key)) {
                event.preventDefault();
                active = active < 0 ? (event.key === "ArrowDown" ? 0 : buttons.length - 1)
                    : (active + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
                buttons.forEach((button, index) => button.setAttribute("aria-selected", String(index === active)));
                search.setAttribute("aria-activedescendant", buttons[active].id);
                buttons[active].scrollIntoView({block: "nearest"});
            } else if (event.key === "Enter" && active >= 0) {
                event.preventDefault();
                buttons[active].click();
            }
        });
        search.addEventListener("blur", () => { cancelSearch(); closeResults(); });
    });
    function chooseAnimal(input, id) {
        input.value = id;
        const row = input.closest("[data-import-animal-row]");
        const action = row?.querySelector("[data-import-animal-action]");
        if (action) {
            action.value = "link";
            action.dispatchEvent(new Event("change", {bubbles: true}));
        }
        input.dispatchEvent(new Event("change", {bubbles: true}));
        form.dispatchEvent(new Event("input", {bubbles: true}));
    }
    form.querySelectorAll("[data-import-animal-row]").forEach(row => {
        const action = row.querySelector("[data-import-animal-action]");
        const input = row.querySelector("[data-import-animal-search]");
        const manual = row.querySelector("[data-import-manual-id]");
        const generated = row.querySelector("[data-import-generated-id]");
        function updateAction() {
            const generating = action.value === "generate";
            input.disabled = generating;
            manual.hidden = generating;
            generated.hidden = !generating;
        }
        action.addEventListener("change", updateAction);
        row.addEventListener("input", event => {
            if (event.target === action || event.target === input) return;
            generated.textContent = "Save and refresh IDs to update this preview.";
        });
        row.querySelectorAll("[data-import-animal-candidate]").forEach(button => {
            button.addEventListener("click", () => chooseAnimal(input, button.dataset.importAnimalCandidate));
        });
        updateAction();
    });
    form.querySelectorAll("[data-import-animal-search]").forEach(input => {
        const results = document.createElement("div");
        results.className = "lookup-results";
        results.setAttribute("aria-live", "polite");
        input.after(results);
        let timeout, controller;
        input.addEventListener("input", () => {
            clearTimeout(timeout);
            if (controller) controller.abort();
            results.replaceChildren();
            if (!input.value.trim()) return;
            timeout = setTimeout(async () => {
                controller = new AbortController();
                const query = input.value;
                try {
                    const response = await fetch(`${input.dataset.importAnimalSearch}?q=${encodeURIComponent(query)}`, {signal: controller.signal});
                    if (!response.ok) throw new Error("Search failed");
                    const data = await response.json();
                    if (input.value !== query) return;
                    results.replaceChildren();
                    data.results.forEach(item => {
                        const button = document.createElement("button");
                        button.type = "button";
                        button.className = "button";
                        button.textContent = item.label;
                        button.addEventListener("click", () => { chooseAnimal(input, item.id); results.replaceChildren(); });
                        results.appendChild(button);
                    });
                } catch (error) {
                    if (error.name !== "AbortError") results.textContent = "Animal search is unavailable. Enter an official Animal ID.";
                }
            }, 200);
        });
    });
    let changed = false;
    form.addEventListener("input", () => { changed = true; });
    document.querySelectorAll(".import-tabs a").forEach(link => link.addEventListener("click", event => {
        if (!changed || form.elements.step.value === "review") return;
        event.preventDefault();
        const destination = new URL(link.href).searchParams.get("step");
        const button = document.createElement("button");
        button.type = "submit";
        button.name = "next_step";
        button.value = destination;
        button.hidden = true;
        form.appendChild(button);
        form.requestSubmit(button);
    }));
});
