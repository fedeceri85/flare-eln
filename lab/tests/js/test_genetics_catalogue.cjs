const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../static/lab/genetics_catalogue.js'), 'utf8');

test('top creation button opens, scrolls and focuses the bottom panel', () => {
    let handler, focused = false, scrolled = false, prevented = false;
    const link = {dataset: {cataloguePanel: 'add-lines'}, addEventListener(event, fn) {handler = fn;}};
    const panel = {open: false, scrollIntoView() {scrolled = true;}, querySelector() {return {focus() {focused = true;}};}};
    vm.runInNewContext(source, {document: {querySelector() {return null;}, querySelectorAll(q) {return q === '[data-catalogue-panel]' ? [link] : [];}, getElementById() {return panel;}}});
    handler({preventDefault() {prevented = true;}});
    assert.ok(panel.open && focused && scrolled && prevented);
});

function orderFixture() {
    let handler;
    const input = {value: '1,2,3'};
    const list = {children: [], addEventListener(event, fn) {handler = fn;}, insertBefore(row, reference) {
        this.children.splice(this.children.indexOf(row), 1);
        this.children.splice(this.children.indexOf(reference), 0, row);
    }};
    function row(id) {
        const item = {dataset: {lineId: id}, buttons: {}};
        for (const direction of ['up', 'down']) item.buttons[direction] = {
            value: id + ':' + direction, disabled: false, focused: false,
            closest() {return item;}, focus() {this.focused = true;}
        };
        item.querySelector = selector => item.buttons[selector.includes(':up') ? 'up' : 'down'];
        Object.defineProperty(item, 'previousElementSibling', {get: () => list.children[list.children.indexOf(item) - 1]});
        Object.defineProperty(item, 'nextElementSibling', {get: () => list.children[list.children.indexOf(item) + 1]});
        return item;
    }
    list.children = ['1', '2', '3'].map(row);
    list.children[0].buttons.up.disabled = true;
    list.children[2].buttons.down.disabled = true;
    const form = {querySelector(q) {return q === '[data-order-list]' ? list : input;}};
    vm.runInNewContext(source, {document: {querySelector() {return null;}, querySelectorAll(q) {return q === '[data-crossing-order]' ? [form] : [];}}});
    return {list, input, click(button) {let prevented = false; handler({target: {closest() {return button;}}, preventDefault() {prevented = true;}}); return prevented;}};
}
test('move up changes hidden order and boundary buttons without losing references', () => {
    const f = orderFixture();
    const middle = f.list.children[1];
    assert.ok(f.click(middle.buttons.up));
    assert.equal(f.input.value, '2,1,3');
    assert.equal(middle.buttons.up.disabled, true);
    assert.ok(middle.buttons.down.focused);
    assert.equal(f.list.children[1].buttons.up.disabled, false);
});
test('move down retains exactly the same line elements', () => {
    const f = orderFixture();
    const initial = [...f.list.children];
    assert.ok(f.click(f.list.children[1].buttons.down));
    assert.equal(f.input.value, '1,3,2');
    assert.deepEqual([...f.list.children].sort((a,b) => a.dataset.lineId.localeCompare(b.dataset.lineId)), initial);
});
test('disabled boundary buttons do not change order', () => {
    const f = orderFixture();
    assert.equal(f.click(f.list.children[0].buttons.up), false);
    assert.equal(f.input.value, '1,2,3');
});
test('script is safe on pages without catalogue controls', () => {
    vm.runInNewContext(source, {document: {querySelector() {return null;}, querySelectorAll() {return [];}}});
});
test('crossing picker restores labels and IDs after a server validation error', () => {
    const script = fs.readFileSync(path.join(__dirname, '../../static/lab/crossing_lines.js'), 'utf8');
    let ready;
    const selectedInput = {value: '12,34'};
    const species = {addEventListener() {}};
    const form = {querySelector(q) {return q === '[name="species"]' ? species : selectedInput;}};
    const input = {dataset: {lookupUrl: '/lines/lookup/'}, addEventListener() {}};
    const list = {innerHTML: '', appendChild() {}};
    vm.runInNewContext(script, {document: {
        addEventListener(event, fn) {ready = fn;},
        querySelector(q) {
            if (q.includes('form input')) return {closest() {return form;}};
            if (q === '[data-line-lookup-input]') return input;
            if (q === '[data-selected-lines]') return list;
            return {};
        },
        getElementById() {return {textContent: JSON.stringify([{id: 12, name: 'Alpha'}, {id: 34, name: 'Beta'}])};},
        createElement() {return {appendChild() {}, addEventListener() {}};}
    }});
    ready();
    assert.equal(selectedInput.value, '12,34');
});

function liveFixture() {
    const calls = [], timers = new Map();
    let timerId = 0;
    const controls = {};
    for (const [name, value] of Object.entries({q: '', species: '1', tab: 'lines', per_page: '25', letter: 'all'})) {
        controls[name] = {value, listeners: {}, addEventListener(event, fn) {this.listeners[event] = fn;}};
    }
    const form = {elements: {namedItem(name) {return controls[name];}}, listeners: {}, addEventListener(event, fn) {this.listeners[event] = fn;}};
    const results = {innerHTML: 'initial', attributes: {}, listeners: {},
        querySelectorAll() {return [];}, setAttribute(name, value) {this.attributes[name] = value;},
        removeAttribute(name) {delete this.attributes[name];}, addEventListener(event, fn) {this.listeners[event] = fn;}};
    const status = {textContent: ''};
    const window = {location: {href: 'http://localhost/lines-crossings/?page=3', origin: 'http://localhost'}, history: {replaceState(state, title, url) {window.location.href = 'http://localhost' + url;}}};
    class DOMParser {
        parseFromString(text) {return {querySelector() {return {innerHTML: text, dataset: {activeTab: controls.tab.value, pageSize: controls.per_page.value, activeLetter: controls.letter.value}};}};}
    }
    const document = {querySelectorAll() {return [];}, querySelector(q) {
        if (q === '[data-catalogue-filter]') return form;
        if (q === '[data-catalogue-results-region]') return results;
        return status;
    }};
    vm.runInNewContext(source, {document, window, URL, DOMParser, AbortController,
        setTimeout(fn) {timers.set(++timerId, fn); return timerId;}, clearTimeout(id) {timers.delete(id);},
        fetch(url, options) {return new Promise((resolve, reject) => calls.push({url, options, resolve, reject}));}
    });
    return {calls, controls, results, status, form, window,
        flush() {const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn());},
        type(value) {controls.q.value = value; controls.q.listeners.input();},
        species(value) {controls.species.value = value; controls.species.listeners.change();},
        async resolve(index, text) {calls[index].resolve({ok: true, async text() {return text;}}); await new Promise(resolve => setImmediate(resolve));}
    };
}

test('typing debounces requests, resets pagination and preserves the search control', async () => {
    const f = liveFixture();
    const input = f.controls.q;
    f.type('al'); f.type('alpha');
    assert.equal(f.calls.length, 0);
    f.flush();
    assert.equal(f.calls.length, 1);
    const url = new URL(f.calls[0].url);
    assert.equal(url.searchParams.get('q'), 'alpha');
    assert.equal(url.searchParams.has('page'), false);
    await f.resolve(0, 'alpha results');
    assert.equal(f.results.innerHTML, 'alpha results');
    assert.equal(f.controls.q, input);
    assert.equal(input.value, 'alpha');
    assert.equal(f.status.textContent, '');
});
test('species selection updates immediately using the current query', async () => {
    const f = liveFixture();
    f.type('alpha');
    f.species('2');
    assert.equal(f.calls.length, 1);
    assert.equal(new URL(f.calls[0].url).searchParams.get('species'), '2');
    assert.equal(new URL(f.calls[0].url).searchParams.get('q'), 'alpha');
    f.flush();
    assert.equal(f.calls.length, 1);
    await f.resolve(0, 'rat results');
    assert.equal(f.results.innerHTML, 'rat results');
});
test('a response invalidated during the next debounce never replaces newer intent', async () => {
    const f = liveFixture();
    f.type('alpha'); f.flush();
    f.type('beta');
    assert.equal(f.calls[0].options.signal.aborted, true);
    await f.resolve(0, 'stale alpha');
    assert.equal(f.results.innerHTML, 'initial');
    f.flush();
    await f.resolve(1, 'beta results');
    assert.equal(f.results.innerHTML, 'beta results');
});
test('older request finishing last cannot overwrite the current species', async () => {
    const f = liveFixture();
    f.species('1'); f.species('2');
    await f.resolve(1, 'current species');
    await f.resolve(0, 'old species');
    assert.equal(f.results.innerHTML, 'current species');
});
test('filter failures retain results and show a retry message', async () => {
    const f = liveFixture();
    f.species('2');
    f.calls[0].reject(new Error('offline'));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.results.innerHTML, 'initial');
    assert.match(f.status.textContent, /Could not update/);
    let prevented = false;
    f.form.listeners.submit({preventDefault() {prevented = true;}});
    assert.equal(prevented, true);
    assert.equal(f.calls.length, 2);
    await f.resolve(1, 'retry succeeds');
    assert.equal(f.results.innerHTML, 'retry succeeds');
});
test('clearing search fetches the full catalogue and tab clicks retain pending input', async () => {
    const f = liveFixture();
    f.type(''); f.flush();
    assert.equal(new URL(f.calls[0].url).searchParams.get('q'), '');
    f.type('pending');
    const link = {href: 'http://localhost/lines-crossings/?q=old&tab=crossings&per_page=25'};
    f.results.listeners.click({target: {closest() {return link;}}, preventDefault() {}});
    const url = new URL(f.calls[1].url);
    assert.equal(url.searchParams.get('q'), 'pending');
    assert.equal(url.searchParams.get('tab'), 'crossings');
    assert.equal(f.controls.tab.value, 'crossings');
    await f.resolve(1, 'filtered crossings');
});

test('letter navigation resets pagination and persists through live search and row limits', async () => {
    const f = liveFixture();
    const link = {href: 'http://localhost/lines-crossings/?tab=lines&letter=A&per_page=25'};
    f.results.listeners.click({target: {closest() {return link;}}, preventDefault() {}});
    assert.equal(new URL(f.calls[0].url).searchParams.get('letter'), 'A');
    assert.equal(new URL(f.calls[0].url).searchParams.has('page'), false);
    await f.resolve(0, 'A results');
    f.type('alpha'); f.flush();
    assert.equal(new URL(f.calls[1].url).searchParams.get('letter'), 'A');
    f.results.listeners.change({target: {matches() {return true;}, value: '50'}});
    const url = new URL(f.calls[2].url);
    assert.equal(url.searchParams.get('letter'), 'A');
    assert.equal(url.searchParams.get('per_page'), '50');
});
