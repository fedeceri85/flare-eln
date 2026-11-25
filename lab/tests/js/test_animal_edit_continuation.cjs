const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');

const source = readFileSync(join(__dirname, '../../static/lab/animal_edit_continuation.js'), 'utf8');

function eventTarget(extra = {}) {
    const listeners = new Map();
    return Object.assign({
        addEventListener(name, callback, options = {}) {
            const entries = listeners.get(name) || [];
            entries.push({callback, once: Boolean(options.once)});
            listeners.set(name, entries);
        },
        removeEventListener(name, callback) {
            listeners.set(name, (listeners.get(name) || []).filter(entry => entry.callback !== callback));
        },
        emit(name, event = {}) {
            const entries = [...(listeners.get(name) || [])];
            event.defaultPrevented = Boolean(event.defaultPrevented);
            event.preventDefault ||= function () { this.defaultPrevented = true; };
            entries.forEach(entry => {
                entry.callback(event);
                if (entry.once) this.removeEventListener(name, entry.callback);
            });
            return event;
        },
    }, extra);
}

function fixture({preventPrimarySubmit = false, nativeValidationFailure = false} = {}) {
    const activeTab = {value: 'procedures'};
    const status = {textContent: ''};
    const staged = [];
    const primary = eventTarget({
        querySelector: selector => selector === '[data-mouse-active-tab]' ? activeTab : null,
        appendChild(input) { staged.push(input); },
        requestSubmit() {
            this.requested = true;
            if (!nativeValidationFailure) this.emit('submit', {});
        },
    });
    if (preventPrimarySubmit) primary.addEventListener('submit', event => event.preventDefault());

    const fields = new Map([
        ['recording-recording_type', {value: '7'}],
        ['recording-project', {value: '8'}],
        ['recording-experiment', {value: '9'}],
    ]);
    const recording = eventTarget({elements: {namedItem: name => fields.get(name)}});
    const document = eventTarget({
        getElementById(id) {
            if (id === 'primary-form') return primary;
            if (id === 'new-animal-recording-form') return recording;
            return null;
        },
        querySelector: selector => selector === '[data-save-continuation-status]' ? status : null,
        createElement() {
            return {
                dataset: {},
                remove() {
                    const index = staged.indexOf(this);
                    if (index >= 0) staged.splice(index, 1);
                },
            };
        },
    });
    const originalAddEventListener = document.addEventListener.bind(document);
    document.addEventListener = function (name, callback, options) {
        if (name === 'DOMContentLoaded') callback();
        else originalAddEventListener(name, callback, options);
    };

    runInNewContext(source, {document, Promise});
    return {document, primary, recording, activeTab, status, staged};
}

test('recording selection is copied into the main save before continuing', () => {
    const ui = fixture();
    const event = ui.recording.emit('submit', {});
    assert.equal(event.defaultPrevented, true);
    assert.equal(ui.primary.requested, true);
    assert.equal(ui.activeTab.value, 'services');
    assert.equal(ui.status.textContent, 'Saving animal changes before continuing…');
    assert.deepEqual(
        ui.staged.map(input => [input.name, input.value]),
        [
            ['select_recording_destination', '1'],
            ['recording-recording_type', '7'],
            ['recording-project', '8'],
            ['recording-experiment', '9'],
        ],
    );
});

test('recording workflow links save the main form before navigation', () => {
    const ui = fixture();
    const link = {
        href: 'https://eln.test/projects/8/experiments/add/?recording_type=7',
        closest: selector => selector === '[data-save-before-continue]' ? link : null,
    };
    const event = ui.document.emit('click', {
        target: link, button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
    });
    assert.equal(event.defaultPrevented, true);
    assert.equal(ui.primary.requested, true);
    assert.deepEqual(ui.staged.map(input => [input.name, input.value]), [
        ['save_and_continue_url', link.href],
    ]);
});

test('a blocked or invalid main save does not leave a stale continuation behind', async () => {
    for (const options of [{preventPrimarySubmit: true}, {nativeValidationFailure: true}]) {
        const ui = fixture(options);
        const link = {
            href: 'https://eln.test/projects/8/experiments/add/',
            closest: selector => selector === '[data-save-before-continue]' ? link : null,
        };
        ui.document.emit('click', {
            target: link, button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
        });
        await Promise.resolve();
        assert.deepEqual(ui.staged, []);
        assert.match(ui.status.textContent, /before continuing|highlighted animal details/);
    }
});
