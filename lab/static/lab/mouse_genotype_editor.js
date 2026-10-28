/* Copy shared results while retaining later edits to individual mice. */
(function () {
    function create(options) {
        const {form, rows, sharedRoot, toggle} = options;
        const sharedSelector = options.sharedSelector || "[data-genotype-field]";
        const individualSelector = options.individualSelector || "[data-individual-genotype-field]";
        const sharedEdits = new Map();
        const individualEdits = new Map();
        let version = 1;
        function key(field) { return field.dataset.lineId + (field.type === "checkbox" ? ":uncertain" : ":genotype"); }
        function value(field) { return field.type === "checkbox" ? field.checked : field.value; }
        function assign(field, next) {
            if (field.type === "checkbox") field.checked = next;
            else field.value = next;
        }
        function sharedFields() { return sharedRoot.querySelectorAll(sharedSelector); }
        function modes() {
            return Array.from(sharedRoot.querySelectorAll('[data-genotype-individual]')).filter(field => field.dataset.genotypeIndividual);
        }
        function modeFor(field) { return modes().find(mode => String(mode.dataset.lineId) === String(field.dataset.lineId)); }
        function modeVisible(mode) { return !mode.closest?.('[data-multiple-mice-only]')?.hidden; }
        function sharedEnabled(field) {
            const mode = modeFor(field);
            return mode ? modeVisible(mode) && !mode.checked : enabled();
        }
        function syncModes() {
            sharedFields().forEach(field => {
                const mode = modeFor(field);
                if (mode) field.disabled = mode.checked;
            });
            rows.querySelectorAll(individualSelector).forEach(field => {
                const mode = modeFor(field);
                if (!mode) return;
                const shared = Array.from(sharedFields()).find(item => key(item) === key(field));
                field.disabled = modeVisible(mode) && !mode.checked && Boolean(shared && hasValue(shared));
            });
        }
        function enabled() { return !toggle || toggle.checked; }
        function hasValue(field) { return field.type === "checkbox" || Boolean(field.value); }

        if (options.seedDefaults) {
            // Litter rows on a validation page already contain individual choices.
            rows.querySelectorAll(individualSelector).forEach(field => individualEdits.set(field.name, version));
            sharedFields().forEach(field => sharedEdits.set(key(field), {value: value(field), version: 0}));
        }
        function fillField(field, replay) {
            if (!sharedEnabled(field) || !hasValue(field)) return 0;
            const edit = sharedEdits.get(key(field));
            if (!edit || edit.value !== value(field)) return 0;
            let count = 0;
            rows.querySelectorAll(individualSelector).forEach(individual => {
                if (key(individual) !== key(field)) return;
                if (replay && !modeFor(field) && (individualEdits.get(individual.name) || 0) > edit.version) return;
                assign(individual, edit.value);
                count++;
            });
            return count;
        }
        function copyField(field) {
            if (!sharedEnabled(field) || !hasValue(field)) return;
            if (modeFor(field) && toggle) toggle.checked = true;
            sharedEdits.set(key(field), {value: value(field), version: ++version});
            const count = fillField(field, false);
            if (count && options.onCopy) options.onCopy(count);
        }
        function replay() {
            sharedFields().forEach(field => {
                if (modeFor(field) && sharedEnabled(field)) copyField(field);
                else fillField(field, true);
            });
            syncModes();
        }
        form.addEventListener("change", function (event) {
            const field = event.target;
            if (field.dataset.genotypeIndividual) {
                sharedFields().forEach(item => {
                    if (String(item.dataset.lineId) === String(field.dataset.lineId)) copyField(item);
                });
                syncModes();
            } else if (field === toggle) {
                if (enabled()) sharedFields().forEach(copyField);
            } else if (field.matches(sharedSelector)) {
                copyField(field);
                syncModes();
            } else if (field.matches(individualSelector)) {
                individualEdits.set(field.name, ++version);
            }
        });
        function replaceShared(html) {
            const live = new Map();
            sharedFields().forEach(field => live.set(field.name, value(field)));
            modes().forEach(field => live.set(field.name, value(field)));
            sharedRoot.innerHTML = html;
            modes().forEach(field => { if (live.has(field.name)) assign(field, live.get(field.name)); });
            const currentKeys = new Set();
            sharedFields().forEach(field => {
                currentKeys.add(key(field));
                if (live.has(field.name)) assign(field, live.get(field.name));
                else if (options.seedDefaults) sharedEdits.set(key(field), {value: value(field), version: 0});
            });
            sharedEdits.forEach((edit, name) => { if (!currentKeys.has(name)) sharedEdits.delete(name); });
            replay();
        }
        syncModes();
        document.addEventListener('mouse-editor:count-changed', replay);
        if (window.MutationObserver) {
            new MutationObserver(records => {
                if (records.some(record => Array.from(record.addedNodes).some(node =>
                    node.nodeType === 1 && (node.matches('[data-genotype-individual]') || node.querySelector('[data-genotype-individual]'))
                ))) replay();
            }).observe(sharedRoot, {childList: true, subtree: true});
        }
        return {replay, replaceShared};
    }
    window.MouseGenotypeEditor = {create};
})();
