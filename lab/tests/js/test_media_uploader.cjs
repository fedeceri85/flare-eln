const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const source = readFileSync(join(__dirname, '../../static/lab/media_uploader.js'), 'utf8');

function element(extra = {}) {
    const events = {};
    return Object.assign({hidden: false, disabled: false, value: '', textContent: '', children: [], attrs: {}, style: {},
        addEventListener(name, handler) { (events[name] ||= []).push(handler); },
        async emit(name, extra = {}) {
            const event = {target: this, preventDefault() { this.prevented = true; }, ...extra};
            await Promise.all((events[name] || []).map(handler => handler(event)));
            return event;
        },
        setAttribute(key, value) { this.attrs[key] = value; },
        getAttribute(key) { return this.attrs[key]; },
        removeAttribute(key) { delete this.attrs[key]; },
        querySelectorAll() { return []; },
        append(...children) { this.children.push(...children); },
        appendChild(child) { this.children.push(child); },
        replaceChildren() { this.children = []; },
        click() { this.clicked = true; },
    }, extra);
}

function fixture(immediate = false) {
    const controls = Object.fromEntries(['input', 'browse', 'dropzone', 'status', 'items', 'retry', 'upload-fallback'].map(name => [name, element()]));
    controls.input.files = [];
    controls.input.parentElement = element();
    const root = element({dataset: immediate ? {mediaUploadForm: 'upload', mediaRemoveForm: 'remove'} : {},
        querySelector(selector) { return controls[selector === '[data-media-input]' ? 'input' : selector.slice('[data-media-'.length, -1)]; },
    });
    const upload = element({reset() { this.wasReset = true; controls.input.files = []; }});
    upload.setAttribute('action', '/import/images/');
    const remove = element(); remove.setAttribute('action', '/import/images/');
    const settings = element({unsavedValue: 'edited grouping'});
    const revisions = [element({value: 'old'}), element({value: 'old'})];
    const requests = [], pending = [], revoked = [];
    runInNewContext(source, {
        document: {
            addEventListener(name, handler) { handler(); },
            querySelectorAll(selector) { return selector === '[data-media-uploader]' ? [root] : revisions; },
            getElementById(id) { return id === 'upload' ? upload : remove; },
            querySelector() { return settings; },
            createElement(tagName) { return element({tagName}); },
        },
        window: {addEventListener() {}},
        URL: {createObjectURL(file) { return 'blob:' + file.name; }, revokeObjectURL(url) { revoked.push(url); }},
        DataTransfer: class {
            constructor() { this.files = []; this.items = {add: file => this.files.push(file)}; }
        },
        FormData: class {
            constructor(form) { this.form = form; this.files = [...controls.input.files]; this.fields = {}; }
            set(key, value) { this.fields[key] = value; }
        },
        fetch(url, options) {
            requests.push({url, options});
            return new Promise(resolve => pending.push(resolve));
        },
    });
    return {controls, root, upload, settings, revisions, requests, revoked,
        async choose(files) { controls.input.files = files; await controls.input.emit('change'); },
        async drop(files) { return controls.dropzone.emit('drop', {dataTransfer: {files}}); },
        async finish(payload = {html: '<div>saved images</div>', count: 1, revision: 'new'}, ok = true) {
            pending.shift()({ok, json: async () => payload});
            await new Promise(resolve => setImmediate(resolve));
        },
    };
}
const file = name => ({name, size: 100, lastModified: 1});

test('Browse opens the picker and manual drag/drop queues removable files without uploading', async () => {
    const ui = fixture();
    await ui.controls.browse.emit('click');
    assert.equal(ui.controls.input.clicked, true);
    assert.equal(ui.controls.browse.hidden, false);
    assert.equal(ui.controls.input.parentElement.hidden, true);
    await ui.drop([file('gel.png')]);
    await ui.choose([file('second.jpg')]);
    assert.deepEqual(ui.controls.input.files.map(item => item.name), ['gel.png', 'second.jpg']);
    assert.equal(ui.requests.length, 0);
    assert.equal(ui.controls.items.children.length, 2);
    const thumbnail = ui.controls.items.children[0].children[0];
    assert.equal(thumbnail.width, 100);
    assert.equal(thumbnail.height, 72);
    assert.equal(thumbnail.style.objectFit, "contain");
    const remove = ui.controls.items.children[0].children[2];
    await remove.emit('click');
    assert.deepEqual(ui.controls.input.files.map(item => item.name), ['second.jpg']);
    assert.ok(ui.revoked.includes('blob:gel.png'));
});

test('unsupported drops preserve the manual queue and show a validation message', async () => {
    const ui = fixture();
    await ui.choose([file('gel.png')]);
    await ui.drop([file('document.pdf')]);
    assert.deepEqual(ui.controls.input.files.map(item => item.name), ['gel.png']);
    assert.match(ui.controls.status.textContent, /JPEG, PNG, WebP or TIFF/);
});

test('import uploads are immediate and update revisions while retaining unsaved settings', async () => {
    const ui = fixture(true);
    await ui.drop([file('gel.png')]);
    assert.equal(ui.requests.length, 1);
    assert.equal(ui.requests[0].url, '/import/images/');
    assert.equal(ui.requests[0].options.body.files.length, 1);
    assert.equal(ui.controls.input.disabled, true);
    assert.equal((await ui.settings.emit('submit')).prevented, true);
    await ui.finish();
    assert.equal(ui.settings.unsavedValue, 'edited grouping');
    assert.equal(ui.revisions[0].value, 'new');
    assert.equal(ui.revisions[1].defaultValue, 'new');
    assert.equal(ui.controls.items.innerHTML, '<div>saved images</div>');
    assert.equal(ui.upload.wasReset, true);
    assert.equal(ui.controls.input.disabled, false);
    assert.notEqual((await ui.settings.emit('submit')).prevented, true);
});

test('failed import uploads retain files and can be retried', async () => {
    const ui = fixture(true);
    await ui.choose([file('gel.webp')]);
    await ui.finish({errors: ['Storage unavailable.']}, false);
    assert.equal(ui.controls.retry.hidden, false);
    assert.equal(ui.controls.input.files.length, 1);
    assert.equal(ui.controls.status.textContent, 'Storage unavailable.');
    const retry = ui.controls.retry.emit('click');
    assert.equal(ui.requests.length, 2);
    await ui.finish();
    await retry;
    assert.equal(ui.controls.retry.hidden, true);
});

test('removing a shared image posts its ID without submitting edited settings', async () => {
    const ui = fixture(true);
    const button = element({value: 'image-id', closest() { return this; }});
    const click = ui.root.emit('click', {target: button});
    await click;
    assert.equal(ui.requests[0].options.body.fields.image_id, 'image-id');
    await ui.finish({html: '', count: 0, revision: 'removed'});
    assert.equal(ui.controls.items.innerHTML, '');
    assert.equal(ui.revisions[0].value, 'removed');
    assert.equal(ui.settings.unsavedValue, 'edited grouping');
});

 test('TIFF drops accept both file extensions', async () => {
    const ui = fixture();
    await ui.drop([file('gel.tif'), file('scan.TIFF')]);
    assert.deepEqual(ui.controls.input.files.map(item => item.name), ['gel.tif', 'scan.TIFF']);
});
