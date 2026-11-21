const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

// Exercise the actual template script with a small DOM adapter, including async
// responses. The primary mouse form is deliberately outside the editor subtree.
const source = readFileSync(join(__dirname, '../../static/lab/existing_procedures.js'), 'utf8')
  .trim().replace(/^<script>/, '').replace(/<\/script>$/, '');
function element(properties = {}) {
  const handlers = new Map();
  return Object.assign({ disabled: false, hidden: false, checked: false, value: '', textContent: '',
    addEventListener(name, callback) {
      if (!handlers.has(name)) handlers.set(name, []);
      handlers.get(name).push(callback);
    },
    emit(name, properties = {}) {
      const event = { target: this, preventDefault() {}, ...properties };
      return Promise.all((handlers.get(name) || []).map(callback => callback(event)));
    },
  }, properties);
}
function makeEditor(preview = true) {
  const all = element();
  const rows = [element({ checked: true, name: 'existing_procedures-selected_procedures', value: '11' }),
    element({ name: 'existing_procedures-selected_procedures', value: '12' })];
  const toggle = element({ checked: true, name: 'existing_procedures-update_lab_member', value: 'on' });
  const field = element({ name: 'existing_procedures-lab_member', value: 'New member' });
  const snapshot = element({ name: 'existing_procedures-snapshot', value: preview ? 'signed-token' : '' });
  const save = element({ disabled: !preview, hidden: !preview });
  const deleteButton = element({ disabled: false, hidden: false });
  const previewButton = element();
  const status = element();
  const count = element();
  const previewContainer = { cleared: false, replaceChildren() { this.cleared = true; } };
  const group = { querySelector(selector) { return selector.includes('.bulk-input') ? field : toggle; } };
  const form = element({
    dataset: {},
    elements: [all, ...rows, toggle, field, snapshot, previewButton, save, deleteButton],
    querySelector(selector) {
      return ({ '[data-procedure-select-all]': all, '[data-procedure-save]': save, '[data-procedure-delete]': deleteButton,
        '[name="existing_procedures-snapshot"]': snapshot, '[data-procedure-status]': status,
        '[data-procedure-selection-count]': count, '[data-procedure-preview]': previewContainer })[selector];
    },
    querySelectorAll(selector) {
      return ({ '[data-procedure-row-checkbox]': rows, '[data-procedure-update-field]': [group],
        'button[type="submit"]': [previewButton, save, deleteButton] })[selector] || [];
    },
  });
  const section = { replaced: null, querySelector() { return form; }, querySelectorAll() { return []; },
    replaceWith(replacement) { this.replaced = replacement; } };
  return { section, form, rows, all, toggle, field, snapshot, save, deleteButton, status, count, previewContainer, previewButton };
}
function fixture() {
  const ui = makeEditor();
  let confirmResult = true;
  const confirmCalls = [];
  const primaryMouseForm = { notes: 'Unsaved mouse edits', sex: 'F' };
  let finish, reject, submitted;
  const replacement = makeEditor(false);
  runInNewContext(source, {
    document: {
      addEventListener() {},
      querySelectorAll(selector) { assert.equal(selector, '[data-existing-procedure-section]'); return [ui.section]; },
      createElement() { return { content: { querySelector() { return replacement.section; } } }; },
    },
    window: { location: { href: 'http://localhost/mice/edit-selected/?selected_mouse=Mouse' },
      confirm(message) { confirmCalls.push(message); return confirmResult; } },
    FormData: class {
      constructor(form) {
        this.entries = new Map(form.elements.filter(element => element.name && !element.disabled).map(element => [element.name, element.value]));
      }
      set(key, value) { this.entries.set(key, value); }
    },
    fetch(url, options) {
      submitted = options;
      return new Promise((resolve, failure) => { finish = resolve; reject = failure; });
    },
  });
  return { ...ui, primaryMouseForm, replacement, confirmCalls,
    setConfirmation(value) { confirmResult = value; },
    submit(action = 'preview') { return ui.form.emit('submit', { submitter: { value: action } }); },
    finish(ok = true) { finish({ ok, json: async () => ({ html: '<section>updated</section>' }) }); },
    fail() { reject(new Error('offline')); },
    get submitted() { return submitted; },
  };
}

test('select all updates count and invalidates signed preview in either direction', async () => {
  const ui = fixture();
  ui.all.checked = true;
  await ui.all.emit('change');
  assert.ok(ui.rows.every(row => row.checked));
  assert.equal(ui.count.textContent, '2 records selected');
  assert.equal(ui.snapshot.value, '');
  assert.equal(ui.save.disabled, true);
  assert.equal(ui.previewContainer.cleared, true);
  ui.all.checked = false;
  await ui.all.emit('change');
  assert.ok(ui.rows.every(row => !row.checked));
  assert.equal(ui.count.textContent, '0 records selected');
});

test('row, shared checkbox and value edits invalidate preview immediately', async () => {
  for (const target of ['rows', 'toggle', 'field']) {
    const ui = fixture();
    const control = target === 'rows' ? ui.rows[0] : ui[target];
    if (target === 'toggle') control.checked = false;
    await ui.form.emit('change', { target: control });
    assert.equal(ui.snapshot.value, '');
    assert.equal(ui.save.hidden, true);
    if (target === 'toggle') assert.equal(ui.field.disabled, true);
  }
});

test('network failure is inline, invalidates preview and preserves mouse inputs', async () => {
  const ui = fixture();
  const request = ui.submit('save');
  assert.ok(ui.rows.every(row => row.disabled));
  ui.fail();
  await request;
  assert.match(ui.status.textContent, /Could not complete/);
  assert.equal(ui.snapshot.value, '');
  assert.equal(ui.save.disabled, true);
  assert.ok(ui.rows.every(row => !row.disabled));
  assert.deepEqual(ui.primaryMouseForm, { notes: 'Unsaved mouse edits', sex: 'F' });
  assert.equal(ui.submitted.body.entries.get('existing_procedure_action'), 'save');
  assert.equal(ui.submitted.body.entries.has('apply_bulk_edit'), false);
});

test('successful save and inline validation replace only procedure section and initialize again', async () => {
  for (const ok of [true, false]) {
    const ui = fixture();
    const request = ui.submit();
    ui.finish(ok);
    await request;
    assert.equal(ui.section.replaced, ui.replacement.section);
    assert.equal(ui.replacement.count.textContent, '1 records selected');
    assert.equal(ui.primaryMouseForm.notes, 'Unsaved mouse edits');
    ui.replacement.all.checked = true;
    await ui.replacement.all.emit('change');
    assert.equal(ui.replacement.count.textContent, '2 records selected');
  }
});

test('edits during preview discard late response and keep new control enablement', async () => {
  const ui = fixture();
  const request = ui.submit();
  ui.toggle.checked = false;
  await ui.form.emit('change', { target: ui.toggle });
  ui.finish();
  await request;
  assert.equal(ui.section.replaced, null);
  assert.equal(ui.field.disabled, true);
  assert.equal(ui.save.disabled, true);
  assert.match(ui.status.textContent, /Preview again/);
});


test('delete remains directly available as selection changes and asks confirmation for selected count', async () => {
  const ui = fixture();
  ui.all.checked = true;
  await ui.all.emit('change');
  assert.equal(ui.deleteButton.hidden, false);
  assert.equal(ui.deleteButton.disabled, false);
  ui.setConfirmation(false);
  await ui.submit('delete');
  assert.match(ui.confirmCalls[0], /Delete 2 selected procedure records/);
  assert.equal(ui.submitted, undefined);
  assert.equal(ui.rows.filter(row => row.checked).length, 2);
});

test('confirmed deletion does not require a preview and only locks the procedure form', async () => {
  const ui = fixture();
  ui.snapshot.value = '';
  const pending = ui.submit('delete');
  assert.equal(ui.submitted.body.entries.get('existing_procedure_action'), 'delete');
  assert.equal(ui.submitted.body.entries.get('existing_procedures-confirm_delete'), '1');
  assert.match(ui.status.textContent, /Deleting/);
  assert.ok(ui.rows.every(row => row.disabled));
  ui.fail();
  await pending;
  assert.equal(ui.deleteButton.hidden, false);
  assert.equal(ui.deleteButton.disabled, false);
  assert.equal(ui.primaryMouseForm.notes, 'Unsaved mouse edits');
});

test('zero selection cannot confirm or send a deletion', async () => {
  const ui = fixture();
  ui.all.checked = false;
  await ui.all.emit('change');
  assert.equal(ui.deleteButton.disabled, true);
  await ui.submit('delete');
  assert.equal(ui.confirmCalls.length, 0);
  assert.equal(ui.submitted, undefined);
});

test('confirmed deletion only refreshes the procedure editor', async () => {
  const ui = fixture();
  const pending = ui.submit('delete');
  assert.equal(ui.submitted.body.entries.has('apply_bulk_edit'), false);
  ui.finish();
  await pending;
  assert.equal(ui.section.replaced, ui.replacement.section);
  assert.equal(ui.primaryMouseForm.notes, 'Unsaved mouse edits');
});
