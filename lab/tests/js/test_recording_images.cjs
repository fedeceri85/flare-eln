const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const source = readFileSync(join(__dirname, '../../static/lab/recording_images.js'), 'utf8');

function element(extra = {}) {
    const events = {};
    const classes = new Set();
    return Object.assign({
        hidden: false, disabled: false, textContent: '', value: '', tagName: 'BUTTON', dataset: {}, children: [],
        attrs: {}, classList: {remove: key => classes.delete(key), toggle(key) {
            if (classes.has(key)) { classes.delete(key); return false; }
            classes.add(key); return true;
        }},
        addEventListener(name, callback) { (events[name] ||= []).push(callback); },
        async emit(name, extra = {}) {
            const event = Object.assign({target: this, button: 0, preventDefault() { this.prevented = true; }}, extra);
            await Promise.all((events[name] || []).map(callback => callback(event)));
            return event;
        },
        setAttribute(key, value) { this.attrs[key] = value; },
        getAttribute(key) { return this.attrs[key] ?? null; },
        removeAttribute(key) { delete this[key]; },
        append(...children) { this.children.push(...children); },
        replaceChildren(...children) { this.children = children; },
        closest() { return null; },
        matches() { return false; },
        focus() { this.focused = true; },
        reset() { this.wasReset = true; },
    }, extra);
}

function fixture() {
    const names = ['status', 'viewer', 'stage', 'image', 'image-name', 'position', 'management',
        'upload-details', 'upload-form', 'remove-form', 'previous', 'next', 'zoom', 'download', 'close',
        'document-management', 'document-upload-details', 'document-upload-form', 'documents', 'tab-list'];
    const controls = Object.fromEntries(names.map(name => [name, element()]));
    controls.stage.replaceChildren = image => { controls.image = image; };
    controls['remove-form'].elements = {image_id: element()};
    // Native HTML forms expose named controls ahead of built-in properties.
    Object.defineProperty(controls['upload-form'], 'action', {get() { return {toString: () => '[object HTMLButtonElement]'}; }});
    Object.defineProperty(controls['remove-form'], 'action', {get() { return {toString: () => '[object RadioNodeList]'}; }});
    const imageTab = element({dataset: {galleryTabButton: 'images'}});
    const documentTab = element({dataset: {galleryTabButton: 'documents'}});
    const imagePanel = element({dataset: {galleryTabPanel: 'images'}});
    const documentPanel = element({dataset: {galleryTabPanel: 'documents'}});
    const submitButtons = [element(), element(), element()];
    const dialog = element({open: false,
        contains() { return true; },
        querySelector(selector) {
            if (selector === '[data-gallery-tab-list]') return controls['tab-list'];
            return controls[selector.slice('[data-gallery-'.length, -1)];
        },
        querySelectorAll(selector) {
            if (selector === '[data-gallery-tab-button]') return [imageTab, documentTab];
            if (selector === '[data-gallery-tab-panel]') return [imagePanel, documentPanel];
            return submitButtons;
        },
        showModal() { this.open = true; },
        close() { this.open = false; return this.emit('close'); },
    });
    const link = element({href: 'http://localhost/recordings/1/images/?return_url=/recordings/', dataset: {galleryTab: 'images'},
        closest(selector) { return selector === '[data-recording-gallery]' ? this : null; },
    });
    const documentLink = element({href: link.href, dataset: {galleryTab: 'documents'},
        closest(selector) { return selector === '[data-recording-gallery]' ? this : null; },
    });
    const document = element({
        createElement(tagName) { return element({tagName: tagName.toUpperCase()}); },
        querySelector() { return dialog; },
        querySelectorAll(selector) { return selector === '[data-recording-gallery][data-gallery-tab]' ? [link, documentLink] : []; },
    });
    const requests = [];
    const pending = [];
    runInNewContext(source, {
        document, URL, location: {href: 'http://localhost/recordings/'},
        FormData: class {
            constructor(form) { this.form = form; this.values = {}; }
            set(key, value) { this.values[key] = value; }
        },
        fetch(url, options) {
            requests.push({url, options});
            return new Promise((resolve, reject) => pending.push({resolve, reject}));
        },
    });
    const image = id => ({id: String(id), name: 'Image ' + id,
        url: '/images/' + id, download_url: '/images/' + id + '?download=1'});
    return {controls, dialog, link, documentLink, requests, image, imageTab, documentTab, imagePanel, documentPanel,
        ready() { return document.emit('DOMContentLoaded'); },
        open() { return document.emit('click', {target: link}); },
        openDocuments() { return document.emit('click', {target: documentLink}); },
        respond(images = [image(1), image(2)], extra = {}, ok = true) {
            pending.shift().resolve({ok, json: async () => ({images, documents: [], can_modify: true, ...extra})});
        },
        fail() { pending.shift().reject(new Error('offline')); },
    };
}

test('gallery loads lazily, supports navigation and enlargement, and returns focus on close', async () => {
    const ui = fixture(); await ui.ready();
    assert.equal(ui.requests.length, 0);
    const opening = ui.open();
    ui.respond(); await opening;
    assert.equal(ui.dialog.open, true);
    assert.equal(ui.controls.image.src, '/images/1');
    assert.equal(ui.controls.image.hidden, true);
    ui.controls.image.onload();
    assert.equal(ui.controls.image.hidden, false);
    assert.equal(ui.link.dataset.galleryCount, '2');
    await ui.controls.next.emit('click');
    assert.equal(ui.controls.image.src, '/images/2');
    await ui.dialog.emit('keydown', {key: 'ArrowRight'});
    assert.equal(ui.controls.image.src, '/images/1');
    await ui.controls.zoom.emit('click');
    assert.equal(ui.controls.zoom.attrs['aria-pressed'], 'true');
    await ui.controls.close.emit('click');
    assert.equal(ui.dialog.open, false);
    assert.equal(ui.link.focused, true);
});

test('images failing after the gallery opens are skipped while registered attachment counts remain visible', async () => {
    const ui = fixture(); await ui.ready();
    const opening = ui.open(); ui.respond(); await opening;
    ui.controls.image.onerror();
    assert.equal(ui.controls.image.src, '/images/2');
    assert.equal(ui.link.dataset.galleryCount, '2');
    ui.controls.image.onerror();
    assert.equal(ui.controls.viewer.hidden, true);
    assert.equal(ui.link.hidden, false);
    assert.match(ui.controls.status.textContent, /registered image attachment.*unavailable/);
});

test('an empty gallery remains usable for uploads and viewer permissions hide management', async () => {
    const ui = fixture(); await ui.ready();
    let opening = ui.open(); ui.respond([]); await opening;
    assert.equal(ui.controls.management.hidden, false);
    assert.equal(ui.controls['upload-details'].open, true);
    assert.equal(ui.controls['remove-form'].hidden, true);
    await ui.dialog.close();
    opening = ui.open(); ui.respond([ui.image(1)], {can_modify: false}); await opening;
    assert.equal(ui.controls.management.hidden, true);
});

test('late responses after closing the dialog are ignored', async () => {
    const ui = fixture(); await ui.ready();
    const opening = ui.open();
    await ui.dialog.close();
    ui.respond(); await opening;
    assert.equal(ui.controls.viewer.hidden, true);
    assert.equal(ui.controls.image.src, undefined);
});

test('a queued close event cannot clear an already reopened gallery', async () => {
    const ui = fixture(); await ui.ready();
    const opening = ui.open(); ui.respond(); await opening;
    const current = ui.controls.image;
    await ui.dialog.emit('close');
    assert.equal(ui.controls.image, current);
    assert.equal(current.src, '/images/1');
    current.onload();
    assert.equal(current.hidden, false);
});

test('upload and remove forms post their explicit action and update counts', async () => {
    const ui = fixture(); await ui.ready();
    const opening = ui.open(); ui.respond(); await opening;
    const upload = ui.controls['upload-form'].emit('submit', {submitter: {value: 'upload'}});
    assert.equal(ui.requests.at(-1).url, ui.link.href);
    assert.equal(ui.requests.at(-1).options.body.values.action, 'upload');
    ui.respond([ui.image(1), ui.image(2), ui.image(3)]); await upload;
    assert.equal(ui.link.dataset.galleryCount, '3');
    assert.equal(ui.controls['upload-form'].wasReset, true);
    const removal = ui.controls['remove-form'].emit('submit', {submitter: {value: 'remove'}});
    assert.equal(ui.requests.at(-1).url, ui.link.href);
    assert.equal(ui.requests.at(-1).options.body.values.action, 'remove');
    ui.respond([{...ui.image(2), name: '<script>Shown as text</script>'}, ui.image(3)]); await removal;
    assert.equal(ui.controls['image-name'].textContent, '<script>Shown as text</script>');
    assert.equal(ui.link.dataset.galleryCount, '2');

});

test('upload validation failures retain chosen files and show the server message', async () => {
    const ui = fixture(); await ui.ready();
    const opening = ui.open(); ui.respond(); await opening;
    ui.controls['upload-form'].wasReset = false;
    const upload = ui.controls['upload-form'].emit('submit', {submitter: {value: 'upload'}});
    ui.respond([], {errors: ['Image storage is unavailable.']}, false); await upload;
    assert.equal(ui.controls['upload-form'].wasReset, false);
    assert.equal(ui.controls.status.textContent, 'Image storage is unavailable.');
});

test('failed gallery requests remain closable', async () => {
    const ui = fixture(); await ui.ready();
    const opening = ui.open(); ui.fail(); await opening;
    assert.match(ui.controls.status.textContent, /Could not open/);
    await ui.controls.close.emit('click');
    assert.equal(ui.dialog.open, false);
});

test('PDF trigger opens the documents tab and uploads documents without previewing them', async () => {
    const ui = fixture(); await ui.ready();
    const opening = ui.openDocuments();
    ui.respond([], {documents: [{id: 'd1', name: '<protocol>.pdf', size_bytes: 2048, download_url: '/documents/d1'}], document_registered_count: 1});
    await opening;
    assert.equal(ui.documentPanel.hidden, false);
    assert.equal(ui.imagePanel.hidden, true);
    assert.equal(ui.documentLink.dataset.galleryCount, '1');
    const row = ui.controls.documents.children[0];
    assert.equal(row.children[0].textContent, '<protocol>.pdf · 2 KB');
    assert.equal(row.children[1].href, '/documents/d1');
    const upload = ui.controls['document-upload-form'].emit('submit', {submitter: {value: 'upload_documents'}});
    assert.equal(ui.requests.at(-1).options.body.values.action, 'upload_documents');
    ui.respond([], {documents: [], document_registered_count: 0}); await upload;
    assert.equal(ui.documentLink.hidden, true);
});
