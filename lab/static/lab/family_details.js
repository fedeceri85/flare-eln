/* Family reads are independent of editable inputs and fetched once per section. */
(() => {
    async function load(root) {
        if (!root || root.dataset.familyLoaded === 'true' || root.dataset.familyLoading === 'true') return;
        const content = root.querySelector('[data-family-content]');
        const status = root.querySelector('[data-family-status]');
        const retry = root.querySelector('[data-family-retry]');
        root.dataset.familyLoading = 'true';
        status.textContent = 'Loading…';
        retry.hidden = true;
        try {
            const response = await fetch(root.dataset.familyLoadUrl, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
            if (!response.ok || response.redirected) throw new Error('Family lookup failed');
            content.innerHTML = await response.text();
            root.dataset.familyLoaded = 'true';
            status.textContent = '';
        } catch (_) {
            status.textContent = 'Could not load parents and offspring. Please retry.';
            retry.hidden = false;
        } finally {
            root.dataset.familyLoading = 'false';
        }
    }
    document.addEventListener('mouse-editor:tab-activated', event => {
        if (event.detail.tab === 'family') load(document.getElementById('mouse-panel-family'));
    });
    document.addEventListener('click', event => {
        const retry = event.target.closest('[data-family-retry]');
        if (retry) load(retry.closest('[data-family-load-url]'));
    });
})();
