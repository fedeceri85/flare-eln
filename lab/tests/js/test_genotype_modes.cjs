const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const {join} = require('node:path');
const source = readFileSync(join(__dirname, '../../static/lab/mouse_genotype_editor.js'), 'utf8');
function fixture() {
  const shared = ['1', '2'].map(lineId => ({name: 'shared' + lineId, type: 'select-one', value: 'hom', dataset: {lineId}}));
  const modes = ['1', '2'].map(lineId => ({name: 'individual' + lineId, type: 'checkbox', checked: true, dataset: {lineId, genotypeIndividual: 'true'}}));
  const makeRows = () => ['A', 'B'].flatMap(id => ['1', '2'].map(lineId => ({name: id + lineId, type: 'select-one', value: id === 'A' ? 'het' : 'wt', dataset: {lineId}})));
  const rows = {fields: makeRows(), querySelectorAll() {return this.fields;}};
  const sharedRoot = {querySelectorAll(selector) {return selector === '[data-genotype-individual]' ? modes : shared;}};
  const listeners = {};
  const form = {addEventListener(type, listener) {listeners[type] = listener;}};
  const window = {};
  runInNewContext(source, {window, document: {addEventListener() {}}});
  const toggle = {checked: false};
  const editor = window.MouseGenotypeEditor.create({form, rows, sharedRoot, toggle});
  const change = field => listeners.change({target: Object.assign(field, {matches(selector) {return selector === '[data-genotype-field]' && shared.includes(field);}})});
  return {shared, modes, rows, toggle, editor, change, makeRows};
}
test('per-line individual choice gates shared results and immediately copies on uncheck', () => {
  const ui = fixture();
  assert.equal(ui.shared[0].disabled, true);
  ui.modes[0].checked = false; ui.change(ui.modes[0]);
  assert.equal(ui.shared[0].disabled, false);
  assert.equal(ui.toggle.checked, true);
  assert.deepEqual(ui.rows.fields.map(field => field.value), ['hom', 'het', 'hom', 'wt']);
  assert.deepEqual(ui.rows.fields.map(field => field.disabled), [true, false, true, false]);
  ui.shared[0].value = 'wt'; ui.change(ui.shared[0]);
  assert.equal(ui.rows.fields[0].value, 'wt');
  ui.modes[0].checked = true; ui.change(ui.modes[0]);
  assert.equal(ui.rows.fields[0].disabled, false);
  ui.rows.fields[0].value = 'het'; ui.editor.replay();
  assert.equal(ui.rows.fields[0].value, 'het');
});
test('shared results remain authoritative after row refresh while individual lines retain their values', () => {
  const ui = fixture();
  ui.modes[0].checked = false; ui.change(ui.modes[0]);
  ui.rows.fields = ui.makeRows(); ui.editor.replay();
  assert.deepEqual(ui.rows.fields.map(field => field.value), ['hom', 'het', 'hom', 'wt']);
  assert.equal(ui.rows.fields[0].disabled, true);
});
test('a shared Leave unchanged selection leaves individual inputs available', () => {
  const ui = fixture(); ui.shared[0].value = '';
  ui.modes[0].checked = false; ui.change(ui.modes[0]);
  assert.equal(ui.rows.fields[0].disabled, false);
  assert.equal(ui.rows.fields[0].value, 'het');
});
test('single-animal mode re-enables its row when shared controls are hidden', () => {
  const ui = fixture();
  ui.modes[0].checked = false; ui.change(ui.modes[0]);
  ui.modes[0].closest = () => ({hidden: true});
  ui.editor.replay();
  assert.equal(ui.rows.fields[0].disabled, false);
});
