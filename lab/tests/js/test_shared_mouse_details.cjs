const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

const source = readFileSync(join(__dirname, '../../static/lab/shared_mouse_details.js'), 'utf8')
  .trim().replace(/^<script>/, '').replace(/<\/script>$/, '');
function fixture({ action = '', notesError = false, missingGenotypeToggle = false } = {}) {
  const crossingToggle = { checked: false };
  const genotypeToggle = { checked: false };
  const statusToggle = { checked: false };
  const crossing = { value: 'Selected crossing', disabled: false };
  const status = { value: 'Alive', disabled: false };
  const genotypeFields = [{ value: 'het', disabled: false }];
  const genotypeColumn = { querySelectorAll() { return genotypeFields; }, querySelector() { return null; } };
  const notesAction = { value: action };
  const notesContent = { hidden: false, value: 'Unsaved notes', querySelector() { return notesError ? {} : null; } };
  const group = (toggle, field) => ({
    querySelector() { return toggle; }, querySelectorAll() { return [field]; },
  });
  const groups = [group(crossingToggle, crossing), group(statusToggle, status)];
  let change, mutation;
  const section = {
    querySelector(selector) {
      return ({ '.shared-genotype-column': genotypeColumn, '[name="update_crossing"]': crossingToggle,
        '[name="update_genotype"]': missingGenotypeToggle ? null : genotypeToggle, '[name="notes_action"]': notesAction,
        '[data-shared-notes-content]': notesContent })[selector];
    },
    querySelectorAll() { return groups; },
    addEventListener(name, handler) { change = handler; },
  };
  runInNewContext(source, {
    document: { querySelector() { return section; }, addEventListener(name, callback) { callback(); } },
    window: { MutationObserver: true },
    MutationObserver: class { constructor(callback) { mutation = callback; } observe() {} },
  });
  return { crossingToggle, genotypeToggle, statusToggle, crossing, status, genotypeFields,
    notesAction, notesContent, change() { change(); }, mutation() { mutation(); } };
}

test('unchecked fields are inactive and toggling preserves values', () => {
  const ui = fixture();
  assert.equal(ui.status.disabled, true);
  ui.statusToggle.checked = true;
  ui.change();
  assert.equal(ui.status.disabled, false);
  ui.status.value = 'Changed status';
  ui.statusToggle.checked = false;
  ui.change();
  assert.equal(ui.status.disabled, true);
  assert.equal(ui.status.value, 'Changed status');
});

test('single animal changes work when the genotype toggle is outside the details panel', () => {
  const ui = fixture({ missingGenotypeToggle: true });
  assert.equal(ui.notesContent.hidden, true);
  ui.statusToggle.checked = true;
  assert.doesNotThrow(() => ui.change());
  assert.equal(ui.status.disabled, false);
});

test('genotypes become active for genotype updates or crossing changes', () => {
  const ui = fixture();
  assert.equal(ui.genotypeFields[0].disabled, true);
  ui.genotypeToggle.checked = true;
  ui.change();
  assert.equal(ui.genotypeFields[0].disabled, false);
  assert.equal(ui.crossing.disabled, true);
  ui.genotypeToggle.checked = false;
  ui.crossingToggle.checked = true;
  ui.change();
  assert.equal(ui.crossing.disabled, false);
  assert.equal(ui.genotypeFields[0].disabled, false);
});

test('notes only expand for append/replace and preserve unsaved text while collapsed', () => {
  const ui = fixture();
  assert.equal(ui.notesContent.hidden, true);
  for (const action of ['append', 'replace']) {
    ui.notesAction.value = action;
    ui.change();
    assert.equal(ui.notesContent.hidden, false);
  }
  ui.notesAction.value = '';
  ui.change();
  assert.equal(ui.notesContent.hidden, true);
  assert.equal(ui.notesContent.value, 'Unsaved notes');
  assert.equal(fixture({ notesError: true }).notesContent.hidden, false);
});

test('genotype fields inserted by crossing lookup adopt the current inactive state', () => {
  const ui = fixture();
  const newGene = { value: 'unknown', disabled: false };
  ui.genotypeFields.push(newGene);
  ui.mutation();
  assert.equal(newGene.disabled, true);
  ui.crossingToggle.checked = true;
  ui.change();
  assert.equal(newGene.disabled, false);
  assert.equal(newGene.value, 'unknown');
});
