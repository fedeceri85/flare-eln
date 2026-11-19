document.addEventListener('DOMContentLoaded', function () {
    const section = document.querySelector('[data-new-animal-recording]');
    if (!section) return;

    const options = JSON.parse(document.getElementById('animal-recording-destinations').textContent);
    const projects = JSON.parse(document.getElementById('animal-recording-projects').textContent);
    const type = section.querySelector('[name="recording-recording_type"]');
    const project = section.querySelector('[name="recording-project"]');
    const experiment = section.querySelector('[name="recording-experiment"]');
    const destination = section.querySelector('[data-recording-destination]');
    const experimentField = section.querySelector('[data-recording-experiment]');
    const guidance = section.querySelector('[data-recording-experiment-guidance]');
    const createExperiment = section.querySelector('[data-add-compatible-experiment]');
    const button = section.querySelector('[data-recording-continue]');
    const status = section.querySelector('[data-recording-destination-status]');
    let searchableIndex = 0;

    function choicesFor(select) {
        const choices = Array.from(select.options)
            .filter(option => option.value)
            .map(option => ({value: option.value, label: option.textContent || option.text}));
        const counts = new Map();
        choices.forEach(choice => {
            const key = choice.label.trim().toLocaleLowerCase();
            counts.set(key, (counts.get(key) || 0) + 1);
        });
        choices.forEach(choice => {
            const duplicate = counts.get(choice.label.trim().toLocaleLowerCase()) > 1;
            choice.display = duplicate ? `${choice.label} (#${choice.value})` : choice.label;
        });
        return choices;
    }

    function enhanceSearchable(select, placeholder, onSelection) {
        const input = document.createElement('input');
        const results = document.createElement('div');
        const field = select.closest('.form-field');
        const label = field && field.querySelector('label');
        const suffix = ++searchableIndex;
        input.type = 'text';
        input.id = `${select.id || select.name}-search-${suffix}`;
        input.setAttribute('autocomplete', 'off');
        input.setAttribute('placeholder', placeholder);
        input.setAttribute('data-recording-search', '');
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', `${input.id}-options`);
        results.id = `${input.id}-options`;
        results.className = 'recording-search-results';
        results.setAttribute('role', 'listbox');
        results.hidden = true;
        select.hidden = true;
        select.tabIndex = -1;
        select.setAttribute('aria-hidden', 'true');
        select.parentNode.insertBefore(input, select);
        select.parentNode.insertBefore(results, select);
        if (label) label.htmlFor = input.id;

        function closeSuggestions() {
            results.hidden = true;
            input.setAttribute('aria-expanded', 'false');
        }

        function choose(choice) {
            select.value = choice.value;
            input.value = choice.display;
            closeSuggestions();
            onSelection();
        }

        function renderSuggestions(query = '') {
            const normalized = query.trim().toLocaleLowerCase();
            const matches = (select.searchChoices || []).filter(choice =>
                !normalized
                || choice.display.toLocaleLowerCase().includes(normalized)
                || choice.label.toLocaleLowerCase().includes(normalized)
            );
            results.replaceChildren(...matches.map(choice => {
                const option = document.createElement('button');
                option.type = 'button';
                option.className = 'recording-search-option';
                option.textContent = choice.display;
                option.setAttribute('role', 'option');
                option.addEventListener('mousedown', event => event.preventDefault());
                option.addEventListener('click', () => choose(choice));
                option.addEventListener('keydown', event => {
                    const choices = Array.from(results.children);
                    const index = choices.indexOf(option);
                    if (event.key === 'ArrowDown') {
                        event.preventDefault();
                        (choices[index + 1] || choices[0]).focus();
                    } else if (event.key === 'ArrowUp') {
                        event.preventDefault();
                        (choices[index - 1] || choices[choices.length - 1]).focus();
                    } else if (event.key === 'Escape') {
                        event.preventDefault();
                        closeSuggestions();
                        input.focus();
                    }
                });
                return option;
            }));
            results.hidden = matches.length === 0;
            input.setAttribute('aria-expanded', matches.length ? 'true' : 'false');
        }

        select.refreshSearch = function () {
            const choices = choicesFor(select);
            const selected = choices.find(choice => choice.value === select.value);
            input.value = selected ? selected.display : '';
            select.searchChoices = choices;
            closeSuggestions();
        };

        input.addEventListener('input', function () {
            const query = input.value.trim().toLocaleLowerCase();
            const matches = (select.searchChoices || []).filter(choice =>
                choice.display.trim().toLocaleLowerCase() === query
                || choice.label.trim().toLocaleLowerCase() === query
            );
            select.value = matches.length === 1 ? matches[0].value : '';
            onSelection();
            renderSuggestions(input.value);
        });
        input.addEventListener('focus', () => renderSuggestions(input.value));
        input.addEventListener('blur', () => closeSuggestions());
        input.addEventListener('keydown', event => {
            if (event.key === 'ArrowDown') {
                const first = results.children[0];
                if (first) {
                    event.preventDefault();
                    first.focus();
                }
            } else if (event.key === 'Escape') {
                closeSuggestions();
            }
        });
        select.refreshSearch();
    }

    function populate(select, choices, placeholder) {
        const selected = select.value;
        select.replaceChildren(new Option(placeholder, ''));
        choices.forEach(([value, label]) => select.add(new Option(label, value)));
        select.value = choices.some(([value]) => value === selected) ? selected : '';
        select.disabled = choices.length === 0;
        if (select.refreshSearch) select.refreshSearch();
    }

    function clearSelection(select) {
        select.value = '';
        if (select.refreshSearch) select.refreshSearch();
    }

    function updateCreationLink() {
        const selectedProject = projects.find(option => option.project === project.value);
        const visible = Boolean(type.value && selectedProject);
        guidance.hidden = !visible;
        if (!visible) {
            createExperiment.removeAttribute('href');
            status.textContent = '';
            return;
        }

        const compatible = options.filter(option =>
            option.recording_type === type.value && option.project === project.value
        );
        status.textContent = compatible.length
            ? ''
            : 'No experiment of this type exists in this project';
        status.hidden = compatible.length !== 0;

        const resumeUrl = new URL(window.location.href);
        resumeUrl.searchParams.set('mouse_editor_tab', 'services');
        resumeUrl.searchParams.set('recording_recording_type', type.value);
        resumeUrl.searchParams.set('recording_project', project.value);
        resumeUrl.searchParams.delete('recording_experiment');
        const addUrl = new URL(selectedProject.add_experiment_url, window.location.href);
        addUrl.searchParams.set('recording_type', type.value);
        addUrl.searchParams.set('resume_recording', '1');
        addUrl.searchParams.set('return_url', resumeUrl.toString());
        createExperiment.href = addUrl.toString();
    }

    function updateExperiments() {
        const compatible = options.filter(option =>
            option.recording_type === type.value && option.project === project.value
        );
        populate(
            experiment,
            compatible.map(option => [option.experiment, option.experiment_name]),
            'Select an experiment'
        );
        experimentField.hidden = !project.value;
        button.disabled = !experiment.value;
        updateCreationLink();
    }

    function updateProjects() {
        populate(
            project,
            projects.map(option => [option.project, option.project_name]),
            'Select a project'
        );
        destination.hidden = !type.value;
        updateExperiments();
    }

    enhanceSearchable(type, 'Type to search recording types', function () {
        clearSelection(project);
        clearSelection(experiment);
        updateProjects();
    });
    enhanceSearchable(project, 'Type to search projects', function () {
        clearSelection(experiment);
        updateExperiments();
    });
    enhanceSearchable(experiment, 'Type to search experiments', function () {
        button.disabled = !experiment.value;
    });

    window.addEventListener('pageshow', updateProjects);
    updateProjects();
});
