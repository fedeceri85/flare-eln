const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');
const source = readFileSync(join(__dirname, '../../static/lab/spreadsheet_import.js'), 'utf8');

function element(extra = {}) {
  const listeners = {}, attrs = {};
  return Object.assign({ children: [], dataset: {}, value: '', id: '', textContent: '',
    addEventListener(name, callback) { (listeners[name] ||= []).push(callback); },
    emit(name, event = {}) {
      const data = { target: this, preventDefault() { this.defaultPrevented = true; }, ...event };
      (listeners[name] || []).forEach(callback => callback(data));
      return data;
    },
    dispatchEvent(event) { this.emit(event.type, event); },
    appendChild(node) { this.children.push(node); return node; },
    replaceChildren(...nodes) { this.children = nodes; this.textContent = ''; },
    before(node) { this.search = node; },
    after(results, status) { this.results = results; this.status = status; },
    setAttribute(name, value) { attrs[name] = String(value); },
    getAttribute(name) { return attrs[name] ?? null; },
    hasAttribute(name) { return name in attrs; },
    removeAttribute(name) { delete attrs[name]; },
    setCustomValidity(message) { this.validationMessage = message; },
    scrollIntoView() {}, click() { this.emit('click'); },
  }, extra);
}

function fixture(fetchResults = async () => ({ results: [{ id: 7, label: 'Mouse - Line A' }] }), mappingRows = [], templateControls = null, imagePreviews = []) {
  const selects = ['shared', 'strain'].map((name, index) => {
    const option = element({ value: String(index + 1), textContent: `Mouse - Saved ${name}` });
    const select = element({ id: `id_${name}`, value: option.value,
      dataset: { importCrossingSelect: '/crossings/lookup/' }, children: [option] });
    Object.defineProperties(select, {
      options: { get() { return this.children; } },
      selectedOptions: { get() { return this.children.filter(node => node.value === this.value); } },
    });
    return select;
  });
  const timers = new Map(), requests = [];
  let timerId = 0;
  const form = element({
    querySelector(selector) {
      if (selector === '[data-import-template-select]') return templateControls?.select;
      if (selector === '[data-import-template-action]') return templateControls?.action;
      if (selector === '[data-import-template-open]') return templateControls?.open;
      return null;
    },
    noValidate: false,
    fields: templateControls?.fields || [],
    requestSubmit() { templateControls.submissions.push({ action: templateControls.action.disabled ? null : templateControls.action.value, noValidate: this.noValidate }); },
    querySelectorAll(selector) {
      if (selector === 'select[data-import-crossing-select]') return selects;
      return selector === '[data-import-mapping-row]' ? mappingRows : [];
    },
  });
  runInNewContext(source, {
    document: {
      addEventListener(name, callback) { callback(); },
      querySelector(selector) { return selector === '[data-import-form]' ? form : templateControls?.dialog; },
      querySelectorAll(selector) { return selector === '[data-import-image-preview]' ? imagePreviews : []; },
      createElement() { return element(); },
    },
    Event: class { constructor(type) { this.type = type; } }, AbortController,
    FormData: class {
      constructor(form) { this.fields = form.fields; }
      [Symbol.iterator]() { return this.fields[Symbol.iterator](); }
    },
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
    async fetch(url) {
      requests.push(url);
      return { ok: true, json: async () => fetchResults(url) };
    },
  });
  return { selects, requests, form,
    type(index, value) { const input = selects[index].search; input.value = value; input.emit('input'); },
    flush() { const [id, callback] = [...timers][0]; timers.delete(id); return callback(); },
  };
}

test('selecting a template immediately submits its apply action, while clearing the selector does nothing', () => {
  const controls = { select: element(), action: element({ name: 'template_action', value: 'apply', disabled: true }), submissions: [] };
  const ui = fixture(undefined, [], controls);
  controls.select.value = '17';
  controls.select.emit('change');
  assert.deepEqual(controls.submissions, [{ action: 'apply', noValidate: true }]);
  assert.equal(controls.action.disabled, true);
  assert.equal(ui.form.noValidate, false);
  controls.select.value = '';
  controls.select.emit('change');
  assert.equal(controls.submissions.length, 1);
});

test('shared image previews disappear if storage becomes unavailable before or after rendering', () => {
  const missing = element({complete: true, naturalWidth: 0, hidden: false});
  const available = element({complete: true, naturalWidth: 400, hidden: false});
  fixture(undefined, [], null, [missing, available]);
  assert.equal(missing.hidden, true);
  assert.equal(available.hidden, false);
  available.emit('error');
  assert.equal(available.hidden, true);
});

function dialogControls(reopen = false) {
  const snapshot = element(), cancel = element();
  const saveForm = element({ querySelector() { return snapshot; } });
  const dialog = element({ dataset: { reopen: String(reopen) }, open: false,
    querySelector(selector) { return selector === '[data-import-template-save-form]' ? saveForm : cancel; },
    showModal() { this.open = true; }, close() { this.open = false; },
  });
  return { open: element({ hidden: true }), dialog, saveForm, snapshot, cancel, fields: [] };
}

test('save opens a cancellable name dialog and submits current mapping values without duplicating popup metadata', () => {
  const controls = dialogControls();
  const ui = fixture(undefined, [], controls);
  assert.equal(controls.open.hidden, false);
  assert.equal(controls.dialog.open, false);
  controls.open.emit('click');
  assert.equal(controls.dialog.open, true);
  controls.cancel.emit('click');
  assert.equal(controls.dialog.open, false);
  controls.open.emit('click');
  ui.form.fields = [['step', 'mapping'], ['revision', 'current'], ['csrfmiddlewaretoken', 'token'], ['template_action', 'apply'],
    ['column_recording_date', '3'], ['mode_recording_date', 'column'], ['default_notes', 'Unsaved notes']];
  controls.saveForm.emit('submit');
  assert.deepEqual(controls.snapshot.children.map(input => [input.name, input.value]), [
    ['column_recording_date', '3'], ['mode_recording_date', 'column'], ['default_notes', 'Unsaved notes'],
  ]);
  ui.form.fields = [['column_recording_date', '4']];
  controls.saveForm.emit('submit');
  assert.deepEqual(controls.snapshot.children.map(input => [input.name, input.value]), [['column_recording_date', '4']]);
});

test('the name dialog reopens after a server-side save error', () => {
  const controls = dialogControls(true);
  fixture(undefined, [], controls);
  assert.equal(controls.dialog.open, true);
});

test('crossing controls preserve selected labels and use independent clickable suggestions', async () => {
  const ui = fixture();
  const [shared, strain] = ui.selects;
  assert.equal(shared.hidden, true);
  assert.equal(shared.search.id, 'id_shared');
  assert.equal(shared.id, 'id_shared__value');
  assert.equal(shared.search.value, 'Mouse - Saved shared');
  ui.type(0, 'Alias & line');
  assert.equal(shared.value, '');
  assert.ok(shared.search.validationMessage);
  assert.equal(strain.value, '2');
  await ui.flush();
  assert.equal(ui.requests[0], '/crossings/lookup/?q=Alias%20%26%20line');
  shared.results.children[0].click();
  assert.equal(shared.value, '7');
  assert.equal(shared.search.value, 'Mouse - Line A');
  assert.equal(shared.search.validationMessage, '');
  assert.equal(shared.results.children.length, 0);
  assert.equal(strain.search.value, 'Mouse - Saved strain');
});

test('clicking or editing a mapping input selects it and clears the alternative', () => {
  const mode = element({ value: 'column' });
  const controls = [element({ value: '2' }), element({ value: '99' })];
  const options = ['column', 'shared'].map((value, index) => element({
    dataset: { importMappingOption: value }, querySelectorAll() { return [controls[index]]; },
  }));
  const row = element({ querySelector() { return mode; }, querySelectorAll() { return options; } });
  fixture(undefined, [row]);
  assert.equal(options[0].dataset.importActive, 'true');
  assert.equal(options[1].dataset.importActive, 'false');
  assert.equal(controls[1].value, '');
  options[1].emit('pointerdown');
  assert.equal(mode.value, 'shared');
  assert.equal(controls[0].value, '');
  controls[1].value = '0';
  options[1].emit('input');
  assert.equal(controls[1].value, '0');
  assert.equal(options[1].dataset.importActive, 'true');
  controls[0].value = '3';
  options[0].emit('input');
  assert.equal(mode.value, 'column');
  assert.equal(options[0].dataset.importActive, 'true');
  assert.equal(controls[0].value, '3');
  assert.equal(controls[1].value, '');
});

test('keyboard selection chooses a crossing and editing invalidates its saved ID', async () => {
  const ui = fixture();
  const select = ui.selects[1];
  ui.type(1, 'Line');
  await ui.flush();
  assert.equal(select.search.emit('keydown', { key: 'ArrowDown' }).defaultPrevented, true);
  assert.equal(select.search.getAttribute('aria-activedescendant'), select.results.children[0].id);
  select.search.emit('keydown', { key: 'Enter' });
  assert.equal(select.value, '7');
  ui.type(1, 'another crossing');
  assert.equal(select.value, '');
  assert.ok(select.search.validationMessage);
  ui.type(1, '');
  assert.equal(select.search.validationMessage, '');
  assert.equal(select.search.getAttribute('aria-expanded'), 'false');
});

test('late search results cannot replace a newer query or a chosen crossing', async () => {
  let resolveFirst;
  const ui = fixture(url => url.endsWith('first') ? new Promise(resolve => { resolveFirst = resolve; })
    : { results: [{ id: 8, label: 'Mouse - Latest' }] });
  const select = ui.selects[0];
  ui.type(0, 'first');
  const pending = ui.flush();
  await new Promise(resolve => setImmediate(resolve));
  ui.type(0, 'second');
  await ui.flush();
  select.results.children[0].click();
  resolveFirst({ results: [{ id: 9, label: 'Mouse - Stale' }] });
  await pending;
  assert.equal(select.value, '8');
  assert.equal(select.search.value, 'Mouse - Latest');
  assert.equal(select.results.children.length, 0);
});

test('empty or failed searches require selection and can be dismissed', async () => {
  const ui = fixture(async url => {
    if (url.endsWith('offline')) throw new Error('offline');
    return { results: [] };
  });
  const select = ui.selects[0];
  ui.type(0, 'missing');
  await ui.flush();
  assert.equal(select.status.textContent, 'No matching crossings.');
  assert.equal(select.value, '');
  assert.ok(select.search.validationMessage);
  ui.type(0, 'offline');
  await ui.flush();
  assert.match(select.status.textContent, /unavailable/);
  select.search.emit('keydown', { key: 'Escape' });
  assert.equal(select.status.textContent, '');
  ui.type(0, 'missing');
  select.search.emit('blur');
  assert.equal(select.search.getAttribute('aria-expanded'), 'false');
});
