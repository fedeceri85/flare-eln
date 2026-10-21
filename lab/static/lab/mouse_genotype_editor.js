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
        function enabled() { return !toggle || toggle.checked; }
        function hasValue(field) { return field.type === "checkbox" || Boolean(field.value); }

        if (options.seedDefaults) {
            // Litter rows on a validation page already contain individual choices.
            rows.querySelectorAll(individualSelector).forEach(field => individualEdits.set(field.name, version));
            sharedFields().forEach(field => sharedEdits.set(key(field), {value: value(field), version: 0}));
        }
        function fillField(field, replay) {
            if (!enabled() || !hasValue(field)) return 0;
            const edit = sharedEdits.get(key(field));
            if (!edit || edit.value !== value(field)) return 0;
            let count = 0;
            rows.querySelectorAll(individualSelector).forEach(individual => {
                if (key(individual) !== key(field)) return;
                if (replay && (individualEdits.get(individual.name) || 0) > edit.version) return;
                assign(individual, edit.value);
                count++;
            });
            return count;
        }
        function copyField(field) {
            if (!enabled() || !hasValue(field)) return;
            sharedEdits.set(key(field), {value: value(field), version: ++version});
            const count = fillField(field, false);
            if (count && options.onCopy) options.onCopy(count);
        }
        function replay() { sharedFields().forEach(field => fillField(field, true)); }
        form.addEventListener("change", function (event) {
            const field = event.target;
            if (field === toggle) {
                if (enabled()) sharedFields().forEach(copyField);
            } else if (field.matches(sharedSelector)) {
                copyField(field);
            } else if (field.matches(individualSelector)) {
                individualEdits.set(field.name, ++version);
            }
        });
        function replaceShared(html) {
            const live = new Map();
            sharedFields().forEach(field => live.set(field.name, value(field)));
            sharedRoot.innerHTML = html;
            const currentKeys = new Set();
            sharedFields().forEach(field => {
                currentKeys.add(key(field));
                if (live.has(field.name)) assign(field, live.get(field.name));
                else if (options.seedDefaults) sharedEdits.set(key(field), {value: value(field), version: 0});
            });
            sharedEdits.forEach((edit, name) => { if (!currentKeys.has(name)) sharedEdits.delete(name); });
            replay();
        }
        return {replay, replaceShared};
    }
    window.MouseGenotypeEditor = {create};
})();
