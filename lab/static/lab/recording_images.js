/* Lazy private image gallery. Missing files are skipped, never shown as broken images. */
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-image-fallback]').forEach(image => {
        image.addEventListener('error', () => image.closest('[data-image-card]').remove());
        if (image.complete && !image.naturalWidth) image.closest('[data-image-card]').remove();
    });
    const dialog = document.querySelector('[data-image-dialog]');
    if (!dialog || !dialog.showModal) return;
    dialog.querySelector('[data-gallery-tab-list]').setAttribute('role', 'tablist');
    dialog.querySelectorAll('[data-gallery-tab-button]').forEach(tab => tab.setAttribute('role', 'tab'));
    dialog.querySelectorAll('[data-gallery-tab-panel]').forEach(panel => panel.setAttribute('role', 'tabpanel'));
    const find = key => dialog.querySelector('[data-gallery-' + key + ']');
    const status = find('status'), viewer = find('viewer'), stage = find('stage');
    let picture = find('image');
    const imageName = find('image-name'), position = find('position');
    const management = find('management'), documentManagement = find('document-management');
    const uploadDetails = find('upload-details'), uploadForm = find('upload-form'), removeForm = find('remove-form');
    const documentUploadDetails = find('document-upload-details'), documentUploadForm = find('document-upload-form');
    const documentsList = find('documents');
    const previous = find('previous'), next = find('next'), zoom = find('zoom'), download = find('download');
    let registeredCount = 0, documentRegisteredCount = 0, canModify = false;
    let images = [], documents = [], index = 0, url = '', opener, generation = 0, imageGeneration = 0, busy = false;

    function selectTab(name, focus = false) {
        dialog.querySelectorAll('[data-gallery-tab-button]').forEach(tab => {
            const selected = tab.dataset.galleryTabButton === name;
            tab.setAttribute('aria-selected', String(selected));
            tab.tabIndex = selected ? 0 : -1;
            if (selected && focus) tab.focus();
        });
        dialog.querySelectorAll('[data-gallery-tab-panel]').forEach(panel => {
            panel.hidden = panel.dataset.galleryTabPanel !== name;
        });
    }

    function updateLinks() {
        document.querySelectorAll('[data-recording-gallery][data-gallery-tab]').forEach(link => {
            if (new URL(link.href, location.href).pathname !== new URL(url, location.href).pathname) return;
            const count = link.dataset.galleryTab === 'documents' ? documentRegisteredCount : registeredCount;
            link.hidden = count === 0;
            link.dataset.galleryCount = String(count);
            const label = link.dataset.galleryTab === 'documents' ? 'PDF documents' : 'Images';
            link.setAttribute('aria-label', label + ' (' + count + ')');
            const hiddenLabel = link.querySelector?.('[data-gallery-label]');
            if (hiddenLabel) hiddenLabel.textContent = label + ' (' + count + ')';
        });
    }
    function formatBytes(bytes) {
        return bytes < 1024 * 1024 ? Math.max(1, Math.round(bytes / 1024)) + ' KB' : (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }
    function renderDocuments(unavailable = 0) {
        documentsList.replaceChildren();
        if (!documents.length) {
            const empty = document.createElement('p');
            empty.textContent = documentRegisteredCount ? 'No registered PDF documents are currently available.' : 'No PDF documents attached.';
            documentsList.append(empty);
        }
        documents.forEach(item => {
            const row = document.createElement('div'); row.className = 'gallery-document-row';
            const name = document.createElement('span'); name.textContent = item.name + ' · ' + formatBytes(item.size_bytes);
            const downloadLink = document.createElement('a'); downloadLink.className = 'button'; downloadLink.href = item.download_url; downloadLink.textContent = 'Download';
            row.append(name, downloadLink);
            if (canModify) {
                const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button button-danger';
                remove.textContent = 'Remove'; remove.dataset.galleryRemoveDocument = item.id; row.append(remove);
            }
            documentsList.append(row);
        });
        if (unavailable) {
            const warning = document.createElement('p'); warning.className = 'message';
            warning.textContent = unavailable + ' registered PDF document(s) currently unavailable.'; documentsList.append(warning);
        }
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
        documents = payload.documents || [];
        documentRegisteredCount = payload.document_registered_count ?? documents.length;
        canModify = payload.can_modify;
        const selectedIndex = images.findIndex(image => image.id === selected);
        index = selectedIndex >= 0 ? selectedIndex : Math.min(index, Math.max(0, images.length - 1));
        management.hidden = !payload.can_modify;
        documentManagement.hidden = !payload.can_modify;
        uploadDetails.open = images.length === 0;
        documentUploadDetails.open = documents.length === 0;
        renderDocuments(payload.document_unavailable_count || 0);
        show();
    }
    document.addEventListener('click', async function (event) {
        const removeDocument = event.target.closest('[data-gallery-remove-document]');
        if (removeDocument && dialog.contains(removeDocument)) {
            if (busy) return;
            const data = new FormData(); data.set('action', 'remove_document'); data.set('document_id', removeDocument.dataset.galleryRemoveDocument);
            await submitChange(data, 'Saving changes…');
            return;
        }
        const tab = event.target.closest('[data-gallery-tab-button]');
        if (tab && dialog.contains(tab)) { selectTab(tab.dataset.galleryTabButton, true); return; }
        const link = event.target.closest('[data-recording-gallery]');
        if (!link || event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        opener = link; url = link.href;
        const current = ++generation;
        images = []; documents = []; index = 0;
        viewer.hidden = management.hidden = true;
        documentManagement.hidden = true;
        selectTab(link.dataset.galleryTab || 'images');
        status.textContent = 'Loading attachments…';
        uploadForm.reset();
        documentUploadForm.reset();
        // Controls named "action" shadow HTMLFormElement.action in real browsers.
        uploadForm.setAttribute('action', url);
        removeForm.setAttribute('action', url);
        documentUploadForm.setAttribute('action', url);
        dialog.showModal();
        try {
            const response = await fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
            if (!response.ok) throw new Error();
            const payload = await response.json();
            if (current === generation && dialog.open) apply(payload);
        } catch (_) {
            if (current === generation && dialog.open) status.textContent = 'Could not open the attachments. Close and try again.';
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
        if (event.target.matches('[data-gallery-tab-button]') && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
            event.preventDefault(); selectTab(event.target.dataset.galleryTabButton === 'images' ? 'documents' : 'images', true); return;
        }
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault(); index += event.key === 'ArrowLeft' ? -1 : 1; show();
        }
    });
    zoom.addEventListener('click', () => {
        const enlarged = stage.classList.toggle('gallery-enlarged');
        zoom.setAttribute('aria-pressed', String(enlarged));
        zoom.textContent = enlarged ? 'Fit image' : 'Enlarge';
    });
    async function submitChange(data, message) {
        if (busy) return false;
        const current = generation;
        busy = true;
        dialog.querySelectorAll('button[type="submit"], [data-gallery-remove-document]').forEach(button => { button.disabled = true; });
        status.textContent = message;
        try {
            const response = await fetch(url, {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}});
            const payload = await response.json();
            if (current !== generation || !dialog.open) return false;
            if (!response.ok) status.textContent = payload.errors?.join(' ') || 'Changes could not be saved. Please try again.';
            else { apply(payload); return true; }
        } catch (_) {
            if (current === generation && dialog.open) status.textContent = 'Changes could not be saved. Please try again.';
        } finally {
            busy = false;
            dialog.querySelectorAll('button[type="submit"], [data-gallery-remove-document]').forEach(button => { button.disabled = false; });
        }
    }
    [uploadForm, removeForm, documentUploadForm].forEach(form => form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy) return;
        const data = new FormData(form);
        const defaultAction = form === uploadForm ? 'upload' : form === documentUploadForm ? 'upload_documents' : 'remove';
        data.set('action', event.submitter?.value || defaultAction);
        const saved = await submitChange(data, form === uploadForm ? 'Uploading images…' : form === documentUploadForm ? 'Uploading documents…' : 'Saving changes…');
        if (saved && (form === uploadForm || form === documentUploadForm)) form.reset();
    }));
});
