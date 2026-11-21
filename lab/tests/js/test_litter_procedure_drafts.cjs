const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');
const source = readFileSync(join(__dirname, '../../static/lab/litter_procedures.js'), 'utf8')
  .trim().replace(/^<script>/, '').replace(/<\/script>$/, '');
function element(extra = {}) {
  const listeners = {};
  return Object.assign({ children: [], value: '', textContent: '', hidden: false,
    addEventListener(name, callback) { (listeners[name] ||= []).push(callback); },
    emit(name) {
      const event = { preventDefault() { this.prevented = true; this.defaultPrevented = true; } };
      (listeners[name] || []).forEach(callback => callback(event));
      return event;
    },
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    focus() {},
  }, extra);
}
function fixture(initial = [], scope = 'litter', disabled = false) {
  const fields = Object.fromEntries(['procedure_type', 'date', 'lab_member', 'lab_book_page'].map(name => [name, element()]));
  fields.procedure_type.options = [{ value: '1', textContent: 'Injection' }, { value: '2', textContent: 'Observation' }];
  const primary = element();
  const windowEvents = element();
  const stored = { form: primary, value: JSON.stringify(initial), changes: 0, dispatchEvent() { this.changes++; } };
  const rows = element();
  const table = element();
  const empty = element();
  const status = element();
  const add = element();
  const cancelButton = element();
  const cancel = element({ querySelector() { return cancelButton; } });
  const fieldset = { disabled };
  const form = element({
    dataset: { procedureScope: scope },
    elements: { namedItem(name) { return fields[name.replace('new_procedure-', '')]; } },
    querySelector(selector) { return ({
      '[data-litter-procedure-add]': add, '[data-litter-procedure-cancel]': cancel,
      '[data-litter-procedure-status]': status, '[data-procedure-fieldset]': fieldset,
    })[selector]; },
    reset() { Object.values(fields).forEach(field => { field.value = ''; }); },
    reportValidity() { return fields.procedure_type.value !== ''; },
  });
  let confirmed = true;
  runInNewContext(source, {
    document: {
      addEventListener(name, callback) { callback(); },
      getElementById() { return stored; },
      querySelector(selector) { return ({
        '[data-litter-procedure-form]': form, '[data-litter-procedures-rows]': rows,
        '[data-litter-procedures-table]': table, '[data-litter-procedures-empty]': empty,
      })[selector]; },
      createElement() { return element(); },
    },
    window: { confirm() { return confirmed; }, addEventListener: windowEvents.addEventListener.bind(windowEvents) },
    Event: class { constructor(type, options) { this.type = type; this.bubbles = options.bubbles; } },
  });
  return { fields, stored, rows, table, empty, form, status, add, cancel, cancelButton, fieldset, primary, windowEvents,
    drafts() { return JSON.parse(stored.value); },
    confirm(value) { confirmed = value; },
    submit(type = '1', member = '') { fields.procedure_type.value = type; fields.lab_member.value = member; return form.emit('submit'); },
    action(index, name) { rows.children[index].children[4].children[name === 'Edit' ? 0 : 1].emit('click'); },
  };
}
test('adds multiple procedures to table and hidden queue without a server request', () => {
  const ui = fixture();
  assert.equal(ui.empty.hidden, false);
  assert.equal(ui.submit('1', 'AB').prevented, true);
  ui.submit('2', 'CD');
  ui.submit('1', 'EF');
  assert.equal(ui.rows.children.length, 3);
  assert.equal(ui.drafts().length, 3);
  assert.deepEqual(ui.drafts().map(draft => draft.lab_member), ['AB', 'CD', 'EF']);
  assert.equal(ui.rows.children[0].children[0].textContent, 'Injection');
  assert.equal(ui.table.hidden, false);
  assert.equal(ui.empty.hidden, true);
  assert.equal(ui.fields.procedure_type.value, '');
});
test('editing replaces exactly one entry; cancel leaves the queue intact', () => {
  const ui = fixture([{ procedure_type: 1, lab_member: 'Original' }, { procedure_type: 2 }]);
  ui.action(0, 'Edit');
  assert.equal(ui.fields.lab_member.value, 'Original');
  assert.equal(ui.add.textContent, 'Update procedure');
  ui.submit('2', 'Updated');
  assert.equal(ui.drafts().length, 2);
  assert.equal(ui.drafts()[0].lab_member, 'Updated');
  assert.equal(ui.drafts()[1].procedure_type, 2);
  ui.action(0, 'Edit');
  ui.fields.lab_member.value = 'Discard';
  ui.cancelButton.emit('click');
  assert.equal(ui.drafts()[0].lab_member, 'Updated');
  assert.equal(ui.cancel.hidden, true);
});
test('remove requires confirmation and updates the empty state', () => {
  const ui = fixture([{ procedure_type: 1 }]);
  ui.confirm(false);
  ui.action(0, 'Remove');
  assert.equal(ui.drafts().length, 1);
  ui.confirm(true);
  ui.action(0, 'Remove');
  assert.deepEqual(ui.drafts(), []);
  assert.equal(ui.table.hidden, true);
  assert.equal(ui.empty.hidden, false);
});
test('disabled protocol and invalid form cannot add; unadded inputs stay out of queue', () => {
  const ui = fixture();
  ui.fieldset.disabled = true;
  ui.submit();
  assert.equal(ui.drafts().length, 0);
  ui.fieldset.disabled = false;
  ui.submit('');
  assert.equal(ui.drafts().length, 0);
  ui.fields.procedure_type.value = '1';
  assert.equal(ui.drafts().length, 0);
});
test('reload reconstructs drafts and renders user values as text', () => {
  const ui = fixture([{ procedure_type: 2, lab_member: '<script>bad</script>', date: '2026-10-21' }]);
  assert.equal(ui.rows.children[0].children[2].textContent, '<script>bad</script>');
  assert.equal(ui.rows.children[0].children[1].textContent, '2026-10-21');
  assert.equal(ui.drafts()[0].procedure_type, 2);
});

test('import mode reuses the editor and notifies the wizard when the list changes', () => {
  const ui = fixture([{ procedure_type: 1, lab_member: 'Stored' }], 'import');
  assert.equal(ui.fieldset.disabled, false);
  assert.equal(ui.stored.changes, 0);
  ui.submit('2', 'Added');
  assert.equal(ui.stored.changes, 1);
  assert.match(ui.status.textContent, /complete the import/);
  ui.action(0, 'Edit');
  ui.submit('1', 'Edited');
  assert.equal(ui.stored.changes, 2);
  ui.action(1, 'Remove');
  assert.equal(ui.stored.changes, 3);
  assert.deepEqual(ui.drafts().map(row => row.lab_member), ['Edited']);
});

test('import mode preserves protocol restrictions while allowing queued procedures to be removed', () => {
  const ui = fixture([{ procedure_type: 1 }], 'import', true);
  assert.equal(ui.fieldset.disabled, true);
  ui.submit('2');
  assert.deepEqual(ui.drafts(), [{ procedure_type: 1 }]);
  ui.action(0, 'Remove');
  assert.deepEqual(ui.drafts(), []);
  assert.equal(ui.fieldset.disabled, true);
});


test('animal editor queues procedures until Save and warns before leaving', () => {
  const ui = fixture([], 'animal');
  ui.submit('1', 'Pending');
  assert.match(ui.status.textContent, /press Save changes/);
  assert.equal(ui.drafts().length, 1);
  assert.equal(ui.windowEvents.emit('beforeunload').prevented, true);
  assert.equal(ui.primary.emit('submit').prevented, undefined);
  assert.equal(ui.primary.emit('submit').prevented, true);
  assert.equal(ui.windowEvents.emit('beforeunload').prevented, undefined);
  ui.windowEvents.emit('pageshow');
  assert.equal(ui.windowEvents.emit('beforeunload').prevented, true);
  ui.action(0, 'Remove');
  assert.equal(ui.windowEvents.emit('beforeunload').prevented, undefined);
});

test('finish editing a queued procedure before main Save', () => {
  const ui = fixture([{ procedure_type: 1 }], 'animal');
  ui.action(0, 'Edit');
  assert.equal(ui.primary.emit('submit').prevented, true);
  assert.match(ui.status.textContent, /Update the procedure/);
  ui.submit('2', 'Updated');
  assert.equal(ui.drafts()[0].procedure_type, '2');
  assert.equal(ui.primary.emit('submit').prevented, undefined);
});

test('another editor control preventing Save keeps the pending procedure warning', async () => {
  const ui = fixture([{ procedure_type: 1 }], 'animal');
  ui.primary.addEventListener('submit', event => event.preventDefault());
  assert.equal(ui.primary.emit('submit').prevented, true);
  await Promise.resolve();
  assert.equal(ui.windowEvents.emit('beforeunload').prevented, true);
});
