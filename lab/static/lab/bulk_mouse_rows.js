document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("[data-bulk-individual-form]");
    if (!form) return;
    const rows = document.getElementById("bulk-individual-mouse-rows");
    const button = form.querySelector("[data-preview-individual-mice]");
    const status = form.querySelector("[data-bulk-rows-status]");
    const retryContainer = form.querySelector("[data-bulk-rows-retry]");
    const genotypes = window.MouseGenotypeEditor.create({
        form, rows, sharedRoot: form, toggle: form.elements.update_genotype,
        onCopy(count) {
            status.textContent = "Genotype copied to " + count + " animal rows. Review the individual values before saving.";
        },
    });

    window.MouseRowEditor.create({
        form, rows, button, status, retryContainer,
        previewAction: "preview_individual_mice",
        watchFields: ["update_crossing", "crossing_definition", ...Array.from(form.querySelectorAll('[data-individual-crossing]'), field => field.name)],
        delay: 250,
        afterRefresh() { genotypes.replay(); },
        successMessage() { return "Animal rows updated. The displayed genotype values will be saved."; },
        skipSubmit(event) { return event.submitter?.hasAttribute("formaction"); },
    });
});
