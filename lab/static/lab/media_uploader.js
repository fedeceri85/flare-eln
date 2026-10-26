/* One media box for immediate import uploads and files saved with a new recording. */
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-media-uploader]').forEach(root => {
        const input = root.querySelector('[data-media-input]');
        const browse = root.querySelector('[data-media-browse]');
        const zone = root.querySelector('[data-media-dropzone]');
        const status = root.querySelector('[data-media-status]');
        const items = root.querySelector('[data-media-items]');
        const retry = root.querySelector('[data-media-retry]');
        const uploadForm = root.dataset.mediaUploadForm ? document.getElementById(root.dataset.mediaUploadForm) : null;
        const removeForm = uploadForm ? document.getElementById(root.dataset.mediaRemoveForm) : null;
        let busy = false, files = [], previews = [];
        root.setAttribute('data-media-ready', 'true');
        input.parentElement.hidden = true;
        browse.hidden = false;
        const fallback = root.querySelector('[data-media-upload-fallback]');
        if (fallback) fallback.hidden = true;
        browse.addEventListener('click', () => input.click());

        function hideUnavailable() {
            items.querySelectorAll('img').forEach(image => {
                image.addEventListener('error', () => { image.hidden = true; });
                if (image.complete && !image.naturalWidth) image.hidden = true;
            });
        }
        hideUnavailable();
        function setFiles(selected) {
            const transfer = new DataTransfer();
            selected.forEach(file => transfer.items.add(file));
            input.files = transfer.files;
        }
        function showPending() {
            previews.forEach(url => URL.revokeObjectURL(url));
            previews = [];
            items.replaceChildren();
            files.forEach((file, index) => {
                const item = document.createElement('div'); item.className = 'media-item';
                const image = document.createElement('img');
                image.alt = file.name;
                image.width = 100; image.height = 72;
                image.style.objectFit = 'contain';
                image.style.maxWidth = '100%';
                image.style.flexShrink = '0';
                image.src = URL.createObjectURL(file); previews.push(image.src);
                image.addEventListener('error', () => { image.hidden = true; });
                const label = document.createElement('strong'); label.textContent = file.name;
                const remove = document.createElement('button'); remove.type = 'button';
                remove.className = 'button button-danger'; remove.textContent = 'Remove';
                remove.addEventListener('click', () => {
                    files.splice(index, 1); setFiles(files); showPending();
                });
                item.append(image, label, remove); items.appendChild(item);
            });
            status.textContent = files.length ? files.length + (files.length === 1 ? ' image will' : ' images will') + ' be saved with this recording.' : '';
        }
        function updateRevisions(revision) {
            document.querySelectorAll('input[name="revision"]').forEach(field => {
                field.value = revision;
                field.defaultValue = revision;
            });
        }
        async function send(form, imageId) {
            if (busy) return;
            const data = new FormData(form);
            if (imageId) data.set('image_id', imageId);
            busy = true;
            input.disabled = browse.disabled = true;
            if (retry) retry.hidden = true;
            status.textContent = imageId ? 'Removing image…' : 'Uploading images…';
            try {
                const response = await fetch(form.getAttribute('action'), {
                    method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.errors?.join(' ') || 'Images could not be saved. Please try again.');
                if (!imageId) form.reset();
                updateRevisions(payload.revision);
                items.innerHTML = payload.html;
                hideUnavailable();
                status.textContent = payload.count ? payload.count + (payload.count === 1 ? ' image ready' : ' images ready') + ' for this import.' : 'No images attached.';
            } catch (error) {
                status.textContent = error.message || 'Images could not be saved. Please try again.';
                if (retry && !imageId) retry.hidden = false;
            } finally {
                busy = false;
                input.disabled = browse.disabled = false;
            }
        }
        function choose(selected) {
            if (busy) return;
            const incoming = Array.from(selected);
            if (incoming.some(file => !/\.(jpe?g|png|webp)$/i.test(file.name))) {
                status.textContent = 'Choose JPEG, PNG or WebP images.';
                if (!uploadForm) setFiles(files);
                return;
            }
            const combined = uploadForm ? incoming : [...files, ...incoming].filter((file, index, all) =>
                all.findIndex(other => other.name === file.name && other.size === file.size && other.lastModified === file.lastModified) === index);
            if (combined.length > 10 || combined.some(file => file.size > 20 * 1024 * 1024)) {
                status.textContent = 'Choose up to 10 images, no larger than 20 MB each.';
                if (!uploadForm) setFiles(files);
                return;
            }
            if (!combined.length) return;
            setFiles(combined);
            if (uploadForm) send(uploadForm);
            else { files = combined; showPending(); }
        }
        input.addEventListener('change', () => choose(input.files));
        zone.addEventListener('dragover', event => {
            event.preventDefault(); zone.setAttribute('data-dragging', 'true');
        });
        zone.addEventListener('dragleave', () => zone.removeAttribute('data-dragging'));
        zone.addEventListener('drop', event => {
            event.preventDefault(); zone.removeAttribute('data-dragging'); choose(event.dataTransfer.files);
        });
        if (uploadForm) {
            uploadForm.addEventListener('submit', event => { event.preventDefault(); choose(input.files); });
            retry.addEventListener('click', () => send(uploadForm));
            root.addEventListener('click', event => {
                const button = event.target.closest('[data-media-remove]');
                if (!button) return;
                event.preventDefault(); send(removeForm, button.value);
            });
            const settingsForm = document.querySelector('[data-import-form]');
            settingsForm?.addEventListener('submit', event => {
                if (busy) { event.preventDefault(); status.textContent = 'Please wait for the image upload to finish before continuing.'; }
            });
        }
        window.addEventListener('pagehide', () => previews.forEach(url => URL.revokeObjectURL(url)));
    });
});
