/* Both dialogs use authenticated endpoints; codes identify views, not permissions. */
(() => {
    const shareDialog = document.querySelector('[data-share-dialog]');
    const codeDialog = document.querySelector('[data-code-dialog]');
    if (!shareDialog || !codeDialog) return;
    const content = shareDialog.querySelector('[data-share-content]');
    const loading = shareDialog.querySelector('[data-share-loading]');
    const error = shareDialog.querySelector('[data-share-error]');
    const status = shareDialog.querySelector('[data-share-status]');
    const codeOutput = shareDialog.querySelector('[data-share-code]');
    const linkOutput = shareDialog.querySelector('[data-share-link]');
    const retry = shareDialog.querySelector('[data-share-retry]');
    const codeForm = codeDialog.querySelector('[data-code-form]');
    const codeInput = codeForm.querySelector('[data-code-input]');
    const codeError = codeForm.querySelector('[data-code-error]');
    const openButton = codeForm.querySelector('[data-code-submit]');
    let shareVersion = 0;
    let openVersion = 0;
    let lastShare;
    let currentShare;
    let shareOpener;
    let codeOpener;

    function closeShare() {
        shareVersion++;
        shareDialog.close();
        shareOpener?.focus();
    }
    function closeCode() {
        openVersion++;
        openButton.disabled = false;
        codeDialog.close();
        codeOpener?.focus();
    }
    shareDialog.querySelector('[data-share-close]').addEventListener('click', closeShare);
    codeDialog.querySelector('[data-code-close]').addEventListener('click', closeCode);
    shareDialog.addEventListener('cancel', event => { event.preventDefault(); closeShare(); });
    codeDialog.addEventListener('cancel', event => { event.preventDefault(); closeCode(); });

    async function createShare(endpoint) {
        const version = ++shareVersion;
        lastShare = endpoint;
        currentShare = null;
        content.hidden = true;
        error.hidden = true;
        retry.hidden = true;
        loading.hidden = false;
        status.textContent = '';
        shareDialog.setAttribute('aria-busy', 'true');
        if (!shareDialog.open) shareDialog.showModal();
        try {
            const response = await fetch(endpoint.pathname, {
                method: 'POST', credentials: 'same-origin',
                headers: {
                    'X-CSRFToken': shareDialog.querySelector('[name="csrfmiddlewaretoken"]').value,
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Accept': 'application/json',
                },
                body: endpoint.searchParams.toString(),
            });
            if (version !== shareVersion) return;
            if (response.redirected) throw new Error('Your session may have expired. Reload the page and try again.');
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Could not create a share code. Please try again.');
            if (!result.code || !result.url) throw new Error('Could not create a share code. Please try again.');
            currentShare = result;
            codeOutput.textContent = result.code;
            linkOutput.href = result.url;
            linkOutput.textContent = result.url;
            content.hidden = false;
            codeOutput.focus();
        } catch (failure) {
            if (version !== shareVersion) return;
            error.textContent = failure.message || 'Could not create a share code. Please try again.';
            error.hidden = false;
            retry.hidden = false;
        } finally {
            if (version === shareVersion) {
                loading.hidden = true;
                shareDialog.removeAttribute('aria-busy');
            }
        }
    }
    retry.addEventListener('click', () => { if (lastShare) createShare(lastShare); });

    async function copy(value, label) {
        if (!value) return;
        try {
            await navigator.clipboard.writeText(value);
            status.textContent = label + ' copied.';
        } catch (_) {
            window.prompt('Copy ' + label.toLowerCase() + ':', value);
        }
    }
    shareDialog.querySelector('[data-share-copy-code]').addEventListener('click', () => copy(currentShare?.code, 'Code'));
    shareDialog.querySelector('[data-share-copy-link]').addEventListener('click', () => copy(currentShare?.url, 'Link'));

    document.addEventListener('click', event => {
        const shareButton = event.target.closest('[data-share-url]');
        if (shareButton) {
            event.preventDefault();
            shareOpener = shareButton;
            const endpoint = new URL(shareButton.dataset.shareUrl, window.location.href);
            if (endpoint.origin !== window.location.origin) return;
            if (shareButton.hasAttribute('data-share-animals')) {
                const tab = document.querySelector('[data-mouse-tab][aria-selected="true"]');
                if (tab) endpoint.searchParams.set('mouse_editor_tab', tab.dataset.mouseTab);
            }
            createShare(endpoint);
            return;
        }
        const openLink = event.target.closest('[data-open-code]');
        if (!openLink || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0) return;
        event.preventDefault();
        codeOpener = openLink;
        codeError.hidden = true;
        codeInput.removeAttribute('aria-invalid');
        if (!codeDialog.open) codeDialog.showModal();
        codeInput.focus();
        codeInput.select();
    });

    codeForm.addEventListener('submit', async event => {
        event.preventDefault();
        if (!codeForm.reportValidity()) return;
        const version = ++openVersion;
        openButton.disabled = true;
        codeError.hidden = true;
        codeInput.removeAttribute('aria-invalid');
        try {
            const url = new URL(codeForm.action, window.location.href);
            url.searchParams.set('code', codeInput.value);
            const response = await fetch(url.href, {credentials: 'same-origin', headers: {'Accept': 'application/json'}});
            if (version !== openVersion) return;
            if (response.redirected) throw new Error('Your session may have expired. Reload the page and try again.');
            const result = await response.json();
            if (!response.ok || !result.url) throw new Error(result.error || 'Could not open this code. Please try again.');
            window.location.assign(result.url);
        } catch (failure) {
            if (version !== openVersion) return;
            codeError.textContent = failure.message || 'Could not open this code. Please try again.';
            codeError.hidden = false;
            codeInput.setAttribute('aria-invalid', 'true');
            codeInput.focus();
        } finally {
            if (version === openVersion) openButton.disabled = false;
        }
    });
    codeInput.addEventListener('input', () => {
        openVersion++;
        openButton.disabled = false;
        codeError.hidden = true;
        codeInput.removeAttribute('aria-invalid');
    });
})();
