document.addEventListener('DOMContentLoaded', function () {
    const sectionRoot = document.querySelector('[data-shared-bulk-editor]');
    const section = sectionRoot?.closest?.('form') || sectionRoot;
    if (!section) return;
    const genotypeColumn = section.querySelector('.shared-genotype-column');
    const crossingToggle = section.querySelector('[name="update_crossing"]');
    const genotypeToggle = section.querySelector('[name="update_genotype"]');
    const notesAction = section.querySelector('[name="notes_action"]');
    const notesContent = section.querySelector('[data-shared-notes-content]');

    function updateGenotypeState() {
        // Changing crossing also applies its displayed genotypes in the existing save logic.
        const enabled = Boolean(crossingToggle?.checked || genotypeToggle?.checked);
        if (genotypeColumn.querySelector('[data-genotype-individual]')) return;
        genotypeColumn.querySelectorAll('[data-genotype-subset] input, [data-genotype-subset] select').forEach(field => {
            field.disabled = !enabled;
        });
    }
    function updateState() {
        section.querySelectorAll('[data-shared-update]').forEach(group => {
            const enabled = group.querySelector('input[type="checkbox"]').checked;
            group.querySelectorAll('.bulk-input input, .bulk-input select, .bulk-input textarea').forEach(field => {
                field.disabled = !enabled;
            });
        });
        updateGenotypeState();
        notesContent.hidden = !['append', 'replace'].includes(notesAction.value) && !notesContent.querySelector('.error-list');
    }
    section.addEventListener('change', updateState);
    updateState();
    if (window.MutationObserver) {
        new MutationObserver(updateGenotypeState).observe(genotypeColumn, { childList: true, subtree: true });
    }
});
