function bindCataloguePanels(scope) {
    scope.querySelectorAll('[data-catalogue-panel]').forEach(function (link) {
        link.addEventListener('click', function (event) {
            const panel = document.getElementById(link.dataset.cataloguePanel);
            if (!panel) return;
            event.preventDefault();
            panel.open = true;
            panel.scrollIntoView({behavior: 'smooth', block: 'start'});
            const input = panel.querySelector('input:not([type="hidden"]), select, textarea');
            if (input) input.focus({preventScroll: true});
        });
    });
}
bindCataloguePanels(document);
document.querySelectorAll('[data-crossing-order]').forEach(function (form) {
    const list = form.querySelector('[data-order-list]');
    const input = form.querySelector('[name="selected_lines"]');
    list.addEventListener('click', function (event) {
        const button = event.target.closest('button[name="move"]');
        if (!button || button.disabled) return;
        event.preventDefault();
        const row = button.closest('[data-line-id]');
        if (button.value.endsWith(':up') && row.previousElementSibling) {
            list.insertBefore(row, row.previousElementSibling);
        } else if (button.value.endsWith(':down') && row.nextElementSibling) {
            list.insertBefore(row.nextElementSibling, row);
        }
        const rows = Array.from(list.children);
        input.value = rows.map(item => item.dataset.lineId).join(',');
        rows.forEach(function (item, index) {
            item.querySelector('[value$=":up"]').disabled = index === 0;
            item.querySelector('[value$=":down"]').disabled = index === rows.length - 1;
        });
        if (button.disabled) {
            row.querySelector(button.value.endsWith(':up') ? '[value$=":down"]' : '[value$=":up"]').focus();
        }
    });
});

/* Refresh only catalogue results; leave input focus and unfinished forms intact. */
(function () {
    const form = document.querySelector('[data-catalogue-filter]');
    const results = document.querySelector('[data-catalogue-results-region]');
    const status = document.querySelector('[data-catalogue-filter-status]');
    if (!form || !results) return;
    let timer;
    let controller;
    let version = 0;
    let composing = false;

    function filterUrl() {
        const url = new URL(window.location.href);
        ['species', 'q', 'tab', 'per_page', 'letter'].forEach(function (name) {
            url.searchParams.set(name, form.elements.namedItem(name).value);
        });
        ['page', 'edit_line', 'edit_crossing', 'panel'].forEach(name => url.searchParams.delete(name));
        url.hash = '';
        return url;
    }
    async function refresh(url, current) {
        controller = new AbortController();
        try {
            const response = await fetch(url.href, {signal: controller.signal, credentials: 'same-origin'});
            if (!response.ok) throw new Error('Unavailable');
            const html = await response.text();
            if (current !== version) return;
            const page = new DOMParser().parseFromString(html, 'text/html');
            const updated = page.querySelector('[data-catalogue-results-region]');
            if (!updated) throw new Error('Missing results');
            results.innerHTML = updated.innerHTML;
            form.elements.namedItem('tab').value = updated.dataset.activeTab;
            form.elements.namedItem('letter').value = updated.dataset.activeLetter;
            form.elements.namedItem('per_page').value = updated.dataset.pageSize;
            bindCataloguePanels(results);
            // The shared rows selector uses a normal submit outside this page.
            results.querySelectorAll('.page-size-form select').forEach(select => select.removeAttribute('onchange'));
            window.history.replaceState(null, '', url.pathname + url.search);
            if (status) status.textContent = '';
        } catch (error) {
            if (current === version && error.name !== 'AbortError' && status) {
                status.textContent = 'Could not update results. Press Enter in the name field to retry.';
            }
        } finally {
            if (current === version) results.removeAttribute('aria-busy');
        }
    }
    function schedule(url, delay) {
        clearTimeout(timer);
        if (controller) controller.abort();
        const current = ++version; // Invalidate old responses before the debounce.
        results.setAttribute('aria-busy', 'true');
        if (status) status.textContent = 'Updating results…';
        if (delay) timer = setTimeout(() => refresh(url, current), delay);
        else refresh(url, current);
    }
    form.addEventListener('submit', function (event) {
        event.preventDefault();
        schedule(filterUrl(), 0);
    });
    form.elements.namedItem('q').addEventListener('input', function () {
        if (!composing) schedule(filterUrl(), 200);
    });
    form.elements.namedItem('q').addEventListener('compositionstart', function () {
        composing = true;
        clearTimeout(timer);
        ++version;
        if (controller) controller.abort();
        results.removeAttribute('aria-busy');
    });
    form.elements.namedItem('q').addEventListener('compositionend', function () {
        composing = false;
        schedule(filterUrl(), 200);
    });
    form.elements.namedItem('species').addEventListener('change', function () {
        schedule(filterUrl(), 0);
    });
    results.querySelectorAll('.page-size-form select').forEach(select => select.removeAttribute('onchange'));
    results.addEventListener('change', function (event) {
        if (!event.target.matches('.page-size-form select')) return;
        form.elements.namedItem('per_page').value = event.target.value;
        schedule(filterUrl(), 0);
    });
    results.addEventListener('click', function (event) {
        const link = event.target.closest('.protocol-tabs a, .pagination a, .catalogue-letter-index a');
        if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0) return;
        const url = new URL(link.href, window.location.href);
        if (url.origin !== window.location.origin) return;
        event.preventDefault();
        form.elements.namedItem('tab').value = url.searchParams.get('tab') || 'lines';
        form.elements.namedItem('letter').value = url.searchParams.get('letter') || 'all';
        ['species', 'q', 'per_page'].forEach(name => url.searchParams.set(name, form.elements.namedItem(name).value));
        schedule(url, 0);
    });
})();
