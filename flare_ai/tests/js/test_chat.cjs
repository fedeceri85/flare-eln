const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../../static/flare_ai/chat.js'), 'utf8');

const flush = () => new Promise(resolve => setImmediate(resolve));
const tick = async (n = 15) => { for (let i = 0; i < n; i += 1) await flush(); };

function element(extra = {}) {
    return Object.assign({
        hidden: false,
        textContent: '',
        value: '',
        className: '',
        dataset: {},
        attrs: {},
        events: {},
        children: [],
        disabled: false,
        files: null,
        scrollTop: 0,
        scrollHeight: 0,
        focused: false,
        classList: {
            _set: new Set(),
            add(...names) { names.forEach(name => this._set.add(name)); },
            remove(...names) { names.forEach(name => this._set.delete(name)); },
            contains(name) { return this._set.has(name); },
        },
        addEventListener(name, callback) { this.events[name] = callback; },
        emit(name, event = {}) { return this.events[name]?.({preventDefault() {}, ...event}); },
        setAttribute(name, value) { this.attrs[name] = value; },
        append(...nodes) { this.children.push(...nodes); },
        replaceChildren(...nodes) { this.children = [...nodes]; },
        focus() { this.focused = true; },
        click() { this.emit('click'); },
        requestSubmit() { this.emit('submit', {preventDefault() {}}); },
    }, extra);
}

function streamResponse(events = [
    {type: 'start', conversation_id: 'uuid-1234-5678'},
    {type: 'delta', text: 'Hello '},
    {type: 'delta', text: 'world'},
    {type: 'done', conversation_id: 'uuid-1234-5678', suggestions: []},
]) {
    const encoder = new TextEncoder();
    let index = 0;
    return {
        ok: true,
        status: 200,
        json: async () => ({}),
        body: {
            getReader() {
                return {
                    read() {
                        if (index < events.length) {
                            const line = `${JSON.stringify(events[index])}\n`;
                            index += 1;
                            return Promise.resolve({value: encoder.encode(line), done: false});
                        }
                        return Promise.resolve({value: undefined, done: true});
                    },
                };
            },
        },
    };
}

const FORM_DATA_MARKER = '__flareFormData';

function fixture(options = {}) {
    const parts = {};
    const selectorMap = {
        '[data-ai-launcher]': 'launcher',
        '[data-ai-drawer]': 'drawer',
        '[data-ai-close]': 'close',
        '[data-ai-reset]': 'reset',
        '[data-ai-retry]': 'retry',
        '[data-ai-stop]': 'stop',
        '[data-ai-send]': 'send',
        '[data-ai-messages]': 'messages',
        '[data-ai-suggestions]': 'suggestions',
        '[data-ai-status]': 'status',
        '[data-ai-form]': 'form',
        '[data-ai-input]': 'input',
        '[data-ai-attach]': 'attach',
        '[data-ai-attachment]': 'attachment',
        '[data-ai-attachment-chip]': 'chip',
        '[data-ai-remove-attachment]': 'remove-attachment',
        '[data-ai-file-input]': 'file-input',
    };
    for (const key of Object.values(selectorMap)) parts[key] = element();
    parts.form.querySelector = (selector) => (selector === '[name=csrfmiddlewaretoken]' ? {value: 'csrf-token'} : null);

    const root = element({
        dataset: {assistantName: 'Marcott-E', streamUrl: '/ai/chat/stream/', resetUrl: '/ai/chat/reset/'},
        querySelector(selector) { return parts[selectorMap[selector]] ?? null; },
    });

    const requests = [];
    const document = {
        querySelector(selector) { return selector === '[data-flare-ai-chat]' ? root : null; },
        createElement: (tag) => element({tagName: tag}),
        createTextNode: (text) => ({text}),
    };
    const fetchMock = (url, config) => {
        requests.push({url, config});
        return Promise.resolve(streamResponse(options.events));
    };
    const sessionStorage = {
        _store: {},
        getItem(key) { return this._store[key] ?? null; },
        setItem(key, value) { this._store[key] = value; },
    };
    class FormData {
        constructor() { this.fields = {}; this[FORM_DATA_MARKER] = true; }
        append(key, value) { (this.fields[key] ||= []).push(value); }
    }

    runInNewContext(source, {
        document,
        window: {location: {pathname: '/mice/', origin: 'http://localhost'}, addEventListener() {}},
        crypto: {randomUUID: () => 'uuid-1234-5678'},
        sessionStorage,
        fetch: fetchMock,
        FormData,
        AbortController: class {
            constructor() { this.signal = {aborted: false}; }
            abort() { this.signal.aborted = true; }
        },
        TextDecoder: globalThis.TextDecoder,
        URL: globalThis.URL,
        setTimeout: globalThis.setTimeout,
        setImmediate: globalThis.setImmediate,
    });

    return {
        parts,
        requests,
        file(name, size, type = '') { return {name, size, type}; },
        selectFile(file) {
            parts['file-input'].files = [file];
            parts['file-input'].emit('change');
        },
        attach() { parts.attach.emit('click'); },
        remove() { parts['remove-attachment'].emit('click'); },
        close() { parts.close.emit('click'); },
        reset() { parts.reset.emit('click'); },
        submit(text) {
            parts.input.value = text;
            parts.form.emit('submit', {preventDefault() {}});
        },
        settle: tick,
    };
}

test('selecting a file shows a removable chip with the name and size', () => {
    const ui = fixture();
    ui.selectFile(ui.file('data.csv', 2048, 'text/csv'));
    assert.equal(ui.parts.attachment.hidden, false);
    assert.match(ui.parts.chip.textContent, /data\.csv/);
    assert.match(ui.parts.chip.textContent, /2\.0 KB/);
});

test('selecting a new file replaces the previous attachment', () => {
    const ui = fixture();
    ui.selectFile(ui.file('data.csv', 2048, 'text/csv'));
    assert.match(ui.parts.chip.textContent, /data\.csv/);
    ui.selectFile(ui.file('notes.md', 120, 'text/markdown'));
    assert.match(ui.parts.chip.textContent, /notes\.md/);
    assert.doesNotMatch(ui.parts.chip.textContent, /data\.csv/);
});

test('removing the file hides the chip and clears it', () => {
    const ui = fixture();
    ui.selectFile(ui.file('data.csv', 2048, 'text/csv'));
    assert.equal(ui.parts.attachment.hidden, false);
    ui.remove();
    assert.equal(ui.parts.attachment.hidden, true);
    assert.equal(ui.parts.chip.textContent, '');
});

test('a file prompt is submitted as multipart with the file and form fields', async () => {
    const ui = fixture();
    const file = ui.file('data.csv', 2048, 'text/csv');
    ui.selectFile(file);
    ui.submit('What is this?');
    await ui.settle();
    assert.equal(ui.requests.length, 1);
    const request = ui.requests[0];
    assert.equal(request.url, '/ai/chat/stream/');
    assert.equal(request.config.body[FORM_DATA_MARKER], true);
    assert.equal(request.config.body.fields.message[0], 'What is this?');
    assert.equal(request.config.body.fields.attachment[0], file);
    assert.equal(request.config.body.fields.page_path[0], '/mice/');
    assert.ok(request.config.body.fields.conversation_id[0]);
    assert.equal(request.config.headers['Content-Type'], undefined);
    assert.equal(request.config.headers['X-CSRFToken'], 'csrf-token');
});

test('the attachment is resent on follow-ups and dropped after removal', async () => {
    const ui = fixture();
    const file = ui.file('data.csv', 2048, 'text/csv');
    ui.selectFile(file);
    ui.submit('First question');
    await ui.settle();
    ui.submit('Second question');
    await ui.settle();
    assert.equal(ui.requests.length, 2);
    assert.equal(ui.requests[0].config.body[FORM_DATA_MARKER], true);
    assert.equal(ui.requests[1].config.body[FORM_DATA_MARKER], true);
    assert.equal(ui.requests[0].config.body.fields.attachment[0], file);
    assert.equal(ui.requests[1].config.body.fields.attachment[0], file);

    ui.remove();
    ui.submit('Third question');
    await ui.settle();
    assert.equal(ui.requests.length, 3);
    const third = ui.requests[2].config;
    assert.equal(typeof third.body, 'string');
    assert.equal(third.headers['Content-Type'], 'application/json');
    const parsed = JSON.parse(third.body);
    assert.equal(parsed.message, 'Third question');
    assert.equal(parsed.attachment, undefined);
});

test('closing the drawer clears the attachment', () => {
    const ui = fixture();
    ui.selectFile(ui.file('data.csv', 2048, 'text/csv'));
    assert.equal(ui.parts.attachment.hidden, false);
    ui.close();
    assert.equal(ui.parts.attachment.hidden, true);
    assert.equal(ui.parts.chip.textContent, '');
});

test('resetting the conversation clears the attachment', async () => {
    const ui = fixture();
    ui.selectFile(ui.file('data.csv', 2048, 'text/csv'));
    assert.equal(ui.parts.attachment.hidden, false);
    ui.reset();
    await ui.settle();
    assert.equal(ui.parts.attachment.hidden, true);
    assert.equal(ui.parts.chip.textContent, '');
});

const TOOLS_EVENTS = [
    {type: 'start', conversation_id: 'uuid-1234-5678'},
    {type: 'delta', text: 'Found 3 mice.'},
    {
        type: 'done',
        conversation_id: 'uuid-1234-5678',
        suggestions: [],
        tools: [
            {name: 'search_mice', count: 2},
            {name: 'user_guide', count: 1},
        ],
    },
];

const toolsBoxOf = (ui, index) => {
    const item = ui.parts.messages.children[index];
    return item.children.find((child) => child.className === 'flare-ai-tools');
};

test('a done event with tools renders a collapsed tools toggle', async () => {
    const ui = fixture({events: TOOLS_EVENTS});
    ui.submit('What mice are there?');
    await ui.settle();
    const toolsBox = toolsBoxOf(ui, ui.parts.messages.children.length - 1);
    assert.ok(toolsBox, 'tools section rendered under the assistant message');
    const toggle = toolsBox.children[0];
    assert.equal(toggle.className, 'flare-ai-tools-toggle');
    assert.match(toggle.textContent, /3 tools used/);
    assert.equal(toggle.attrs['aria-expanded'], 'false');
    assert.equal(toolsBox.children.length, 1, 'list stays hidden while collapsed');
});

test('the tools toggle expands the raw tool names and keeps its state across re-renders', async () => {
    const ui = fixture({events: TOOLS_EVENTS});
    ui.submit('What mice are there?');
    await ui.settle();
    toolsBoxOf(ui, 1).children[0].click();
    await ui.settle();

    const expanded = toolsBoxOf(ui, 1);
    assert.equal(expanded.children[0].attrs['aria-expanded'], 'true');
    const list = expanded.children[1];
    assert.equal(list.className, 'flare-ai-tools-list');
    assert.deepEqual(list.children.map((li) => li.textContent), ['search_mice (×2)', 'user_guide']);

    // A follow-up message re-renders the whole list; the expanded state must survive.
    ui.submit('Tell me more');
    await ui.settle();
    assert.equal(toolsBoxOf(ui, 1).children[0].attrs['aria-expanded'], 'true');
    assert.deepEqual(
        toolsBoxOf(ui, 1).children[1].children.map((li) => li.textContent),
        ['search_mice (×2)', 'user_guide'],
    );
});
