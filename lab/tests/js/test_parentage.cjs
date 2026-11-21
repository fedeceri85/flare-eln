const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const source = readFileSync(join(__dirname, '../../static/lab/parentage.js'), 'utf8');
function element(value = '') {
    return {value, hidden: false, disabled: false, dataset: {}, children: [], attrs: {}, handlers: {},
        setAttribute(k, v) {this.attrs[k] = v;}, removeAttribute(k) {delete this.attrs[k];},
        addEventListener(k, fn) {this.handlers[k] = fn;}, replaceChildren() {this.children = [];},
        append(child) {this.children.push(child);}, focus() {}, click() {return this.handlers.click();}};
}
function fixture({reference = '', text = '', enabled = true, fetcher, hadParent = false, importMode = false} = {}) {
    const parts = {
        '[data-parent-text]': element(text), '[data-parent-reference]': element(reference),
        '[data-parent-state]': element(), '[data-parent-suggestions]': element(),
        'input[type="checkbox"]': {...element(), checked: enabled}, '[data-parent-remove]': element(),
    };
    const buttons = {'[data-remove-parent-cancel]': element(), '[data-remove-parent-confirm]': element(), '[data-remove-parent-description]': element()};
    const dialog = {...element(), open: false, querySelector: key => buttons[key],
        showModal() {this.open = true;}, close() {this.open = false;}};
    const input = parts['[data-parent-text]'];
    input.dataset = {parentLookup: '/lookup/', parentExclude: 'Self / 1'};
    if (importMode) {
        parts['input[type="checkbox"]'] = null;
        parts['[data-parent-remove]'] = null;
        delete input.dataset.parentExclude;
    }
    const calls = [];
    const timers = new Map(); let timerId = 0;
    const root = {dataset: {parentRole: 'mother', parentTarget: 'this parent group', parentHadValue: String(hadParent), parentImport: String(importMode)},
        querySelector: key => parts[key], contains: node => Object.values(parts).includes(node)};
    runInNewContext(source, {
        document: {addEventListener() {}, querySelector: () => dialog, querySelectorAll: () => [root], createElement: () => element()},
        URLSearchParams, window: {confirm: () => true},
        fetch: async url => {
            calls.push(url);
            if (fetcher) return fetcher(url);
            const q = new URL('http://local' + url).searchParams.get('q');
            return {ok: true, json: async () => ({results: [{id: 'M-1', label: 'M-1'}],
                exact_match: q.toLowerCase() === 'm-1' ? {id: 'M-1', label: 'M-1'} : null, ambiguous: false})};
        },
        setTimeout(callback) {timers.set(++timerId, callback); return timerId;},
        clearTimeout(id) {timers.delete(id);},
    });
    return {parts, input, dialog, buttons, calls, ref: parts['[data-parent-reference]'], list: parts['[data-parent-suggestions]'],
        type(value) {input.value = value; input.handlers.input();},
        async suggestions() {const callbacks = [...timers.values()]; timers.clear(); for (const callback of callbacks) await callback();},
        blur(relatedTarget = null) {return input.handlers.blur({relatedTarget});},
        key(key) {let prevented = false; const pending = input.handlers.keydown({key, preventDefault() {prevented = true;}}); return {prevented, pending};}};
}
test('typing invalidates the link immediately but does not request or match until blur', async () => {
    const f = fixture({reference: 'Old', text: 'Old'});
    f.type('m-1'); assert.equal(f.ref.value, ''); assert.equal(f.calls.length, 0);
    await f.blur();
    assert.equal(f.ref.value, 'M-1'); assert.equal(f.input.value, 'm-1');
    assert.equal(f.list.hidden, true);
    assert.equal(f.parts['[data-parent-state]'].textContent, 'Linked: M-1');
});
test('Enter resolves without submitting; suggestions remain selectable by keyboard', async () => {
    const f = fixture(); f.type('M-1');
    const exact = f.key('Enter'); assert.equal(exact.prevented, true); await exact.pending;
    assert.equal(f.ref.value, 'M-1');
    f.type('M'); await f.key('Enter').pending;
    assert.equal(f.ref.value, ''); assert.equal(f.list.hidden, false);
    f.key('ArrowDown'); assert.equal(f.list.children[0].attrs['aria-selected'], 'true');
    assert.equal(f.key('Enter').prevented, true); assert.equal(f.ref.value, 'M-1');
    assert.equal(f.input.value, 'M-1'); assert.equal(f.list.hidden, true);
});
test('ambiguous and unmatched values remain text until a suggestion is selected', async () => {
    const f = fixture({fetcher: async () => ({ok: true, json: async () => ({exact_match: null, ambiguous: true,
        results: [{id: 'Mixed', label: 'Mixed'}, {id: 'MIXED', label: 'MIXED'}]})})});
    f.type('Mixed'); await f.key('Enter').pending;
    assert.equal(f.ref.value, ''); assert.match(f.parts['[data-parent-state]'].textContent, /Multiple exact/);
    f.list.children[1].click(); assert.equal(f.ref.value, 'MIXED');
});
test('clearing a matched field drops the reference and shows pending removal', async () => {
    const f = fixture({reference: 'M-1', text: 'M-1', hadParent: true});
    f.type(''); await f.blur(); assert.equal(f.calls.length, 0); assert.equal(f.ref.value, '');
    assert.match(f.parts['[data-parent-state]'].textContent, /removed when saved/);
    assert.equal(f.parts['[data-parent-remove]'].hidden, true);
});
test('Remove parent stages both values only on confirmation and enables its update checkbox', () => {
    const f = fixture({reference: 'M-1', text: 'M-1', enabled: false, hadParent: true});
    f.parts['[data-parent-remove]'].click(); assert.equal(f.dialog.open, true);
    assert.match(f.buttons['[data-remove-parent-description]'].textContent, /mother M-1/);
    assert.equal(f.ref.value, 'M-1'); assert.equal(f.parts['input[type="checkbox"]'].checked, false);
    f.buttons['[data-remove-parent-cancel]'].click(); assert.equal(f.dialog.open, false); assert.equal(f.input.value, 'M-1');
    f.parts['[data-parent-remove]'].click(); f.buttons['[data-remove-parent-confirm]'].click();
    assert.equal(f.ref.value, ''); assert.equal(f.input.value, '');
    assert.equal(f.parts['input[type="checkbox"]'].checked, true); assert.equal(f.input.disabled, false);
});
test('Escape cancels removal without changing parent values', () => {
    const f = fixture({reference: 'M-1', text: 'M-1'});
    f.parts['[data-parent-remove]'].click(); let prevented = false;
    f.dialog.handlers.cancel({preventDefault() {prevented = true;}});
    assert.equal(prevented, true); assert.equal(f.dialog.open, false); assert.equal(f.ref.value, 'M-1');
});
test('a late lookup cannot restore a parent after text deletion or confirmed removal', async () => {
    let finish;
    const f = fixture({fetcher: () => new Promise(resolve => {finish = resolve;})});
    f.type('M-1'); const pending = f.blur(); f.type('');
    finish({ok: true, json: async () => ({results: [], exact_match: {id: 'M-1'}, ambiguous: false})});
    await pending; assert.equal(f.ref.value, ''); assert.equal(f.input.value, '');
});
test('older successful or failed searches cannot replace a newer result', async () => {
    const requests = [];
    const f = fixture({fetcher: () => new Promise((resolve, reject) => requests.push({resolve, reject}))});
    f.type('Old'); const old = f.blur(); f.type('New'); const current = f.blur();
    requests[1].resolve({ok: true, json: async () => ({results: [], exact_match: {id: 'New'}, ambiguous: false})});
    await current; requests[0].reject(new Error('old failure')); await old;
    assert.equal(f.ref.value, 'New');
});
test('moving to a suggestion or removal button does not start a competing blur lookup', async () => {
    const f = fixture(); f.type('M'); await f.blur(f.parts['[data-parent-remove]']);
    assert.equal(f.calls.length, 0);
});
test('unavailable lookups leave text usable for authoritative server resolution', async () => {
    const f = fixture({fetcher: async () => {throw new Error('offline');}});
    f.type('Unregistered'); await f.blur();
    assert.equal(f.input.value, 'Unregistered'); assert.equal(f.ref.value, '');
    assert.match(f.parts['[data-parent-state]'].textContent, /checked when saved/);
});

test('typing offers suggestions without automatically establishing an exact link', async () => {
    const f = fixture(); f.type('m'); f.type('m-1');
    await f.suggestions();
    assert.equal(f.calls.length, 1);
    assert.equal(f.ref.value, ''); assert.equal(f.list.hidden, false);
    assert.equal(f.input.attrs['aria-expanded'], 'true');
    f.key('ArrowDown'); f.key('Enter');
    assert.equal(f.input.value, 'M-1'); assert.equal(f.ref.value, 'M-1');
});
test('blur cancels the suggestion timer and resolves the exact ID once', async () => {
    const f = fixture(); f.type('m-1'); await f.blur(); await f.suggestions();
    assert.equal(f.calls.length, 1); assert.equal(f.ref.value, 'M-1'); assert.equal(f.list.hidden, true);
});
test('stale typing suggestions cannot reopen the list after Escape or deletion', async () => {
    let finish;
    const f = fixture({fetcher: () => new Promise(resolve => {finish = resolve;})});
    f.type('M'); const pending = f.suggestions(); f.key('Escape');
    finish({ok: true, json: async () => ({results: [{id: 'M-1', label: 'M-1'}]})});
    await pending; assert.equal(f.list.hidden, true);
    f.type('M'); f.type(''); await f.suggestions(); assert.equal(f.calls.length, 1);
});
test('import parent overrides use the same suggestions and reserve link confirmation for Review', async () => {
    const f = fixture({importMode: true});
    assert.match(f.parts['[data-parent-state]'].textContent, /mapped/);
    f.type('M'); await f.suggestions();
    assert.equal(f.list.hidden, false); f.list.children[0].click();
    assert.equal(f.input.value, 'M-1');
    assert.match(f.parts['[data-parent-state]'].textContent, /confirm in Review/);
    f.type('External-001'); await f.blur();
    assert.equal(f.ref.value, ''); assert.equal(f.input.value, 'External-001');
});

for (const importMode of [false, true]) {
    test(`pointer selection survives browsers that blur inputs without focusing buttons (${importMode ? 'import' : 'add/edit'})`, async () => {
        const f = fixture({importMode});
        f.type('M'); await f.suggestions();
        const option = f.list.children[0];
        let prevented = false;
        option.handlers.pointerdown?.({preventDefault() {prevented = true;}});
        // Some browsers report relatedTarget=null when clicking a button. Blur hides
        // the list before click can fire unless pointerdown keeps input focus.
        if (!prevented) await f.blur(null);
        if (!f.list.hidden) option.click();
        assert.equal(f.input.value, 'M-1');
        assert.equal(f.ref.value, 'M-1');
        assert.equal(f.list.hidden, true);
        assert.equal(f.calls.length, 1);
    });
}
