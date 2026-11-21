const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const source = readFileSync(join(__dirname, '../../static/lab/new_animal_recording.js'), 'utf8');

function element(extra = {}) {
    const events = {};
    const attributes = new Map();
    return Object.assign({value: '', hidden: false, disabled: false, textContent: '', children: [],
        addEventListener(name, fn) { events[name] = fn; },
        emit(name, event = {}) { events[name]?.({preventDefault() {}, ...event}); },
        focus() { this.focused = true; this.emit('focus'); },
        setAttribute(name, value) { attributes.set(name, String(value)); },
        removeAttribute(name) { attributes.delete(name); if (name === 'href') this.href = ''; },
        getAttribute(name) { return attributes.get(name); },
        replaceChildren(...children) { this.children = children; },
    }, extra);
}

function select(value = '', choices = []) {
    const label = element();
    const field = {querySelector: selector => selector === 'label' ? label : null};
    const parent = {children: [], insertBefore(node) { this.children.push(node); }};
    const control = element({
        value,
        id: `select-${Math.random()}`,
        name: 'selection',
        options: [option('Select', ''), ...choices.map(([choiceValue, text]) => option(text, choiceValue))],
        parentNode: parent,
        closest: selector => selector === '.form-field' ? field : null,
        replaceChildren(first) { this.options = [first]; this.value = ''; },
        add(newOption) { this.options.push(newOption); },
    });
    return control;
}

function option(text, value) {
    return {text, textContent: text, value};
}

function searchInput(control) {
    return control.parentNode.children.find(child => child.getAttribute?.('data-recording-search') === '');
}

function searchResults(control) {
    return control.parentNode.children.find(child => child.className === 'recording-search-results');
}

function choose(control, label) {
    const input = searchInput(control);
    input.value = label;
    input.emit('input');
}

function fixture(initial = {}) {
    const type = select(initial.type, [
        ['imaging', 'Imaging'], ['behaviour', 'Behaviour'], ['empty', 'Empty type'],
    ]);
    const project = select(initial.project);
    const experiment = select(initial.experiment);
    const destination = element();
    const experimentField = element();
    const guidance = element();
    const createExperiment = element();
    const button = element();
    const status = element();
    const options = [
        {recording_type: 'imaging', project: 'alpha', project_name: 'Alpha', experiment: '1', experiment_name: 'Imaging 1'},
        {recording_type: 'imaging', project: 'alpha', project_name: 'Alpha', experiment: '2', experiment_name: 'Imaging 2'},
        {recording_type: 'imaging', project: 'beta', project_name: 'Beta', experiment: '3', experiment_name: 'Imaging 3'},
        {recording_type: 'behaviour', project: 'alpha', project_name: 'Alpha', experiment: '4', experiment_name: 'Behaviour'},
    ];
    const projects = [
        {project: 'alpha', project_name: 'Alpha', add_experiment_url: '/projects/alpha/experiments/add/'},
        {project: 'beta', project_name: 'Beta', add_experiment_url: '/projects/beta/experiments/add/'},
        {project: 'gamma', project_name: 'Gamma (no experiments)', add_experiment_url: '/projects/gamma/experiments/add/'},
    ];
    const selectors = {
        '[name="recording-recording_type"]': type,
        '[name="recording-project"]': project,
        '[name="recording-experiment"]': experiment,
        '[data-recording-destination]': destination,
        '[data-recording-experiment]': experimentField,
        '[data-recording-experiment-guidance]': guidance,
        '[data-add-compatible-experiment]': createExperiment,
        '[data-recording-continue]': button,
        '[data-recording-destination-status]': status,
    };
    const events = {};
    runInNewContext(source, {
        Option: option,
        URL,
        document: {
            addEventListener(name, fn) { fn(); },
            createElement: tag => element({tagName: tag.toUpperCase()}),
            querySelector: () => ({querySelector: selector => selectors[selector]}),
            getElementById: id => ({textContent: JSON.stringify(
                id === 'animal-recording-projects' ? projects : options
            )}),
        },
        window: {
            location: {href: 'https://eln.test/animals/edit/?selected_mouse=M1&return_url=%2Fanimals%2F'},
            addEventListener(name, fn) { events[name] = fn; },
        },
    });
    return {type, project, experiment, destination, experimentField, guidance,
        createExperiment, button, status, events};
}

const values = control => control.options.map(item => item.value);

test('all three selectors are searchable and every active project is offered', () => {
    const ui = fixture();
    assert.ok(searchInput(ui.type));
    assert.ok(searchInput(ui.project));
    assert.ok(searchInput(ui.experiment));
    assert.equal(ui.destination.hidden, true);
    searchInput(ui.type).focus();
    assert.equal(searchResults(ui.type).hidden, false);
    assert.deepEqual(searchResults(ui.type).children.map(option => option.textContent), [
        'Imaging', 'Behaviour', 'Empty type',
    ]);
    searchInput(ui.type).value = 'imag';
    searchInput(ui.type).emit('input');
    assert.deepEqual(searchResults(ui.type).children.map(option => option.textContent), ['Imaging']);
    searchResults(ui.type).children[0].emit('click');
    assert.equal(ui.type.value, 'imaging');
    assert.equal(ui.destination.hidden, false);
    assert.deepEqual(values(ui.project), ['', 'alpha', 'beta', 'gamma']);
    choose(ui.project, 'Alpha');
    assert.deepEqual(values(ui.experiment), ['', '1', '2']);
    choose(ui.experiment, 'Imaging 2');
    assert.equal(ui.button.disabled, false);
});

test('changing type or project clears incompatible dependent selections', () => {
    const ui = fixture({type: 'imaging', project: 'alpha', experiment: '1'});
    choose(ui.type, 'Behaviour');
    assert.equal(ui.project.value, '');
    assert.equal(ui.experiment.value, '');
    assert.equal(ui.button.disabled, true);
    choose(ui.project, 'Alpha');
    assert.deepEqual(values(ui.experiment), ['', '4']);
    choose(ui.type, 'Imaging');
    choose(ui.project, 'Beta');
    assert.deepEqual(values(ui.experiment), ['', '3']);
});

test('empty projects show the warning and correctly contextualised creation link', () => {
    const ui = fixture();
    choose(ui.type, 'Imaging');
    choose(ui.project, 'Gamma (no experiments)');
    assert.equal(ui.guidance.hidden, false);
    assert.equal(ui.experiment.disabled, true);
    assert.equal(ui.button.disabled, true);
    assert.equal(ui.status.hidden, false);
    assert.equal(ui.status.textContent, 'No experiment of this type exists in this project');
    const link = new URL(ui.createExperiment.href);
    assert.equal(link.pathname, '/projects/gamma/experiments/add/');
    assert.equal(link.searchParams.get('recording_type'), 'imaging');
    assert.equal(link.searchParams.get('resume_recording'), '1');
    const returnUrl = new URL(link.searchParams.get('return_url'));
    assert.equal(returnUrl.searchParams.get('selected_mouse'), 'M1');
    assert.equal(returnUrl.searchParams.get('recording_recording_type'), 'imaging');
    assert.equal(returnUrl.searchParams.get('recording_project'), 'gamma');
});

test('creation link remains available when compatible experiments exist', () => {
    const ui = fixture();
    choose(ui.type, 'Imaging');
    choose(ui.project, 'Alpha');
    assert.equal(ui.guidance.hidden, false);
    assert.equal(ui.status.hidden, true);
    assert.ok(ui.createExperiment.href);
});

test('server-restored valid selections retain the destination after pageshow', () => {
    const ui = fixture({type: 'imaging', project: 'alpha', experiment: '2'});
    assert.equal(ui.project.value, 'alpha');
    assert.equal(ui.experiment.value, '2');
    assert.equal(ui.button.disabled, false);
    ui.events.pageshow();
    assert.equal(ui.experiment.value, '2');
    assert.equal(ui.experimentField.hidden, false);
});
