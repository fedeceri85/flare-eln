document.addEventListener("DOMContentLoaded", function () {
    const primaryForm = document.getElementById("primary-form");
    const recordingForm = document.getElementById("new-animal-recording-form");
    if (!primaryForm || !recordingForm) return;

    const activeTab = primaryForm.querySelector("[data-mouse-active-tab]");
    const status = document.querySelector("[data-save-continuation-status]");
    let stagedInputs = [];

    function clearStagedInputs() {
        stagedInputs.forEach(input => input.remove());
        stagedInputs = [];
    }

    function stage(name, value) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        input.dataset.saveContinuation = "";
        primaryForm.appendChild(input);
        stagedInputs.push(input);
    }

    function submitPrimary() {
        if (activeTab) activeTab.value = "services";
        if (status) status.textContent = "Saving animal changes before continuing…";

        let submitEvent;
        const observeSubmit = event => {
            submitEvent = event;
            Promise.resolve().then(() => {
                if (event.defaultPrevented) {
                    clearStagedInputs();
                    if (status) status.textContent = "Save the animal changes before continuing.";
                }
            });
        };
        primaryForm.addEventListener("submit", observeSubmit, {once: true});
        primaryForm.requestSubmit();

        // Native validation can stop submission before a submit event is fired.
        if (!submitEvent) {
            primaryForm.removeEventListener("submit", observeSubmit);
            clearStagedInputs();
            if (status) status.textContent = "Correct the highlighted animal details before continuing.";
        }
    }

    recordingForm.addEventListener("submit", function (event) {
        event.preventDefault();
        clearStagedInputs();
        stage("select_recording_destination", "1");
        ["recording-recording_type", "recording-project", "recording-experiment"].forEach(name => {
            const field = recordingForm.elements.namedItem(name);
            stage(name, field ? field.value : "");
        });
        submitPrimary();
    });

    document.addEventListener("click", function (event) {
        const link = event.target.closest("[data-save-before-continue]");
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (!link.href) return;
        event.preventDefault();
        clearStagedInputs();
        stage("save_and_continue_url", link.href);
        submitPrimary();
    });
});
