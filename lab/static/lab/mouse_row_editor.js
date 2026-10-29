/* Shared refresh lifecycle for the litter and existing-mouse editors. */
(function () {
    function create(options) {
        const {form, rows, button, status, retryContainer} = options;
        let requestNumber = 0;
        let timer;
        let pending = false;
        let dirty = false;
        retryContainer.hidden = true;

        async function refresh() {
            clearTimeout(timer);
            const currentRequest = ++requestNumber;
            const data = new FormData(form);
            data.set(options.previewAction, "1");
            if (options.beforeRefresh) options.beforeRefresh();
            pending = true;
            dirty = false;
            retryContainer.hidden = true;
            button.disabled = true;
            status.textContent = "Updating animal rows…";
            try {
                const response = await fetch(form.action || window.location.href, {
                    method: "POST", body: data, headers: {"X-Requested-With": "XMLHttpRequest"},
                });
                if (!response.ok) throw new Error("Preview failed");
                const result = await response.json();
                if (currentRequest !== requestNumber) return;
                if (typeof result.html !== "string") throw new Error("Invalid preview");
                // Keep edits made while the request was in flight, including unchecked boxes.
                const regions = [{element: rows, html: result.html}, ...(options.extraRegions || []).map(region => ({
                    element: region.element, html: result[region.key],
                }))];
                regions.forEach(({element, html}) => {
                    if (!element || typeof html !== "string") return;
                    const live = new Map();
                    const expanded = new Set();
                    element.querySelectorAll("details[open]").forEach(detail => {
                        const field = detail.querySelector("input, select, textarea");
                        if (field) expanded.add(field.name);
                    });
                    element.querySelectorAll("input, select, textarea").forEach(field => {
                        live.set(field.name, field.type === "checkbox" ? field.checked : field.value);
                    });
                    element.innerHTML = html;
                    element.querySelectorAll("input, select, textarea").forEach(field => {
                        if (!live.has(field.name)) return;
                        if (field.type === "checkbox") field.checked = live.get(field.name);
                        else field.value = live.get(field.name);
                    });
                    element.querySelectorAll("details").forEach(detail => {
                        const field = detail.querySelector("input, select, textarea");
                        if (field && expanded.has(field.name)) detail.open = true;
                    });
                });
                if (options.afterRefresh) options.afterRefresh(result);
                status.textContent = options.successMessage(result);
            } catch (error) {
                if (currentRequest !== requestNumber) return;
                dirty = true;
                retryContainer.hidden = false;
                status.textContent = "Could not update the rows. Use Retry updating animal rows to try again.";
            } finally {
                if (currentRequest === requestNumber) {
                    pending = false;
                    button.disabled = false;
                }
            }
        }

        function scheduleRefresh() {
            dirty = true;
            clearTimeout(timer);
            // Invalidate earlier responses as soon as their source fields change.
            requestNumber++;
            timer = setTimeout(refresh, options.delay);
        }
        options.watchFields.forEach(function (name) {
            const field = form.elements[name];
            if (!field) return;
            field.addEventListener("change", scheduleRefresh);
            if (options.listenToInput && field.type !== "checkbox" && field.type !== "hidden") {
                field.addEventListener("input", scheduleRefresh);
            }
        });
        button.addEventListener("click", function (event) {
            event.preventDefault();
            refresh();
        });
        form.addEventListener("submit", function (event) {
            if (event.submitter === button || (options.skipSubmit && options.skipSubmit(event))) return;
            if (pending || dirty) {
                event.preventDefault();
                status.textContent = "Updating animal rows. Review them before saving.";
                if (!pending) refresh();
            }
        });
        return {refresh, scheduleRefresh};
    }
    window.MouseRowEditor = {create};
})();
