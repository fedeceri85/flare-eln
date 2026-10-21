(() => {
    function initialize(section) {
        const form = section.querySelector('[data-new-procedure-form]');
        const fields = form.querySelector('[data-new-procedure-inputs]');
        const status = form.querySelector('[data-new-procedure-status]');
        fields.disabled = form.dataset.canAdd !== 'true';
        let pending = false;
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (pending || fields.disabled) return;
            const existing = document.querySelector('[data-existing-procedure-section]');
            const existingForm = existing?.querySelector('[data-existing-procedure-editor]');
            if (existingForm?.dataset.procedureBusy === 'true') {
                status.textContent = 'Wait for the current procedure request to finish before adding a procedure.';
                return;
            }
            const data = new FormData(form);
            const previousControls = existingForm ? [...existingForm.elements].map(element => [element, element.disabled]) : [];
            pending = true;
            fields.disabled = true;
            if (existingForm) existingForm.dataset.procedureBusy = 'true';
            previousControls.forEach(([element]) => { element.disabled = true; });
            status.textContent = 'Adding procedures…';
            try {
                const response = await fetch(window.location.href, {
                    method: 'POST', body: data, credentials: 'same-origin',
                    headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}
                });
                const result = await response.json();
                if (typeof result.creation_html !== 'string') throw new Error('Invalid response');
                const template = document.createElement('template');
                template.innerHTML = result.creation_html;
                const replacement = template.content.querySelector('[data-new-procedure-section]');
                if (!replacement) throw new Error('Invalid response');
                if (result.ok && existing && typeof result.existing_html === 'string') {
                    const values = new Map();
                    existingForm.querySelectorAll('input, select, textarea').forEach(input => {
                        if (!input.name.startsWith('existing_procedures-') || input.type === 'hidden') return;
                        const key = input.name + (input.type === 'checkbox' ? ':' + input.value : '');
                        values.set(key, input.type === 'checkbox' ? input.checked : input.value);
                    });
                    const existingTemplate = document.createElement('template');
                    existingTemplate.innerHTML = result.existing_html;
                    const refreshed = existingTemplate.content.querySelector('[data-existing-procedure-section]');
                    if (!refreshed) throw new Error('Invalid procedure list');
                    refreshed.querySelectorAll('input, select, textarea').forEach(input => {
                        const key = input.name + (input.type === 'checkbox' ? ':' + input.value : '');
                        if (!values.has(key)) return;
                        if (input.type === 'checkbox') input.checked = values.get(key);
                        else input.value = values.get(key);
                    });
                    existing.replaceWith(refreshed);
                    document.dispatchEvent(new CustomEvent('lab:procedure-editor-replaced', {detail: {section: refreshed}}));
                }
                section.replaceWith(replacement);
                initialize(replacement);
            } catch (error) {
                status.textContent = 'Could not confirm whether procedures were added. Your mouse edits are still here. Check existing procedures before trying again.';
            } finally {
                pending = false;
                fields.disabled = form.dataset.canAdd !== 'true';
                previousControls.forEach(([element, disabled]) => { element.disabled = disabled; });
                if (existingForm) delete existingForm.dataset.procedureBusy;
            }
        });
    }
    document.querySelectorAll('[data-new-procedure-section]').forEach(initialize);
})();
