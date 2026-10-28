/* Resolve parents on field exit/Enter; text edits always invalidate old links. */
(() => {
    const dialog = document.querySelector('[data-remove-parent-dialog]');
    let pendingRemoval = null;
    const cancelRemoval = () => { pendingRemoval = null; dialog.close(); };
    if (dialog) {
        dialog.querySelector('[data-remove-parent-cancel]').addEventListener('click', cancelRemoval);
        dialog.addEventListener('cancel', event => { event.preventDefault(); cancelRemoval(); });
        dialog.querySelector('[data-remove-parent-confirm]').addEventListener('click', () => {
            const remove = pendingRemoval;
            pendingRemoval = null;
            dialog.close();
            if (remove) remove();
        });
    }
    document.querySelectorAll('[data-parent-control]').forEach((root, index) => {
        const text = root.querySelector('[data-parent-text]');
        const reference = root.querySelector('[data-parent-reference]');
        const state = root.querySelector('[data-parent-state]');
        const list = root.querySelector('[data-parent-suggestions]');
        const toggle = root.querySelector('input[type="checkbox"]');
        const removeButton = root.querySelector('[data-parent-remove]');
        const importMode = root.dataset.parentImport === 'true';
        const role = root.dataset.parentRole || 'parent';
        const hadParent = root.dataset.parentHadValue === 'true';
        let sequence = 0, active = -1, matchStatus = '', suggestionTimer;
        list.id = `parent-suggestions-${index}`;
        list.setAttribute('role', 'listbox');
        list.setAttribute('aria-label', 'Matching animals');
        text.setAttribute('role', 'combobox');
        text.setAttribute('aria-autocomplete', 'list');
        text.setAttribute('aria-controls', list.id);
        const close = () => {
            list.hidden = true;
            text.setAttribute('aria-expanded', 'false');
            text.removeAttribute('aria-activedescendant');
            active = -1;
        };
        const refresh = () => {
            const disabled = Boolean(toggle && !toggle.checked);
            text.disabled = reference.disabled = disabled;
            const removing = hadParent && !text.value.trim() && !reference.value && !disabled;
            if (removing) state.setAttribute('data-removal-pending', '');
            else state.removeAttribute('data-removal-pending');
            state.textContent = removing ? `${role[0].toUpperCase() + role.slice(1)} will be removed when saved.`
                : reference.value ? `${importMode ? 'Matched ID' : 'Linked'}: ${reference.value}${importMode ? ' · confirm in Review' : ''}`
                : matchStatus || (text.value.trim() ? (importMode ? 'Review will resolve this ID.' : 'External text') : (importMode ? 'Uses mapped parent ID' : 'Unknown'));
            if (removeButton) removeButton.hidden = !text.value.trim() && !reference.value;
            if (disabled) close();
        };
        const choose = animal => {
            clearTimeout(suggestionTimer);
            sequence += 1;
            reference.value = animal.id;
            text.value = animal.id;
            matchStatus = '';
            close(); refresh(); text.focus();
        };
        async function lookup(resolve, showSuggestions) {
            const query = text.value.trim();
            if (!query || text.disabled) return;
            const request = ++sequence;
            const params = new URLSearchParams({q: query, resolve_exact: '1'});
            if (text.dataset.parentExclude) params.append('exclude', text.dataset.parentExclude);
            matchStatus = 'Checking ID…'; refresh();
            try {
                const response = await fetch(`${text.dataset.parentLookup}?${params}`, {headers: {'Accept': 'application/json'}});
                if (!response.ok) throw new Error('Lookup failed');
                const result = await response.json();
                if (request !== sequence || text.disabled || text.value.trim() !== query) return;
                if (resolve && result.exact_match) {
                    if (result.exact_match.id === text.dataset.parentExclude) {
                        matchStatus = 'An animal cannot be its own parent.';
                    } else {
                        reference.value = result.exact_match.id;
                        matchStatus = '';
                    }
                    close();
                } else {
                    matchStatus = result.ambiguous ? 'Multiple exact matches — select an animal or keep external text.'
                        : importMode ? 'Review checks existing animals and animals in this import.' : 'External text';
                    if (showSuggestions) {
                        list.replaceChildren();
                        result.results.forEach((animal, position) => {
                            const option = document.createElement('button');
                            option.type = 'button'; option.id = `${list.id}-${position}`;
                            option.setAttribute('role', 'option'); option.setAttribute('aria-selected', 'false');
                            option.textContent = animal.label;
                            // Keep focus in the input until click selects the result.
                            // Some browsers blur with no relatedTarget for button clicks.
                            option.addEventListener('pointerdown', event => event.preventDefault());
                            option.addEventListener('click', () => choose(animal));
                            list.append(option);
                        });
                        list.hidden = !list.children.length;
                        text.setAttribute('aria-expanded', String(!list.hidden));
                    } else close();
                }
                refresh();
            } catch (_) {
                if (request !== sequence) return;
                matchStatus = importMode ? 'Could not check ID. Review will check it.' : 'Could not check ID. It will be checked when saved.';
                close(); refresh();
            }
        }
        text.addEventListener('input', () => {
            clearTimeout(suggestionTimer);
            sequence += 1;
            reference.value = '';
            matchStatus = text.value.trim() ? 'Leave this field or press Enter to check the ID.' : '';
            close(); refresh();
            // Suggest while typing, but establish exact links only on blur/Enter.
            if (text.value.trim() && !text.disabled) suggestionTimer = setTimeout(() => lookup(false, true), 200);
        });
        text.addEventListener('blur', event => {
            clearTimeout(suggestionTimer);
            // Choosing a suggestion or removal action must not race a blur lookup.
            if (event.relatedTarget && root.contains(event.relatedTarget)) return;
            sequence += 1;
            close();
            if (!reference.value) return lookup(true, false);
        });
        text.addEventListener('keydown', event => {
            if (event.key === 'Escape') { clearTimeout(suggestionTimer); sequence += 1; close(); return; }
            const options = Array.from(list.children);
            if (event.key === 'Enter') {
                clearTimeout(suggestionTimer);
                event.preventDefault();
                if (!list.hidden && active >= 0) options[active].click();
                else return lookup(true, true);
            } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                clearTimeout(suggestionTimer);
                event.preventDefault();
                if (list.hidden) return lookup(false, true);
                active = active < 0 ? (event.key === 'ArrowDown' ? 0 : options.length - 1)
                    : (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
                options.forEach((option, i) => option.setAttribute('aria-selected', String(i === active)));
                text.setAttribute('aria-activedescendant', options[active].id);
            }
        });
        removeButton?.addEventListener('click', () => {
            const description = `Remove ${role} ${reference.value || text.value} from ${root.dataset.parentTarget || 'this animal'}?`
                + (root.dataset.parentHasOverrides === 'true' ? ' Individual overrides still apply.' : '');
            const remove = () => {
                clearTimeout(suggestionTimer);
                sequence += 1;
                if (toggle) toggle.checked = true;
                reference.value = text.value = '';
                matchStatus = ''; close(); refresh(); text.focus();
            };
            if (dialog && typeof dialog.showModal === 'function') {
                dialog.querySelector('[data-remove-parent-description]').textContent = description;
                pendingRemoval = remove;
                dialog.showModal();
            } else if (window.confirm(description + ' The change takes effect when saved.')) remove();
        });
        if (toggle) toggle.addEventListener('change', () => { clearTimeout(suggestionTimer); sequence += 1; close(); refresh(); });
        close(); refresh();
    });
})();
