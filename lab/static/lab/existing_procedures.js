(() => {
    function initialize(section) {
        const form = section.querySelector('[data-existing-procedure-editor]');
        if (!form) return;
        section.querySelectorAll('[data-procedure-js-only]').forEach(element => { element.hidden = false; });
        const rows = [...form.querySelectorAll('[data-procedure-row-checkbox]')];
        const all = form.querySelector('[data-procedure-select-all]');
        const save = form.querySelector('[data-procedure-save]');
        const deleteButton = form.querySelector('[data-procedure-delete]');
        const confirmations = [save].filter(Boolean);
        const snapshot = form.querySelector('[name="existing_procedures-snapshot"]');
        const status = form.querySelector('[data-procedure-status]');
        let revision = 0;
        let saving = false;
        let pending = false;
        function count() {
            const selected = rows.filter(row => row.checked).length;
            const counter = form.querySelector('[data-procedure-selection-count]');
            if (counter) counter.textContent = `${selected} records selected`;
            if (deleteButton) deleteButton.disabled = selected === 0 || pending;
            if (all) {
                all.checked = rows.length > 0 && selected === rows.length;
                all.indeterminate = selected > 0 && selected < rows.length;
            }
        }
        function invalidate() {
            revision++;
            snapshot.value = '';
            confirmations.forEach(button => { button.disabled = true; button.hidden = true; });
            form.querySelector('[data-procedure-preview]').replaceChildren();
            count();
        }
        function toggleFields() {
            form.querySelectorAll('[data-procedure-update-field]').forEach(group => {
                group.querySelector('.bulk-input input, .bulk-input select').disabled = !group.querySelector('input[type="checkbox"]').checked;
            });
        }
        if (all) all.addEventListener('change', () => {
            rows.forEach(row => { row.checked = all.checked; });
            invalidate();
        });
        form.addEventListener('input', event => {
            if (event.target !== all) { toggleFields(); invalidate(); }
        });
        form.addEventListener('change', event => {
            if (event.target !== all) { toggleFields(); invalidate(); }
        });
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (pending || saving || form.dataset.procedureBusy === 'true') return;
            const action = event.submitter?.value || 'preview';
            if (action === 'save' && !snapshot.value) return;
            if (action === 'delete') {
                const selected = rows.filter(row => row.checked).length;
                if (!selected) { status.textContent = 'Select at least one procedure to delete.'; return; }
                if (!window.confirm(`Delete ${selected} selected procedure records and their notes? This cannot be undone.`)) return;
            }
            const data = new FormData(form);
            data.set('existing_procedure_action', action);
            if (action === 'delete') data.set('existing_procedures-confirm_delete', '1');
            const submittedRevision = revision;
            pending = true;
            form.dataset.procedureBusy = 'true';
            saving = ['save', 'delete'].includes(action);
            const disabledStates = [...form.elements].map(element => [element, element.disabled]);
            if (saving) disabledStates.forEach(([element]) => { element.disabled = true; });
            form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
            status.textContent = action === 'delete' ? 'Deleting selected procedures…' : saving ? 'Saving procedure changes…' : 'Preparing preview…';
            try {
                const response = await fetch(window.location.href, {
                    method: 'POST', body: data, credentials: 'same-origin',
                    headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}
                });
                const result = await response.json();
                if (submittedRevision !== revision) {
                    status.textContent = 'Your selection or changes changed. Preview again.';
                    return;
                }
                if (typeof result.html !== 'string') throw new Error('Invalid response');
                const template = document.createElement('template');
                template.innerHTML = result.html;
                const replacement = template.content.querySelector('[data-existing-procedure-section]');
                if (!replacement) throw new Error('Invalid response');
                section.replaceWith(replacement);
                initialize(replacement);
            } catch (error) {
                invalidate();
                status.textContent = 'Could not complete the request. Your mouse edits are still here. Review the procedure list before trying again.';
            } finally {
                pending = false;
                delete form.dataset.procedureBusy;
                saving = false;
                disabledStates.forEach(([element, disabled]) => { element.disabled = disabled; });
                toggleFields();
                count();
                if (!snapshot.value) confirmations.forEach(button => { button.disabled = true; button.hidden = true; });
            }
        });
        toggleFields();
        count();
    }
    document.addEventListener('lab:procedure-editor-replaced', event => initialize(event.detail.section));
    document.querySelectorAll('[data-existing-procedure-section]').forEach(initialize);
})();
