const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');
const source = ['mouse_row_editor', 'litter_mouse_rows'].map(name =>
  readFileSync(join(__dirname, '../../static/lab/' + name + '.js'), 'utf8')).join('\n');
function element(properties = {}) {
  const listeners = {};
  return Object.assign({ type: 'text', checked: false, value: '', disabled: false,
    addEventListener(type, callback) { (listeners[type] ||= []).push(callback); },
    emit(type, properties = {}) {
      const event = { target: this, preventDefault() { this.prevented = true; }, ...properties };
      (listeners[type] || []).forEach(callback => callback(event));
      return event;
    },
  }, properties);
}
function fixture() {
  const button = element();
  const retry = { hidden: true };
  const status = { textContent: '' };
  const prefix = element({ value: 'PUP-' });
  const generated = element({ type: 'checkbox' });
  const numbers = element({ value: '1-2' });
  const rowField = () => element({ name: 'pup_1_marking', value: 'old-id' });
  const rows = { fields: [rowField()], querySelectorAll(selector) { return selector.startsWith("details") ? [] : this.fields; } };
  Object.defineProperty(rows, 'innerHTML', { set(html) { this.lastHtml = html; this.fields = [rowField()]; } });
  function overrideRegion(toggleName, textName, type = 'textarea') {
    const makeFields = () => [element({name: toggleName, type: 'checkbox'}), element({name: textName, type})];
    const region = {fields: makeFields(), hidden: false, open: false,
      querySelector(selector) {return selector.includes(':checked') ? this.fields.find(field => field.type === 'checkbox' && field.checked) : this.fields[0];},
      querySelectorAll(selector) {
        if (selector === 'details[open]') return this.open ? [this] : [];
        if (selector === 'details') return [this];
        return this.fields;
      },
    };
    Object.defineProperty(region, 'innerHTML', {set(html) {this.lastHtml = html; this.fields = makeFields(); this.open = false;}});
    return region;
  }
  const details = overrideRegion('pup_1_override_notes_enabled', 'pup_1_override_notes');
  const parents = overrideRegion('pup_1_parent_update_mother', 'pup_1_parent_mother_external_id', 'text');
  const form = element({
    closest() { return {dataset: {mouseCount: "2"}}; },
    querySelectorAll() { return []; },
    elements: { mouse_id_prefix: prefix, no_id_available: generated, mouse_numbers: numbers },
    querySelector(selector) { return ({ '[data-preview-mice]': button, '[data-litter-preview-status]': status, '[data-litter-rows-retry]': retry, '[data-litter-detail-overrides]': details, '[data-litter-parent-overrides]': parents })[selector]; },
  });
  let scheduled;
  const requests = [];
  let fetchCount = 0;
  runInNewContext(source, {
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    document: { dispatchEvent() {}, addEventListener(type, callback) { callback(); }, querySelector() { return form; }, getElementById() { return rows; } },
    window: { location: { href: 'http://localhost/mice/add-litter/' } },
    FormData: class { set() {} },
    setTimeout(callback) { scheduled = callback; return 1; }, clearTimeout() { scheduled = null; },
    fetch() { fetchCount++; return new Promise(resolve => { requests.push(resolve); }); },
  });
  return { button, retry, status, rows, numbers, form, prefix, generated, details, parents,
    runScheduled() { scheduled(); },
    get fetchCount() { return fetchCount; },
    get scheduled() { return scheduled; },
    async finish(ok = true, result = {}, index = 0) {
      const resolve = requests.splice(index, 1)[0];
      resolve({ ok, json: async () => ({ html: 'new rows', prefix: 'PUP-', errors: [], ...result }) });
      await new Promise(resolve => setImmediate(resolve));
    },
  };
}

test('button stays hidden during normal automatic updates and after success', async () => {
  const ui = fixture();
  assert.equal(ui.retry.hidden, true);
  ui.numbers.emit('input');
  ui.runScheduled();
  assert.equal(ui.fetchCount, 1);
  assert.equal(ui.retry.hidden, true);
  assert.equal(ui.button.disabled, true);
  await ui.finish();
  assert.equal(ui.retry.hidden, true);
  assert.equal(ui.button.disabled, false);
});

test('failure shows AJAX retry and preserves identifier edits made during retry', async () => {
  const ui = fixture();
  ui.numbers.emit('change');
  ui.runScheduled();
  await ui.finish(false);
  assert.equal(ui.retry.hidden, false);
  assert.match(ui.status.textContent, /Retry updating/);
  const click = ui.button.emit('click');
  assert.equal(click.prevented, true);
  assert.equal(ui.fetchCount, 2);
  assert.equal(ui.retry.hidden, true);
  ui.rows.fields[0].value = 'new-ear-clip';
  await ui.finish();
  assert.equal(ui.rows.fields[0].value, 'new-ear-clip');
  assert.equal(ui.retry.hidden, true);
});

test('saving waits for an automatic row update before allowing submission', async () => {
  const ui = fixture();
  ui.numbers.emit('input');
  const first = ui.form.emit('submit');
  assert.equal(first.prevented, true);
  assert.equal(ui.fetchCount, 1);
  await ui.finish();
  assert.notEqual(ui.form.emit('submit').prevented, true);
});

test('a late response cannot overwrite a newer refresh or its success state', async () => {
  const ui = fixture();
  ui.numbers.emit('input');
  ui.runScheduled();
  ui.numbers.emit('input');
  ui.runScheduled();
  await ui.finish(true, { html: 'latest rows', prefix: 'LATEST-' }, 1);
  assert.equal(ui.rows.lastHtml, 'latest rows');
  assert.equal(ui.prefix.value, 'LATEST-');
  await ui.finish(false);
  assert.equal(ui.rows.lastHtml, 'latest rows');
  assert.equal(ui.prefix.value, 'LATEST-');
  assert.equal(ui.retry.hidden, true);
  assert.equal(ui.button.disabled, false);
});

test('changing source fields invalidates the response before the next request begins', async () => {
  const ui = fixture();
  ui.numbers.emit('change');
  ui.runScheduled();
  ui.numbers.emit('change');
  await ui.finish(true, { html: 'outdated rows' });
  assert.equal(ui.rows.lastHtml, undefined);
  ui.runScheduled();
  await ui.finish(true, { html: 'current rows' });
  assert.equal(ui.rows.lastHtml, 'current rows');
});

test('saving while a refresh is scheduled starts just one request', async () => {
  const ui = fixture();
  ui.numbers.emit('input');
  ui.form.emit('submit');
  assert.equal(ui.fetchCount, 1);
  // The scheduled callback is cancelled when the submit starts its refresh.
  assert.equal(ui.scheduled, null);
  await ui.finish();
  assert.equal(ui.fetchCount, 1);
});

test('malformed previews keep inputs intact and offer retry', async () => {
  const ui = fixture();
  ui.button.emit('click');
  ui.rows.fields[0].value = 'latest-local-id';
  await ui.finish(true, { html: null });
  assert.equal(ui.rows.lastHtml, undefined);
  assert.equal(ui.rows.fields[0].value, 'latest-local-id');
  assert.equal(ui.retry.hidden, false);
  assert.equal(ui.button.disabled, false);
});

test('individual details and parent edits made during refresh survive along with expanded sections', async () => {
  const ui = fixture();
  ui.numbers.emit('input'); ui.runScheduled();
  ui.details.fields[0].checked = true;
  ui.details.fields[1].value = 'Unsaved individual notes';
  ui.details.open = true;
  ui.parents.fields[0].checked = true;
  ui.parents.fields[1].value = 'External mother';
  await ui.finish(true, {detail_overrides_html: 'details', parent_overrides_html: 'parents', mouse_count: 3});
  assert.equal(ui.details.fields[0].checked, true);
  assert.equal(ui.details.fields[1].value, 'Unsaved individual notes');
  assert.equal(ui.details.open, true);
  assert.equal(ui.parents.fields[0].checked, true);
  assert.equal(ui.parents.fields[1].value, 'External mother');
});

test('changing an individual crossing schedules a guarded row refresh', () => {
  const ui = fixture();
  ui.form.emit('change', {target: {hasAttribute: name => name === 'data-litter-crossing-override'}});
  assert.ok(ui.scheduled);
  ui.runScheduled();
  assert.equal(ui.fetchCount, 1);
});
