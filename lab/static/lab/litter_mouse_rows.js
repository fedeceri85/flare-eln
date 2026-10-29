document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("[data-litter-form]");
    if (!form) return;
    const prefix = form.elements.mouse_id_prefix;
    const generated = form.elements.no_id_available;
    let mouseCount = Number(form.closest("[data-mouse-count]")?.dataset.mouseCount || 0);
    prefix.readOnly = generated.checked;
    const editor = window.MouseRowEditor.create({
        form,
        rows: document.getElementById("litter-mouse-rows"),
        button: form.querySelector("[data-preview-mice]"),
        status: form.querySelector("[data-litter-preview-status]"),
        retryContainer: form.querySelector("[data-litter-rows-retry]"),
        extraRegions: [
            {element: form.querySelector('[data-litter-detail-overrides]'), key: 'detail_overrides_html'},
            {element: form.querySelector('[data-litter-parent-overrides]'), key: 'parent_overrides_html'},
        ],
        previewAction: "preview_mice",
        watchFields: ["mouse_numbers", "mouse_id_prefix", "no_id_available", "date_of_birth", "breeding_pair", "crossing_definition"],
        listenToInput: true,
        delay: 350,
        beforeRefresh() { prefix.readOnly = generated.checked; },
        afterRefresh(result) {
            document.dispatchEvent(new CustomEvent("litter:rows-updated", {detail: {genotypeHtml: result.genotype_html}}));
            prefix.value = result.prefix;
            mouseCount = result.mouse_count;
            updateOverrides();
            document.dispatchEvent(new CustomEvent("litter:overrides-updated"));
            document.dispatchEvent(new CustomEvent("mouse-editor:count-changed", {detail: {count: result.mouse_count}}));
        },
        successMessage(result) {
            return result.errors.length ? result.errors.join(" ") : "Animal rows updated.";
        },
    });
    function updateOverrides() {
        ['[data-litter-detail-overrides]', '[data-litter-parent-overrides]'].forEach(selector => {
            const region = form.querySelector(selector);
            if (region) region.hidden = mouseCount <= 1 && !region.querySelector('input[type="checkbox"]:checked');
        });
        form.querySelectorAll('[data-litter-detail-overrides] [data-shared-update]').forEach(group => {
            const toggle = group.querySelector('input[type="checkbox"]');
            group.querySelectorAll('.bulk-input input, .bulk-input select, .bulk-input textarea').forEach(field => {
                field.disabled = !toggle.checked;
            });
        });
    }
    form.addEventListener('change', event => {
        updateOverrides();
        if (event.target.hasAttribute('data-litter-crossing-override')) editor.scheduleRefresh();
    });
    updateOverrides();
});
