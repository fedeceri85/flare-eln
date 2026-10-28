document.addEventListener('DOMContentLoaded', function () {
    const editor = document.querySelector('[data-mouse-editor]');
    if (!editor) return;
    const tablist = editor.querySelector('[data-mouse-tab-list]');
    const tabs = [...tablist.querySelectorAll('[data-mouse-tab]')];
    const panels = [...editor.querySelectorAll('[data-mouse-tab-panel]')];
    const activeInput = editor.querySelector('[data-mouse-active-tab]') || { value: editor.dataset.mouseInitialTab || 'shared' };
    const summaryEditLink = editor.querySelector('[data-mouse-summary-edit]');
    const errorSelector = '.error-summary, .error-list li, .errorlist li, [aria-invalid="true"]';

    function visibleTabs() { return tabs.filter(tab => !tab.hidden); }
    function activate(key, focus = false) {
        if (!visibleTabs().some(tab => tab.dataset.mouseTab === key)) key = visibleTabs()[0].dataset.mouseTab;
        activeInput.value = key;
        tabs.forEach(tab => {
            const selected = tab.dataset.mouseTab === key;
            tab.setAttribute('aria-selected', String(selected));
            tab.tabIndex = selected ? 0 : -1;
            if (selected && focus) tab.focus();
        });
        panels.forEach(panel => { panel.hidden = panel.dataset.mouseTabPanel !== key; });
        editor.dispatchEvent(new CustomEvent('mouse-editor:tab-activated', {bubbles: true, detail: {tab: key}}));
        if (summaryEditLink) {
            const editUrl = new URL(summaryEditLink.href, window.location.href);
            const summaryUrl = new URL(editUrl.searchParams.get('return_url'), window.location.href);
            summaryUrl.searchParams.set('mouse_editor_tab', key);
            const localSummaryUrl = summaryUrl.pathname + summaryUrl.search + summaryUrl.hash;
            editUrl.searchParams.set('mouse_editor_tab', tabs.some(tab => tab.dataset.mouseTab === 'individual' && !tab.hidden) ? 'individual' : 'shared');
            editUrl.searchParams.set('return_url', localSummaryUrl);
            summaryEditLink.href = editUrl.pathname + editUrl.search + editUrl.hash;
            window.history.replaceState(window.history.state, '', localSummaryUrl);
        }
    }
    function reveal(element) {
        const panel = element?.closest('[data-mouse-tab-panel]');
        if (!panel) return;
        activate(panel.dataset.mouseTabPanel);
        let details = element.closest('details');
        while (details && editor.contains(details)) {
            details.open = true;
            details = details.parentElement?.closest('details');
        }
    }
    function markErrors() {
        tabs.forEach(tab => {
            const hasError = panels.some(panel => panel.dataset.mouseTabPanel === tab.dataset.mouseTab && panel.querySelector(errorSelector));
            tab.querySelector('[data-mouse-tab-error]').hidden = !hasError;
        });
    }
    panels.forEach(panel => {
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', 'mouse-tab-' + panel.dataset.mouseTabPanel);
        panel.tabIndex = 0;
    });
    tabs.forEach(tab => {
        tab.addEventListener('click', () => activate(tab.dataset.mouseTab));
        tab.addEventListener('keydown', event => {
            const available = visibleTabs();
            const index = available.indexOf(tab);
            let next;
            if (event.key === 'ArrowRight') next = (index + 1) % available.length;
            else if (event.key === 'ArrowLeft') next = (index + available.length - 1) % available.length;
            else if (event.key === 'Home') next = 0;
            else if (event.key === 'End') next = available.length - 1;
            else return;
            event.preventDefault();
            activate(available[next].dataset.mouseTab, true);
        });
    });
    editor.addEventListener('click', event => {
        const link = event.target.closest('[data-error-summary] a[href^="#"]');
        if (link) reveal(document.getElementById(link.getAttribute('href').slice(1)));
    }, true);
    let handlingInvalid = false;
    editor.addEventListener('invalid', event => {
        event.target.setAttribute('aria-invalid', 'true');
        if (!handlingInvalid) {
            handlingInvalid = true;
            reveal(event.target);
            setTimeout(() => { handlingInvalid = false; }, 0);
        }
        markErrors();
    }, true);
    editor.addEventListener('input', event => {
        if (event.target.getAttribute('aria-invalid') === 'true' && event.target.validity?.valid) {
            event.target.removeAttribute('aria-invalid');
            markErrors();
        }
    });
    function updateMouseCount(count) {
        const widget = editor.querySelector('[data-litter-individual-editor]');
        const singleSlot = editor.querySelector('[data-single-mouse-slot]');
        const multipleSlot = editor.querySelector('[data-multiple-mice-slot]');
        if (!widget || !singleSlot || !multipleSlot) return;
        const single = count <= 1;
        const tab = tabs.find(tab => tab.dataset.mouseTab === 'individual');
        if (tab) tab.hidden = single;
        editor.querySelectorAll('[data-multiple-mice-only]').forEach(element => { element.hidden = single; });
        (single ? singleSlot : multipleSlot).appendChild(widget);
        const genetics = editor.querySelector('[data-genetics-editor]');
        const geneticsSlot = editor.querySelector(single ? '[data-single-genetics-slot]' : '[data-multiple-genetics-slot]');
        if (genetics && geneticsSlot) geneticsSlot.appendChild(genetics);
        if (single && activeInput.value === 'individual') activate('shared');
        else activate(activeInput.value);
        markErrors();
    }
    document.addEventListener('mouse-editor:count-changed', event => {
        if (Number.isInteger(event.detail.count)) updateMouseCount(event.detail.count);
    });
    tablist.hidden = false;
    activate(activeInput.value);
    const firstError = panels.map(panel => panel.querySelector(errorSelector)).find(Boolean);
    if (firstError) reveal(firstError);
    markErrors();
    // AJAX refreshes keep the tab containers in place, including mouse inputs.
    // Mark and reveal newly returned server errors without rebuilding the forms.
    if (window.MutationObserver) {
        new MutationObserver(records => {
            markErrors();
            for (const record of records) {
                for (const node of record.addedNodes) {
                    if (node.nodeType !== 1) continue;
                    const error = node.matches(errorSelector) ? node : node.querySelector(errorSelector);
                    if (error) { reveal(error); return; }
                }
            }
        }).observe(editor, { childList: true, subtree: true });
    }
});
