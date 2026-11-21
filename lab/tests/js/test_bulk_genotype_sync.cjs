const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

// Execute the real shared and bulk browser assets against a small DOM adapter. This checks
// synchronous propagation and request ordering without a browser dependency.
const source = ['mouse_row_editor', 'mouse_genotype_editor', 'bulk_mouse_rows'].map(name =>
  readFileSync(join(__dirname, '../../static/lab/' + name + '.js'), 'utf8')).join('\n');

function element(properties = {}) {
  const listeners = new Map();
  return Object.assign({
    name: '', value: '', checked: false, type: 'select-one', dataset: {}, textContent: '',
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(callback);
    },
    emit(type, properties = {}) {
      const event = Object.assign({ target: this, preventDefault() { this.prevented = true; } }, properties);
      for (const listener of listeners.get(type) || []) listener(event);
      return event;
    },
    matches(selector) {
      return selector === '[data-genotype-field]' ? Boolean(this.dataset.genotypeField)
        : selector === '[data-individual-genotype-field]' && Boolean(this.dataset.individualGenotypeField);
    },
    hasAttribute() { return false; },
  }, properties);
}

function fixture() {
  const shared = ['1', '2'].map(lineId => element({ dataset: { genotypeField: 'true', lineId } }));
  const makeRows = () => [
    ...['A', 'B'].flatMap(mouse => ['1', '2'].map(lineId => element({
      name: mouse + '_' + lineId, value: mouse === 'A' ? 'het' : 'hom',
      dataset: { individualGenotypeField: 'true', lineId },
    }))),
    element({ name: 'A_local_identifier', value: 'old-id', type: 'text' }),
    element({ name: 'A_uncertain', type: 'checkbox', checked: true }),
    element({ name: 'B_uncertain', type: 'checkbox', checked: false }),
  ];
  const rows = { fields: makeRows(), querySelectorAll(selector) {
    if (selector.startsWith("details")) return [];
    return selector === '[data-individual-genotype-field]'
      ? this.fields.filter(field => field.dataset.individualGenotypeField) : this.fields;
  } };
  Object.defineProperty(rows, 'innerHTML', { set() { this.fields = makeRows(); } });
  const button = element();
  const retryContainer = element({ hidden: true });
  const status = element();
  const update = element({ checked: false });
  const individualCrossing = element({ name: 'A_override_crossing_definition', value: '1' });
  const individualCrossingToggle = element({ name: 'A_override_crossing_definition_enabled', type: 'checkbox' });
  const form = element({
    action: 'http://localhost/edit-selected/',
    elements: { update_genotype: update, update_crossing: element(), crossing_definition: element(),
      [individualCrossing.name]: individualCrossing, [individualCrossingToggle.name]: individualCrossingToggle },
    querySelector(selector) {
      if (selector === '[data-preview-individual-mice]') return button;
      if (selector === '[data-bulk-rows-retry]') return retryContainer;
      return status;
    },
    querySelectorAll(selector) { return selector === '[data-individual-crossing]' ? [individualCrossing, individualCrossingToggle] : shared; },
  });
  let ready;
  let resolveFetch;
  let fetchCount = 0;
  runInNewContext(source, {
    document: {
      addEventListener(type, callback) { ready = callback; },
      querySelector() { return form; }, getElementById() { return rows; },
    },
    window: { location: { href: form.action } },
    FormData: class { set() {} },
    fetch() { fetchCount++; return new Promise(resolve => { resolveFetch = resolve; }); },
    setTimeout, clearTimeout, Map,
  });
  ready();
  return {
    shared, rows, button, retryContainer, status, form, individualCrossing, individualCrossingToggle,
    get fetchCount() { return fetchCount; },
    field(name) { return rows.fields.find(field => field.name === name); },
    enable(value = true) { update.checked = value; form.emit('change', { target: update }); },
    changeShared(index, value) { shared[index].value = value; form.emit('change', { target: shared[index] }); },
    changeIndividual(name, value) { const field = this.field(name); field.value = value; form.emit('change', { target: field }); },
    async finishRefresh(ok = true) {
      resolveFetch({ ok, json: async () => ({ html: 'fresh rows' }) });
      await new Promise(resolve => setImmediate(resolve));
    },
  };
}

test('checked shared genotype immediately fills matching mice without fetching', () => {
  const ui = fixture();
  ui.enable();
  ui.changeShared(0, 'wt');
  assert.equal(ui.field('A_1').value, 'wt');
  assert.equal(ui.field('B_1').value, 'wt');
  assert.equal(ui.field('A_2').value, 'het');
  assert.equal(ui.field('B_2').value, 'hom');
  assert.equal(ui.field('A_uncertain').checked, true);
  assert.equal(ui.field('B_uncertain').checked, false);
  assert.equal(ui.fetchCount, 0);
});

test('individual crossing selections and toggles automatically refresh genotype rows', async () => {
  const ui = fixture();
  ui.individualCrossing.value = '2';
  ui.individualCrossing.emit('change');
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.equal(ui.fetchCount, 1);
  await ui.finishRefresh();
  assert.equal(ui.individualCrossing.value, '2');
  ui.individualCrossingToggle.checked = true;
  ui.individualCrossingToggle.emit('change');
  assert.equal(ui.form.emit('submit', { submitter: element() }).prevented, true);
  await ui.finishRefresh();
  assert.equal(ui.individualCrossingToggle.checked, true);
  assert.equal(ui.fetchCount, 2);
});

test('unchecked bulk changes do not copy; checking applies the selected value', () => {
  const ui = fixture();
  ui.changeShared(0, 'cre');
  assert.equal(ui.field('A_1').value, 'het');
  ui.enable();
  assert.equal(ui.field('A_1').value, 'cre');
  assert.equal(ui.field('B_1').value, 'cre');
  ui.enable(false);
  ui.changeShared(0, 'wt');
  assert.equal(ui.field('A_1').value, 'cre');
});

test('leave unchanged does not replace individual genotypes', () => {
  const ui = fixture();
  ui.enable();
  ui.changeShared(0, '');
  assert.equal(ui.field('A_1').value, 'het');
  assert.equal(ui.field('B_1').value, 'hom');
});

test('a later individual edit survives an in-flight row refresh', async () => {
  const ui = fixture();
  ui.enable();
  ui.button.emit('click');
  ui.changeShared(0, 'wt');
  ui.changeIndividual('A_1', 'het');
  await ui.finishRefresh();
  assert.equal(ui.field('A_1').value, 'het');
  assert.equal(ui.field('B_1').value, 'wt');
  assert.equal(ui.field('A_uncertain').checked, true);
});

test('a later shared edit replaces an earlier individual edit during refresh', async () => {
  const ui = fixture();
  ui.enable();
  ui.button.emit('click');
  ui.changeIndividual('A_1', 'hom');
  ui.changeShared(0, 'cre');
  await ui.finishRefresh();
  assert.equal(ui.field('A_1').value, 'cre');
  assert.equal(ui.field('B_1').value, 'cre');
});

test('saving waits for an in-flight crossing preview', async () => {
  const ui = fixture();
  ui.button.emit('click');
  const event = ui.form.emit('submit', { submitter: element() });
  assert.equal(event.prevented, true);
  await ui.finishRefresh();
  const next = ui.form.emit('submit', { submitter: element() });
  assert.notEqual(next.prevented, true);
});


test('refresh is hidden during normal use and after successful automatic refresh', async () => {
  const ui = fixture();
  assert.equal(ui.retryContainer.hidden, true);
  ui.form.elements.crossing_definition.emit('change');
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.equal(ui.fetchCount, 1);
  assert.equal(ui.retryContainer.hidden, true);
  await ui.finishRefresh();
  assert.equal(ui.retryContainer.hidden, true);
});

test('failed refresh shows an AJAX retry and preserves individual edits', async () => {
  const ui = fixture();
  ui.button.emit('click');
  await ui.finishRefresh(false);
  assert.equal(ui.retryContainer.hidden, false);
  assert.equal(ui.button.disabled, false);
  assert.match(ui.status.textContent, /Retry updating animal rows/);
  ui.changeIndividual('A_1', 'cre');
  const click = ui.button.emit('click');
  assert.equal(click.prevented, true);
  assert.equal(ui.fetchCount, 2);
  assert.equal(ui.retryContainer.hidden, true);
  await ui.finishRefresh();
  assert.equal(ui.field('A_1').value, 'cre');
  assert.equal(ui.retryContainer.hidden, true);
});


test('typing a local identifier during row refresh preserves the latest text', async () => {
  const ui = fixture();
  ui.button.emit('click');
  ui.field('A_local_identifier').value = 'new-ear-clip';
  await ui.finishRefresh();
  assert.equal(ui.field('A_local_identifier').value, 'new-ear-clip');
});

test('refresh preserves checkbox changes made while the request is in flight', async () => {
  const ui = fixture();
  ui.button.emit('click');
  ui.field('A_uncertain').checked = false;
  ui.field('B_uncertain').checked = true;
  await ui.finishRefresh();
  assert.equal(ui.field('A_uncertain').checked, false);
  assert.equal(ui.field('B_uncertain').checked, true);
});

test('a separate delete action is not intercepted by the row-refresh guard', async () => {
  const ui = fixture();
  ui.button.emit('click');
  const submitter = element({ hasAttribute(name) { return name === 'formaction'; } });
  const event = ui.form.emit('submit', { submitter });
  assert.notEqual(event.prevented, true);
  await ui.finishRefresh();
});
