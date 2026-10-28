/* Lazy private image gallery. Missing files are skipped, never shown as broken images. */
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-image-fallback]').forEach(image => {
        image.addEventListener('error', () => image.closest('[data-image-card]').remove());
        if (image.complete && !image.naturalWidth) image.closest('[data-image-card]').remove();
    });
    const dialog = document.querySelector('[data-image-dialog]');
    if (!dialog || !dialog.showModal) return;
    const find = key => dialog.querySelector('[data-gallery-' + key + ']');
    const status = find('status'), viewer = find('viewer'), stage = find('stage');
    let picture = find('image');
    const imageName = find('image-name'), position = find('position');
    const management = find('management');
    const uploadDetails = find('upload-details'), uploadForm = find('upload-form'), removeForm = find('remove-form');
    const previous = find('previous'), next = find('next'), zoom = find('zoom'), download = find('download');
    let registeredCount = 0;
    let images = [], index = 0, url = '', opener, generation = 0, imageGeneration = 0, busy = false;

    function updateLinks() {
        document.querySelectorAll('[data-gallery-count]').forEach(link => {
            if (new URL(link.href, location.href).pathname !== new URL(url, location.href).pathname) return;
            link.hidden = registeredCount === 0;
            link.textContent = 'Images (' + registeredCount + ')';
        });
    }
    function show() {
        const current = ++imageGeneration;
        index = images.length ? (index + images.length) % images.length : 0;
        const image = images[index];
        viewer.hidden = !image;
        removeForm.hidden = !image;
        stage.classList.remove('gallery-enlarged');
        zoom.setAttribute('aria-pressed', 'false');
        zoom.textContent = 'Enlarge';
        picture.onload = null; picture.onerror = null;
        picture.removeAttribute('src');
        // Each selection owns its image element, so late events from an earlier
        // request cannot hide or remove a newer selection.
        picture = document.createElement('img');
        const rendered = picture;
        rendered.setAttribute('data-gallery-image', '');
        rendered.hidden = true;
        stage.replaceChildren(rendered);
        updateLinks();
        if (!image) {
            status.textContent = registeredCount ? registeredCount + ' registered image attachment(s) currently unavailable. Check image storage.' : 'No images available.';
            return;
        }
        status.textContent = 'Loading image…';
        imageName.textContent = image.name;
        picture.alt = image.name;
        position.textContent = (index + 1) + ' / ' + images.length;
        previous.disabled = next.disabled = images.length < 2;
        download.href = image.download_url;
        removeForm.elements.image_id.value = image.id;
        picture.onload = function () {
            if (current !== imageGeneration) return;
            rendered.hidden = false;
            status.textContent = registeredCount > images.length ? (registeredCount - images.length) + ' registered image attachment(s) currently unavailable.' : '';
        };
        picture.onerror = function () {
            if (current !== imageGeneration) return;
            images = images.filter(item => item.id !== image.id);
            show();
        };
        picture.src = image.url;
    }
    function apply(payload) {
        const selected = images[index]?.id;
        images = payload.images;
        registeredCount = payload.registered_count ?? images.length;
        const selectedIndex = images.findIndex(image => image.id === selected);
        index = selectedIndex >= 0 ? selectedIndex : Math.min(index, Math.max(0, images.length - 1));
        management.hidden = !payload.can_modify;
        uploadDetails.open = images.length === 0;
        show();
    }
    document.addEventListener('click', async function (event) {
        const link = event.target.closest('[data-recording-gallery]');
        if (!link || event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        opener = link; url = link.href;
        const current = ++generation;
        images = []; index = 0;
        viewer.hidden = management.hidden = true;
        status.textContent = 'Loading images…';
        uploadForm.reset();
        // Controls named "action" shadow HTMLFormElement.action in real browsers.
        uploadForm.setAttribute('action', url);
        removeForm.setAttribute('action', url);
        dialog.showModal();
        try {
            const response = await fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
            if (!response.ok) throw new Error();
            const payload = await response.json();
            if (current === generation && dialog.open) apply(payload);
        } catch (_) {
            if (current === generation && dialog.open) status.textContent = 'Could not open the gallery. Close it and try again.';
        }
    });
    find('close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
        // Native close events are queued; an already reopened gallery owns its state.
        if (dialog.open) return;
        ++generation; ++imageGeneration;
        picture.onload = null; picture.onerror = null;
        picture.removeAttribute('src');
        if (opener && !opener.hidden) opener.focus();
    });
    previous.addEventListener('click', () => { if (!busy) { index--; show(); } });
    next.addEventListener('click', () => { if (!busy) { index++; show(); } });
    dialog.addEventListener('keydown', event => {
        if (busy || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault(); index += event.key === 'ArrowLeft' ? -1 : 1; show();
        }
    });
    zoom.addEventListener('click', () => {
        const enlarged = stage.classList.toggle('gallery-enlarged');
        zoom.setAttribute('aria-pressed', String(enlarged));
        zoom.textContent = enlarged ? 'Fit image' : 'Enlarge';
    });
    [uploadForm, removeForm].forEach(form => form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy) return;
        const current = generation;
        const data = new FormData(form);
        data.set('action', event.submitter?.value || (form === uploadForm ? 'upload' : 'remove'));
        busy = true;
        dialog.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
        status.textContent = form === uploadForm ? 'Uploading images…' : 'Saving changes…';
        try {
            const response = await fetch(form.getAttribute('action'), {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}});
            const payload = await response.json();
            if (current !== generation || !dialog.open) return;
            if (!response.ok) {
                status.textContent = payload.errors?.join(' ') || 'Changes could not be saved. Please try again.';
            } else {
                if (form === uploadForm) form.reset();
                apply(payload);
            }
        } catch (_) {
            if (current === generation && dialog.open) status.textContent = 'Changes could not be saved. Please try again.';
        } finally {
            busy = false;
            dialog.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = false; });
        }
    }));
});
