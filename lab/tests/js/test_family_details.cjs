const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const {join} = require('node:path');
const source = readFileSync(join(__dirname, '../../static/lab/family_details.js'), 'utf8');
function fixture() {
    const listeners = {};
    const makeRoot = () => {
        const parts = {'[data-family-content]': {innerHTML: ''}, '[data-family-status]': {textContent: ''}, '[data-family-retry]': {hidden: true}};
        return {parts, dataset: {familyLoadUrl: '/family/', familyLoaded: 'false'}, open: true,
            querySelector(key) {return parts[key];}, matches() {return true;}};
    };
    const family = makeRoot(), matches = makeRoot(), requests = [];
    runInNewContext(source, {document: {addEventListener(type, handler) {listeners[type] = handler;}, getElementById() {return family;}},
        fetch(url) {return new Promise((resolve, reject) => requests.push({url, resolve, reject}));}});
    const settle = () => new Promise(resolve => setImmediate(resolve));
    return {family, matches, requests, settle,
        tab(tab = 'family') {listeners['mouse-editor:tab-activated']({detail: {tab}});},
        expand() {listeners.toggle({target: matches});},
        retry(root = family) {listeners.click({target: {closest() {return {closest() {return root;}};}}});}};
}
test('family reads wait for activation, deduplicate requests and reuse loaded content', async () => {
    const ui = fixture(); ui.tab('shared'); assert.equal(ui.requests.length, 0);
    ui.tab(); ui.tab(); assert.equal(ui.requests.length, 1);
    ui.requests[0].resolve({ok: true, text: async () => '<p>Family</p>'}); await ui.settle();
    assert.equal(ui.family.parts['[data-family-content]'].innerHTML, '<p>Family</p>');
    ui.tab(); assert.equal(ui.requests.length, 1);
    assert.equal(ui.matches.dataset.familyLoaded, 'false');
});
test('failed and redirected requests expose Retry without marking content loaded', async () => {
    const ui = fixture(); ui.tab(); ui.requests[0].reject(new Error('offline')); await ui.settle();
    assert.equal(ui.family.parts['[data-family-retry]'].hidden, false);
    ui.retry(); ui.requests[1].resolve({ok: true, redirected: true}); await ui.settle();
    assert.equal(ui.family.dataset.familyLoaded, 'false');
    ui.retry(); ui.requests[2].resolve({ok: true, text: async () => 'Recovered'}); await ui.settle();
    assert.equal(ui.family.parts['[data-family-status]'].textContent, '');
});
