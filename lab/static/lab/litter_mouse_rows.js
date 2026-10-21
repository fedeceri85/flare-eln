document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("[data-litter-form]");
    if (!form) return;
    const prefix = form.elements.mouse_id_prefix;
    const generated = form.elements.no_id_available;
    prefix.readOnly = generated.checked;
    window.MouseRowEditor.create({
        form,
        rows: document.getElementById("litter-mouse-rows"),
        button: form.querySelector("[data-preview-mice]"),
        status: form.querySelector("[data-litter-preview-status]"),
        retryContainer: form.querySelector("[data-litter-rows-retry]"),
        previewAction: "preview_mice",
        watchFields: ["mouse_numbers", "mouse_id_prefix", "no_id_available", "date_of_birth", "breeding_pair", "crossing_definition"],
        listenToInput: true,
        delay: 350,
        beforeRefresh() { prefix.readOnly = generated.checked; },
        afterRefresh(result) {
            document.dispatchEvent(new CustomEvent("litter:rows-updated", {detail: {genotypeHtml: result.genotype_html}}));
            prefix.value = result.prefix;
            document.dispatchEvent(new CustomEvent("mouse-editor:count-changed", {detail: {count: result.mouse_count}}));
        },
        successMessage(result) {
            return result.errors.length ? result.errors.join(" ") : "Mouse rows updated.";
        },
    });
});
