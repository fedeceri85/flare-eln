document.addEventListener('DOMContentLoaded', function () {
    const section = document.querySelector('[data-new-animal-recording]');
    if (!section) return;
    const options = JSON.parse(document.getElementById('animal-recording-destinations').textContent);
    const type = section.querySelector('[name="recording-recording_type"]');
    const project = section.querySelector('[name="recording-project"]');
    const experiment = section.querySelector('[name="recording-experiment"]');
    const destination = section.querySelector('[data-recording-destination]');
    const experimentField = section.querySelector('[data-recording-experiment]');
    const button = section.querySelector('[data-recording-continue]');
    const status = section.querySelector('[data-recording-destination-status]');

    function populate(select, choices, placeholder) {
        const selected = select.value;
        select.replaceChildren(new Option(placeholder, ''));
        choices.forEach(([value, label]) => select.add(new Option(label, value)));
        select.value = choices.some(([value]) => value === selected) ? selected : '';
        select.disabled = choices.length === 0;
    }

    function updateExperiments() {
        const compatible = options.filter(option => option.recording_type === type.value && option.project === project.value);
        populate(experiment, compatible.map(option => [option.experiment, option.experiment_name]), 'Select an experiment');
        experimentField.hidden = !project.value;
        button.disabled = !experiment.value;
    }

    function updateProjects() {
        const compatible = options.filter(option => option.recording_type === type.value);
        const projects = [...new Map(compatible.map(option => [option.project, option.project_name]))];
        populate(project, projects, 'Select a project');
        destination.hidden = !type.value;
        status.textContent = type.value && !projects.length
            ? 'No compatible experiments are available for this recording type in active projects.'
            : '';
        updateExperiments();
    }

    type.addEventListener('change', function () {
        project.value = '';
        experiment.value = '';
        updateProjects();
    });
    project.addEventListener('change', function () {
        experiment.value = '';
        updateExperiments();
    });
    experiment.addEventListener('change', function () {
        button.disabled = !experiment.value;
    });
    window.addEventListener('pageshow', updateProjects);
    updateProjects();
});
