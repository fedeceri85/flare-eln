document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("[data-litter-form]");
    if (!form) return;
    const status = form.querySelector("[data-litter-genotype-status]");
    const genotypes = window.MouseGenotypeEditor.create({
        form,
        rows: document.getElementById("litter-mouse-rows"),
        sharedRoot: form.querySelector("[data-litter-shared-genotypes]"),
        sharedSelector: "[data-genotype-field], [data-genotype-uncertainty]",
        individualSelector: "[data-individual-genotype-field], [data-individual-genotype-uncertainty]",
        seedDefaults: true,
        onCopy() {
            status.textContent = "Shared genotype copied to all animal rows. You can adjust individual animals afterward.";
        },
    });
    document.addEventListener("litter:rows-updated", function (event) {
        if (typeof event.detail.genotypeHtml === "string") genotypes.replaceShared(event.detail.genotypeHtml);
    });
});
