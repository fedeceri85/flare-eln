const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

const source = readFileSync(join(__dirname, '../../static/lab/mouse_editor_tabs.js'), 'utf8')
  .trim().replace(/^<script>/, '').replace(/<\/script>$/, '');
class Element {
  constructor(properties = {}) {
    Object.assign(this, { dataset: {}, attrs: {}, hidden: false, listeners: {}, parentElement: null, error: null }, properties);
  }
  appendChild(node) { node.parentElement = this; return node; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  dispatchEvent(event) { this.emit(event.type, event); }
  emit(name, event = {}) { (this.listeners[name] || []).forEach(fn => fn({ target: this, preventDefault() {}, ...event })); }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  removeAttribute(key) { delete this.attrs[key]; }
  focus() { this.focused = true; }
  querySelector(selector) { return selector === '[data-mouse-tab-error]' ? this.indicator : this.error; }
  closest(selector) {
    if (selector === '[data-mouse-tab-panel]' && this.dataset.mouseTabPanel) return this;
    if (selector === 'details' && this.isDetails) return this;
    if (selector.startsWith('[data-error-summary]') && this.errorLink) return this;
    return this.parentElement?.closest(selector) || null;
  }
  contains(node) { return node === this || Boolean(node.parentElement && this.contains(node.parentElement)); }
  matches() { return Boolean(this.isError); }
}
function fixture({ active = 'shared', serverError = null, dynamic = false, summary = false, family = false } = {}) {
  const names = dynamic ? ['ids', 'shared', 'individual', 'procedures', 'services'] : ['shared', 'individual', 'procedures', 'services'];
  if (family) names.splice(dynamic ? 2 : 1, 0, 'family');
  const root = new Element({ dataset: { mouseInitialTab: active } });
  const tabs = names.map(name => new Element({ dataset: { mouseTab: name }, indicator: new Element({ hidden: true }) }));
  const panels = [...names, 'procedures'].map(name => new Element({ dataset: { mouseTabPanel: name }, parentElement: root }));
  const activeInput = new Element({ value: active });
  const tablist = new Element({ hidden: true });
  tablist.querySelectorAll = () => tabs;
  const singleSlot = new Element({ parentElement: panels[names.indexOf('shared')] });
  const multipleSlot = new Element({ parentElement: panels[names.indexOf('individual')] });
  const widget = new Element({ parentElement: multipleSlot });
  const singleGeneticsSlot = new Element();
  const multipleGeneticsSlot = new Element();
  const genetics = new Element({ parentElement: multipleGeneticsSlot });
  const genotypeColumn = new Element();
  const summaryUrl = '/recordings/1/animals/?return_url=%2Frecordings%2F%3Fpage%3D2&mouse_editor_tab=shared';
  const summaryEditLink = new Element({ href: '/mice/edit-selected/?selected_mouse=Mouse-1&selected_mouse=Mouse-2&return_url=' + encodeURIComponent(summaryUrl) });
  const history = { state: { preserved: true }, replaceState(state, title, url) { this.state = state; this.url = url; } };
  root.querySelectorAll = selector => selector === '[data-multiple-mice-only]' ? [genotypeColumn] : panels;
  root.querySelector = selector => ({
    '[data-mouse-tab-list]': tablist,
    '[data-mouse-active-tab]': summary ? null : activeInput,
    '[data-mouse-summary-edit]': summary ? summaryEditLink : null,
    '[data-litter-individual-editor]': dynamic ? widget : null,
    '[data-single-mouse-slot]': singleSlot,
    '[data-multiple-mice-slot]': multipleSlot,
    '[data-genetics-editor]': genetics,
    '[data-single-genetics-slot]': singleGeneticsSlot,
    '[data-multiple-genetics-slot]': multipleGeneticsSlot,
  })[selector] || null;
  const fields = panels.map(panel => new Element({ parentElement: panel, value: 'unsaved', validity: { valid: false } }));
  if (serverError !== null) panels[serverError].error = new Element({ parentElement: panels[serverError], isError: true });
  let observer;
  const documentListeners = {};
  const nodes = new Map([['target-field', fields[1]]]);
  runInNewContext(source, {
    document: {
      querySelector() { return root; }, getElementById(id) { return nodes.get(id); },
      addEventListener(name, callback) { if (name === 'DOMContentLoaded') callback(); else documentListeners[name] = callback; },
    },
    window: { MutationObserver: true, location: { href: 'http://localhost' + summaryUrl }, history },
    URL, CustomEvent: class { constructor(type, options) { this.type = type; Object.assign(this, options); } },
    MutationObserver: class { constructor(callback) { observer = callback; } observe() {} },
    setTimeout,
  });
  return { root, tabs, panels, fields, activeInput, tablist, observer, widget, singleSlot, multipleSlot, genotypeColumn, summaryEditLink, history, genetics, singleGeneticsSlot, multipleGeneticsSlot,
    count(value) { documentListeners["mouse-editor:count-changed"]({ detail: { count: value } }); },
    click(index) { tabs[index].emit('click'); },
  };
}

test('switching tabs preserves all input nodes and values and never disables them', () => {
  const ui = fixture();
  assert.equal(ui.tablist.hidden, false);
  assert.equal(ui.panels[0].hidden, false);
  assert.equal(ui.panels[1].hidden, true);
  const field = ui.fields[0];
  field.value = 'edited mouse notes';
  ui.click(1);
  ui.click(2);
  assert.equal(ui.panels[2].hidden, false);
  assert.equal(ui.panels[4].hidden, false);
  ui.click(0);
  assert.equal(ui.fields[0], field);
  assert.equal(field.value, 'edited mouse notes');
  assert.equal(ui.activeInput.value, 'shared');
  assert.ok(ui.fields.every(field => !field.disabled));
});

test('restores posted tab and opens first server error instead when present', () => {
  const restored = fixture({ active: 'individual' });
  assert.equal(restored.panels[1].hidden, false);
  const errored = fixture({ active: 'services', serverError: 2 });
  assert.equal(errored.panels[2].hidden, false);
  assert.equal(errored.tabs[2].indicator.hidden, false);
  assert.equal(errored.tabs[0].indicator.hidden, true);
});

test('keyboard arrows and Home/End select and focus tabs with one tab stop', () => {
  const ui = fixture();
  ui.tabs[0].emit('keydown', { key: 'ArrowLeft' });
  assert.equal(ui.activeInput.value, 'services');
  assert.equal(ui.tabs[3].focused, true);
  assert.deepEqual(ui.tabs.map(tab => tab.tabIndex), [-1, -1, -1, 0]);
  ui.tabs[3].emit('keydown', { key: 'Home' });
  assert.equal(ui.activeInput.value, 'shared');
  ui.tabs[0].emit('keydown', { key: 'End' });
  assert.equal(ui.activeInput.value, 'services');
});

test('native validation reveals the first invalid tab before browser focuses it', () => {
  const ui = fixture();
  ui.root.emit('invalid', { target: ui.fields[1] });
  ui.root.emit('invalid', { target: ui.fields[2] });
  assert.equal(ui.panels[1].hidden, false);
  assert.equal(ui.panels[2].hidden, true);
  assert.equal(ui.fields[1].getAttribute('aria-invalid'), 'true');
});

test('error summary links reveal hidden target before focus handlers run', () => {
  const ui = fixture();
  const link = new Element({ errorLink: true, attrs: { href: '#target-field' } });
  ui.root.emit('click', { target: link });
  assert.equal(ui.activeInput.value, 'individual');
});

test('AJAX procedure errors open the procedures tab', () => {
  const ui = fixture();
  const error = new Element({ nodeType: 1, isError: true, parentElement: ui.panels[4] });
  ui.panels[4].error = error;
  ui.observer([{ addedNodes: [error] }]);
  assert.equal(ui.activeInput.value, 'procedures');
  assert.equal(ui.panels[4].hidden, false);
  assert.equal(ui.tabs[2].indicator.hidden, false);
});


test('one/multiple count moves the same individual widget and preserves draft values', () => {
  const ui = fixture({ dynamic: true, active: 'individual' });
  const input = new Element({ value: 'Draft local ID', parentElement: ui.widget });
  ui.count(1);
  assert.equal(ui.tabs[2].hidden, true);
  assert.equal(ui.activeInput.value, 'shared');
  assert.equal(ui.widget.parentElement, ui.singleSlot);
  assert.equal(ui.genetics.parentElement, ui.singleGeneticsSlot);
  assert.equal(ui.genotypeColumn.hidden, true);
  assert.equal(input.value, 'Draft local ID');
  ui.count(3);
  assert.equal(ui.tabs[2].hidden, false);
  assert.equal(ui.widget.parentElement, ui.multipleSlot);
  assert.equal(ui.genetics.parentElement, ui.multipleGeneticsSlot);
  assert.equal(ui.genotypeColumn.hidden, false);
  assert.equal(input.parentElement, ui.widget);
  assert.equal(input.value, 'Draft local ID');
  assert.equal(ui.activeInput.value, 'shared');
});

test('IDs is the first tab and keyboard navigation skips the hidden individual tab', () => {
  const ui = fixture({ dynamic: true, active: 'ids' });
  ui.count(1);
  assert.equal(ui.panels[0].hidden, false);
  ui.tabs[1].emit('keydown', { key: 'ArrowRight' });
  assert.equal(ui.activeInput.value, 'procedures');
  ui.tabs[3].emit('keydown', { key: 'Home' });
  assert.equal(ui.activeInput.value, 'ids');
  ui.count(2);
  ui.tabs[1].emit('keydown', { key: 'ArrowRight' });
  assert.equal(ui.activeInput.value, 'individual');
});

test('count changes preserve the active procedure tab and reject incomplete counts', () => {
  const ui = fixture({ dynamic: true, active: 'procedures' });
  ui.count(1);
  assert.equal(ui.activeInput.value, 'procedures');
  ui.count(undefined);
  assert.equal(ui.widget.parentElement, ui.singleSlot);
  ui.count(2);
  assert.equal(ui.activeInput.value, 'procedures');
});

test('read-only tabs carry the active section and animal selection into editing and back', () => {
  const ui = fixture({ summary: true, active: 'individual' });
  assert.equal(ui.panels[1].hidden, false);
  ui.click(2);
  const editUrl = new URL(ui.summaryEditLink.href, 'http://localhost');
  assert.equal(editUrl.searchParams.get('mouse_editor_tab'), 'procedures');
  assert.deepEqual(editUrl.searchParams.getAll('selected_mouse'), ['Mouse-1', 'Mouse-2']);
  const returnUrl = new URL(editUrl.searchParams.get('return_url'), 'http://localhost');
  assert.equal(returnUrl.searchParams.get('mouse_editor_tab'), 'procedures');
  assert.equal(returnUrl.searchParams.get('return_url'), '/recordings/?page=2');
  assert.equal(ui.history.url, returnUrl.pathname + returnUrl.search);
  assert.deepEqual(ui.history.state, { preserved: true });
  ui.tabs[2].emit('keydown', { key: 'Home' });
  assert.equal(new URL(ui.summaryEditLink.href, 'http://localhost').searchParams.get('mouse_editor_tab'), 'shared');
});

test('editing retains the Experimental records tab', () => {
  const ui = fixture({ summary: true, active: 'services' });
  assert.equal(ui.tabs[3].getAttribute('aria-selected'), 'true');
  assert.equal(ui.panels[3].hidden, false);
  const editUrl = new URL(ui.summaryEditLink.href, 'http://localhost');
  assert.equal(editUrl.searchParams.get('mouse_editor_tab'), 'services');
  assert.equal(new URL(editUrl.searchParams.get('return_url'), 'http://localhost').searchParams.get('mouse_editor_tab'), 'services');
  ui.tabs[2].emit('keydown', { key: 'ArrowRight' });
  assert.equal(ui.tabs[3].focused, true);
});

test('Parents and offspring remains selected across mouse count changes and opens on family errors', () => {
  const ui = fixture({ family: true, dynamic: true, active: 'family' });
  ui.count(1);
  assert.equal(ui.activeInput.value, 'family');
  ui.count(3);
  assert.equal(ui.activeInput.value, 'family');
  const errors = fixture({ family: true, dynamic: true, active: 'shared', serverError: 2 });
  assert.equal(errors.activeInput.value, 'family');
});

test('editing retains the Parents and offspring tab', () => {
  const ui = fixture({ family: true, summary: true, active: 'family' });
  assert.equal(new URL(ui.summaryEditLink.href, 'http://localhost').searchParams.get('mouse_editor_tab'), 'family');
  assert.match(ui.history.url, /mouse_editor_tab=family/);
});
