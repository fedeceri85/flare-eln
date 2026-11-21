const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');
const source = ['mouse_genotype_editor', 'litter_genotypes'].map(name =>
  readFileSync(join(__dirname, '../../static/lab/' + name + '.js'), 'utf8')).join('\n');
function field(name, lineId, individual, uncertainty = false) {
  return { name, dataset: { lineId }, type: uncertainty ? 'checkbox' : 'select-one', value: 'unknown', checked: false,
    matches(selector) { return selector.includes(individual ? 'data-individual-' : '[data-genotype-'); } };
}
function fixture({ individualValue = 'unknown' } = {}) {
  const sharedGene = field('genotype_line_1', '1', false);
  const sharedUncertain = field('genotype_line_1_uncertain', '1', false, true);
  const status = {};
  let fields = [sharedGene, sharedUncertain];
  const shared = { querySelectorAll() { return fields; } };
  const row1 = field('pup_1_genotype_line_1', '1', true);
  row1.value = individualValue;
  const row1Uncertain = field('pup_1_genotype_line_1_uncertain', '1', true, true);
  const rows = { fields: [row1, row1Uncertain], querySelectorAll() { return this.fields; } };
  const formListeners = {};
  const docListeners = {};
  const form = {
    querySelector(selector) { return selector.includes('status') ? status : shared; },
    addEventListener(name, callback) { formListeners[name] = callback; },
  };
  Object.defineProperty(shared, 'innerHTML', { set(html) {
    fields = html === 'none' ? [] : [field('genotype_line_1', '1', false), field('genotype_line_1_uncertain', '1', false, true)];
  } });
  runInNewContext(source, { window: {}, document: {
    querySelector() { return form; }, getElementById() { return rows; },
    addEventListener(name, callback) { if (name === 'DOMContentLoaded') callback(); else docListeners[name] = callback; },
  } });
  return { rows, shared, row1, row1Uncertain,
    get sharedGene() { return fields[0]; }, get sharedUncertain() { return fields[1]; },
    change(target) { formListeners.change({ target }); },
    refresh(html = 'genes') { docListeners['litter:rows-updated']({ detail: { genotypeHtml: html } }); },
    addMouse(number) {
      const genotype = field(`pup_${number}_genotype_line_1`, '1', true);
      const uncertainty = field(`pup_${number}_genotype_line_1_uncertain`, '1', true, true);
      rows.fields.push(genotype, uncertainty);
      return { genotype, uncertainty };
    },
  };
}
test('shared changes instantly fill all mouse rows without an enable checkbox', () => {
  const ui = fixture();
  const mouse2 = ui.addMouse(2);
  ui.sharedGene.value = 'het';
  ui.change(ui.sharedGene);
  ui.sharedUncertain.checked = true;
  ui.change(ui.sharedUncertain);
  assert.equal(ui.row1.value, 'het');
  assert.equal(mouse2.genotype.value, 'het');
  assert.equal(ui.row1Uncertain.checked, true);
  assert.equal(mouse2.uncertainty.checked, true);
});
test('new shared changes replace matching fields; later individual choices survive refresh', () => {
  const ui = fixture();
  ui.sharedGene.value = 'het';
  ui.change(ui.sharedGene);
  ui.row1.value = 'hom';
  ui.change(ui.row1);
  ui.refresh();
  assert.equal(ui.row1.value, 'hom');
  assert.equal(ui.sharedGene.value, 'het');
  ui.sharedGene.value = 'wt';
  ui.change(ui.sharedGene);
  assert.equal(ui.row1.value, 'wt');
});
test('new mouse rows inherit shared genotype and uncertainty after asynchronous refresh', () => {
  const ui = fixture();
  ui.sharedGene.value = 'het';
  ui.sharedUncertain.checked = true;
  ui.change(ui.sharedGene);
  ui.change(ui.sharedUncertain);
  ui.row1Uncertain.checked = false;
  ui.change(ui.row1Uncertain);
  const mouse2 = ui.addMouse(2);
  ui.refresh();
  assert.equal(mouse2.genotype.value, 'het');
  assert.equal(mouse2.uncertainty.checked, true);
  assert.equal(ui.row1Uncertain.checked, false);
});
test('restoring a validation page preserves displayed individual overrides', () => {
  const ui = fixture({ individualValue: 'hom' });
  ui.refresh();
  assert.equal(ui.row1.value, 'hom');
});
test('fields appear when a crossing is selected and disappear when cleared', () => {
  const ui = fixture();
  ui.refresh('none');
  assert.equal(ui.shared.querySelectorAll().length, 0);
  ui.refresh();
  assert.equal(ui.shared.querySelectorAll().length, 2);
});

test('clearing and reselecting a crossing cannot replay stale shared values', () => {
  const ui = fixture();
  ui.sharedGene.value = 'het';
  ui.change(ui.sharedGene);
  ui.rows.fields = [];
  ui.refresh('none');
  const newMouse = ui.addMouse(1);
  ui.refresh();
  assert.equal(ui.sharedGene.value, 'unknown');
  assert.equal(newMouse.genotype.value, 'unknown');
});

test('shared genotype and uncertainty only update the matching gene', () => {
  const ui = fixture();
  const sharedOther = field('genotype_line_2', '2', false);
  const sharedOtherUncertain = field('genotype_line_2_uncertain', '2', false, true);
  const other = field('pup_1_genotype_line_2', '2', true);
  const otherUncertain = field('pup_1_genotype_line_2_uncertain', '2', true, true);
  ui.shared.querySelectorAll().push(sharedOther, sharedOtherUncertain);
  ui.rows.fields.push(other, otherUncertain);
  ui.sharedUncertain.checked = true;
  ui.change(ui.sharedUncertain);
  assert.equal(ui.row1Uncertain.checked, true);
  assert.equal(otherUncertain.checked, false);
  sharedOther.value = 'cre';
  ui.change(sharedOther);
  assert.equal(other.value, 'cre');
  assert.equal(ui.row1.value, 'unknown');
});
