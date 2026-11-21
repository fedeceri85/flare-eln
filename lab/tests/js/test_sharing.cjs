const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../../static/lab/sharing.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function element(extra = {}) {
    return Object.assign({hidden: false, textContent: '', value: '', attrs: {}, events: {}, focused: false,
        addEventListener(name, callback) {this.events[name] = callback;},
        emit(name, event = {}) {return this.events[name]?.({preventDefault() {}, ...event});},
        setAttribute(name, value) {this.attrs[name] = value;},
        removeAttribute(name) {delete this.attrs[name];},
        focus() {this.focused = true;}, select() {this.selected = true;},
        showModal() {this.open = true;}, close() {this.open = false;},
    }, extra);
}
function fixture() {
    const shareParts = {};
    for (const name of ['content', 'loading', 'error', 'status', 'code', 'link', 'retry', 'close', 'copy-code', 'copy-link']) shareParts[name] = element();
    const csrf = {value: 'csrf-token'};
    const share = element({querySelector(selector) {return selector.includes('csrfmiddlewaretoken') ? csrf : shareParts[selector.slice(12, -1)];}});
    const input = element(); const openError = element(); const openButton = element(); const codeClose = element();
    const form = element({action: 'http://localhost/share/open/', reportValidity: () => true, querySelector(selector) {
        return {'[data-code-input]': input, '[data-code-error]': openError, '[data-code-submit]': openButton}[selector];
    }});
    const codeDialog = element({querySelector(selector) {return {'[data-code-form]': form, '[data-code-close]': codeClose}[selector];}});
    const requests = [], copied = [], navigated = [], prompts = [], events = {};
    const document = {querySelector(selector) {
        return {'[data-share-dialog]': share, '[data-code-dialog]': codeDialog,
                '[data-mouse-tab][aria-selected="true"]': {dataset: {mouseTab: 'family'}}}[selector];
    }, addEventListener(name, fn) {events[name] = fn;}};
    runInNewContext(source, {document, URL,
        window: {location: {href: 'http://localhost/mice/', origin: 'http://localhost', assign: url => navigated.push(url)}, prompt: (...args) => prompts.push(args)},
        navigator: {clipboard: {writeText: async text => copied.push(text)}},
        fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
    });
    function shareButton(path = '/share/animals/?selected_mouse=A&selected_mouse=B', animals = true) {
        return element({dataset: {shareUrl: path}, hasAttribute: name => animals && name === 'data-share-animals',
            closest: selector => selector === '[data-share-url]' ? true : null});
    }
    return {share, parts: shareParts, input, form, openError, openButton, codeClose, codeDialog, requests, copied, navigated, prompts,
        clickShare(path, animals) {
            const button = shareButton(path, animals);
            events.click({preventDefault() {}, target: {closest: selector => selector === '[data-share-url]' ? button : null}});
            return button;
        },
        openEntry() {
            const link = element();
            events.click({preventDefault() {}, target: {closest: selector => selector === '[data-open-code]' ? link : null}});
            return link;
        },
        async respond(index, payload, status = 200, redirected = false) {
            requests[index].resolve({ok: status >= 200 && status < 300, redirected, json: async () => payload});
            await flush();
        },
    };
}

test('Share opens a modal, POSTs the animal selection and active tab, and copies code or link', async () => {
    const ui = fixture();
    ui.clickShare();
    assert.equal(ui.share.open, true);
    assert.equal(ui.parts.loading.hidden, false);
    assert.equal(ui.requests[0].url, '/share/animals/');
    assert.equal(ui.requests[0].options.method, 'POST');
    assert.equal(ui.requests[0].options.headers['X-CSRFToken'], 'csrf-token');
    const data = new URLSearchParams(ui.requests[0].options.body);
    assert.deepEqual(data.getAll('selected_mouse'), ['A', 'B']);
    assert.equal(data.get('mouse_editor_tab'), 'family');
    await ui.respond(0, {code: 'm-maple-otter', url: 'http://localhost/s/m-maple-otter/'});
    assert.equal(ui.parts.code.textContent, 'm-maple-otter');
    assert.equal(ui.parts.code.focused, true);
    assert.equal(ui.parts.content.hidden, false);
    assert.equal(ui.parts.link.href, 'http://localhost/s/m-maple-otter/');
    await ui.parts['copy-code'].emit('click');
    await ui.parts['copy-link'].emit('click');
    assert.deepEqual(ui.copied, ['m-maple-otter', 'http://localhost/s/m-maple-otter/']);
    assert.equal(ui.parts.status.textContent, 'Link copied.');
});

test('recording sharing uses its own endpoint without animal tab state', async () => {
    const ui = fixture();
    ui.clickShare('/share/recordings/17/', false);
    assert.equal(ui.requests[0].url, '/share/recordings/17/');
    assert.equal(ui.requests[0].options.body, '');
    await ui.respond(0, {code: 'r-copper-meadow', url: 'http://localhost/s/r-copper-meadow/'});
    assert.equal(ui.parts.code.textContent, 'r-copper-meadow');
});

test('share errors stay in the modal and can be retried', async () => {
    const ui = fixture();
    ui.clickShare();
    await ui.respond(0, {error: 'Animal no longer available'}, 400);
    assert.equal(ui.parts.error.hidden, false);
    assert.equal(ui.parts.error.textContent, 'Animal no longer available');
    assert.equal(ui.parts.content.hidden, true);
    assert.equal(ui.parts.retry.hidden, false);
    ui.parts.retry.emit('click');
    await ui.respond(1, {code: 'm-maple-otter', url: 'http://localhost/s/m-maple-otter/'});
    assert.equal(ui.parts.error.hidden, true);
    assert.equal(ui.parts.content.hidden, false);
});

test('late share responses cannot overwrite the most recently opened target', async () => {
    const ui = fixture();
    ui.clickShare('/share/recordings/1/', false);
    ui.clickShare('/share/recordings/2/', false);
    await ui.respond(1, {code: 'r-copper-meadow', url: 'http://localhost/s/r-copper-meadow/'});
    await ui.respond(0, {code: 'r-maple-otter', url: 'http://localhost/s/r-maple-otter/'});
    assert.equal(ui.parts.code.textContent, 'r-copper-meadow');
});

test('Enter code validates inline, preserves input, and navigates to the short URL on success', async () => {
    const ui = fixture();
    ui.openEntry();
    assert.equal(ui.codeDialog.open, true);
    ui.input.value = 'M MAPLE OTTER';
    const failed = ui.form.emit('submit');
    await ui.respond(0, {error: 'Code not found'}, 404);
    await failed;
    assert.equal(ui.openError.hidden, false);
    assert.equal(ui.input.value, 'M MAPLE OTTER');
    assert.equal(ui.input.attrs['aria-invalid'], 'true');
    assert.deepEqual(ui.navigated, []);
    ui.input.emit('input');
    const success = ui.form.emit('submit');
    assert.equal(new URL(ui.requests[1].url).searchParams.get('code'), 'M MAPLE OTTER');
    await ui.respond(1, {url: '/s/m-maple-otter/'});
    await success;
    assert.deepEqual(ui.navigated, ['/s/m-maple-otter/']);
});

test('closing Enter code cancels pending navigation and restores the trigger focus', async () => {
    const ui = fixture();
    const opener = ui.openEntry();
    ui.input.value = 'm-maple-otter';
    const pending = ui.form.emit('submit');
    ui.codeClose.emit('click');
    await ui.respond(0, {url: '/s/m-maple-otter/'});
    await pending;
    assert.deepEqual(ui.navigated, []);
    assert.equal(ui.codeDialog.open, false);
    assert.equal(ui.openButton.disabled, false);
    assert.equal(opener.focused, true);
});

test('expired sessions show an error rather than treating login HTML as a code', async () => {
    const ui = fixture();
    ui.clickShare();
    await ui.respond(0, {}, 200, true);
    assert.match(ui.parts.error.textContent, /session may have expired/);
    assert.equal(ui.parts.content.hidden, true);
});
