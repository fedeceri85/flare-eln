const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const source = readFileSync(join(__dirname, '../../static/lab/app.js'), 'utf8');

function element(extra = {}) {
    const events = {};
    return Object.assign({checked: false, textContent: '',
        addEventListener(name, fn) { events[name] = fn; },
        emit(name) { events[name]?.(); },
        dispatchEvent(event) { events[event.type]?.(event); },
    }, extra);
}

function fixture({scoped = false, empty = false, restored = false, animalTable = true, rowCount = 2} = {}) {
    const createUrl = scoped ? '/procedures/licence/17/add/?return_url=%2Fprocedures%2F' : '/mice/add/?return_url=%2Fmice%2F';
    const editPath = scoped ? '/procedures/licence/17/view-selected/' : '/mice/edit/';
    const returnUrl = '/mice/?mouse_id=Alpha&page=2&sort=mouse_id';
    const action = element({href: createUrl,
        'data-animal-selected-label': scoped ? 'View selected' : null,
        getAttribute(name) { return this[name]; },
        setAttribute(name, value) { this[name] = value; },
    });
    const rows = empty ? [] : Array.from({length: rowCount}, (_, i) => element({
        value: i === 0 ? 'Mouse / 1 & "sample"' : 'Mouse ' + (i + 1), checked: restored && i === 0,
        closest(selector) {return selector === '.table-card' ? card : selector === '[hidden]' ? (this.hidden ? this : null) : selector.includes('[data-row-checkbox]') ? this : null;},
    }));
    const all = element();
    const selectButton = element();
    const count = element();
    const form = {id: 'selected-mice-form', action: editPath,
        querySelector: () => ({value: returnUrl}),
    };
    const card = {
        closest: () => form,
        querySelector(selector) {
            return {'[data-select-all]': all, '[data-table-select-all-button]': selectButton,
                '[data-selection-count]': count}[selector];
        },
        querySelectorAll: () => rows,
    };
    const events = {};
    const clicks = [];
    runInNewContext(source, {URL, Event,
        document: {
            documentElement: {style: {setProperty() {}}},
            addEventListener(name, fn) { if (name === "DOMContentLoaded") fn(); else if (name === "click") clicks.push(fn); },
            querySelectorAll: selector => selector === '.table-card' ? [card] : [],
            querySelector: selector => selector === '[data-animal-action="selected-mice-form"]' && animalTable ? action : null,
        },
        window: {location: {href: 'http://localhost/mice/'},
            addEventListener(name, fn) { events[name] = fn; },
        },
    });
    return {action, createUrl, editPath, returnUrl, rows, all, selectButton, count, events,
        click(index, shiftKey = false) {
            this.clickRow(rows[index], shiftKey);
        },
        clickRow(row, shiftKey = false) {
            row.checked = !row.checked;
            clicks.forEach(fn => fn({target: row, shiftKey}));
            row.emit('change');
        },
        selectAll() {
            selectButton.closest = selector => selector === '.table-card' ? card : selector.includes('[data-table-select-all-button]') && !selector.includes('[data-row-checkbox]') ? selectButton : null;
            selectButton.emit('click');
            clicks.forEach(fn => fn({target: selectButton}));
        },
    };
}

test('no selection and empty lists offer creation', () => {
    for (const empty of [false, true]) {
        const ui = fixture({empty});
        assert.equal(ui.action.href, ui.createUrl);
        if (!empty) assert.equal(ui.action.textContent, 'Add animals');
    }
});

test('selection opens viewing from procedures and editing from Animals, preserving ID and list context', () => {
    for (const scoped of [false, true]) {
        const ui = fixture({scoped});
        ui.rows[0].checked = true;
        ui.rows[0].emit('change');
        assert.equal(ui.action.textContent, scoped ? 'View selected' : 'Edit selected');
        const url = new URL(ui.action.href);
        assert.equal(url.pathname, ui.editPath);
        assert.deepEqual(url.searchParams.getAll('selected_mouse'), [ui.rows[0].value]);
        assert.equal(url.searchParams.get('return_url'), ui.returnUrl);
        assert.deepEqual([...url.searchParams.keys()], ['selected_mouse', 'return_url']);
    }
});

test('the header checkbox switches between editing all selected animals and creation', () => {
    const ui = fixture();
    ui.all.checked = true;
    ui.all.emit('change');
    assert.deepEqual(new URL(ui.action.href).searchParams.getAll('selected_mouse'), ui.rows.map(row => row.value));
    assert.equal(ui.action.textContent, 'Edit selected');
    ui.all.checked = false;
    ui.all.emit('change');
    assert.equal(ui.action.href, ui.createUrl);
    assert.equal(ui.action.textContent, 'Add animals');
});

test('Select all and Clear selection keep the unified action in sync', () => {
    const ui = fixture();
    ui.selectButton.emit('click');
    assert.equal(ui.action.textContent, 'Edit selected');
    ui.selectButton.emit('click');
    assert.equal(ui.action.href, ui.createUrl);
    assert.equal(ui.action.textContent, 'Add animals');
});

test('deselecting one of several mice edits only the remaining selection', () => {
    const ui = fixture();
    ui.selectButton.emit('click');
    ui.rows[0].checked = false;
    ui.rows[0].emit('change');
    assert.deepEqual(new URL(ui.action.href).searchParams.getAll('selected_mouse'), [ui.rows[1].value]);
    assert.equal(ui.all.indeterminate, true);
});

test('browser-restored selections are reflected on load and pageshow', () => {
    const ui = fixture({restored: true});
    assert.equal(ui.action.textContent, 'Edit selected');
    ui.rows[0].checked = false;
    ui.events.pageshow();
    assert.equal(ui.action.textContent, 'Add animals');
    assert.equal(ui.action.href, ui.createUrl);
});

test('other selectable tables keep their existing selection controls', () => {
    const ui = fixture({animalTable: false});
    ui.selectButton.emit('click');
    assert.equal(ui.count.textContent, '2 selected');
    assert.equal(ui.all.checked, true);
    assert.equal(ui.action.href, ui.createUrl);
});

test('procedure selections consistently offer viewing after select-all and browser restoration', () => {
    const ui = fixture({scoped: true, restored: true});
    assert.equal(ui.action.textContent, 'View selected');
    ui.selectButton.emit('click');
    assert.equal(ui.action.textContent, 'View selected');
    assert.deepEqual(new URL(ui.action.href).searchParams.getAll('selected_mouse'), ui.rows.map(row => row.value));
    ui.selectButton.emit('click');
    assert.equal(ui.action.textContent, 'Add animals');
    assert.equal(ui.action.href, ui.createUrl);
});

test('Shift-click selects the entire displayed range and updates counts and animal links', () => {
    const ui = fixture({rowCount: 5});
    ui.click(0); ui.click(4, true);
    assert.deepEqual(ui.rows.map(row => row.checked), [true, true, true, true, true]);
    assert.equal(ui.count.textContent, '5 selected');
    assert.equal(ui.all.checked, true);
    assert.deepEqual(new URL(ui.action.href).searchParams.getAll('selected_mouse'), ui.rows.map(row => row.value));
});

test('reverse ranges preserve selections outside the range', () => {
    const ui = fixture({rowCount: 6});
    ui.click(0); ui.click(4); ui.click(2, true);
    assert.deepEqual(ui.rows.map(row => row.checked), [true, false, true, true, true, false]);
    assert.equal(ui.count.textContent, '4 selected');
    assert.equal(ui.all.indeterminate, true);
});

test('Shift-click also clears a range and normal clicks only toggle one row', () => {
    const ui = fixture({rowCount: 5});
    ui.click(0); ui.click(4, true);
    ui.click(1); ui.click(4, true);
    assert.deepEqual(ui.rows.map(row => row.checked), [true, false, false, false, false]);
    assert.equal(ui.count.textContent, '1 selected');
});

test('disabled and hidden rows are skipped when selecting a range', () => {
    const ui = fixture({rowCount: 5});
    ui.rows[1].disabled = true;
    ui.rows[2].hidden = true;
    ui.click(0); ui.click(4, true);
    assert.deepEqual(ui.rows.map(row => row.checked), [true, false, false, true, true]);
});

test('Shift-click without an anchor acts as a normal click and Select all resets the anchor', () => {
    const ui = fixture({rowCount: 5});
    ui.click(3, true);
    assert.deepEqual(ui.rows.map(row => row.checked), [false, false, false, true, false]);
    ui.selectAll();
    ui.click(0, true);
    assert.deepEqual(ui.rows.map(row => row.checked), [false, true, true, true, true]);
});

test('ranges work for non-animal tables without changing unrelated actions', () => {
    const ui = fixture({rowCount: 4, animalTable: false});
    ui.click(0); ui.click(3, true);
    assert.equal(ui.count.textContent, '4 selected');
    assert.equal(ui.action.href, ui.createUrl);
});

test('a range anchor never carries into another table', () => {
    const ui = fixture({rowCount: 5});
    ui.click(0);
    const otherRows = [];
    const otherCard = {querySelectorAll: () => otherRows};
    for (let i = 0; i < 4; i++) otherRows.push(element({
        closest(selector) {return selector === '.table-card' ? otherCard : selector === '[hidden]' ? null : selector.includes('[data-row-checkbox]') ? this : null;},
    }));
    ui.clickRow(otherRows[3], true);
    assert.deepEqual(otherRows.map(row => row.checked), [false, false, false, true]);
    ui.clickRow(otherRows[1], true);
    assert.deepEqual(otherRows.map(row => row.checked), [false, true, true, true]);
    assert.deepEqual(ui.rows.map(row => row.checked), [true, false, false, false, false]);
});

test('replacing table rows discards the old anchor', () => {
    const ui = fixture({rowCount: 5});
    ui.click(0);
    const replacements = ui.rows.map(row => ({...row, checked: false}));
    ui.rows.splice(0, ui.rows.length, ...replacements);
    ui.click(4, true);
    assert.deepEqual(ui.rows.map(row => row.checked), [false, false, false, false, true]);
});
