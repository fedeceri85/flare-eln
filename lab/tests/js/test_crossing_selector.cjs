const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');
const source = readFileSync(join(__dirname, '../../static/lab/crossing_selector.js'), 'utf8');
function element(extra = {}) {
  const listeners = {};
  return Object.assign({ children: [], dataset: {}, value: '', className: '', textContent: '',
    addEventListener(name, callback) { listeners[name] = callback; },
    emit(name) { return listeners[name]?.({ target: this }); },
    dispatchEvent() {},
    appendChild(node) { this.children.push(node); },
    setAttribute(name, value) { this[name] = value; },
    classList: { add() {}, remove() {}, toggle() {} },
  }, extra);
}
function fixture({ litter = false, mouseId = '' } = {}) {
  const field = element({ closest() { return null; }, insertAdjacentElement() {} });
  const input = element({ closest(selector) { return selector === '[data-litter-form]' ? (litter ? {} : null) : field; } });
  const crossing = element();
  const config = { dataset: { lookupUrl: '/configured-crossings/', genotypeFieldsUrl: '/configured-genotypes/', mouseId } };
  let scheduled;
  const requests = [];
  runInNewContext(source, { document: {
    addEventListener(name, callback) { callback(); },
    querySelector(selector) {
      return ({ '[data-crossing-lookup-input]': input, '[data-crossing-definition-input]': crossing,
        '[data-crossing-selector-config]': config })[selector] || null;
    },
    querySelectorAll() { return []; },
    createElement() { return element(); }, createTextNode(text) { return { textContent: text }; },
  }, URLSearchParams, Event: class {},
  setTimeout(callback) { scheduled = callback; }, clearTimeout() { scheduled = null; },
  async fetch(url) {
    requests.push(url);
    return { ok: true, json: async () => url.startsWith('/configured-crossings/')
      ? { results: [{ id: 7, label: 'Gene A x Gene B' }] } : { lines: [], choices: [] } };
  } });
  return { input, crossing, requests,
    async search() { input.value = 'Gene A'; input.emit('input'); await scheduled(); },
    async select() { field.children[0].children[0].emit('click'); await new Promise(resolve => setImmediate(resolve)); },
  };
}
test('crossing lookup uses configured URLs and encodes mouse IDs as data', async () => {
  const mouseId = 'Mouse / "42" & <sample>';
  const ui = fixture({ mouseId });
  await ui.search();
  assert.equal(ui.requests[0], '/configured-crossings/?q=Gene+A');
  await ui.select();
  const params = new URL(ui.requests[1], 'http://localhost').searchParams;
  assert.equal(params.get('crossing'), '7');
  assert.equal(params.get('mouse'), mouseId);
  assert.equal(ui.crossing.value, 7);
  assert.equal(ui.input.value, 'Gene A x Gene B');
});
test('litter selection leaves genotype refresh to the litter row editor', async () => {
  const ui = fixture({ litter: true });
  await ui.search();
  await ui.select();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.crossing.value, 7);
});
