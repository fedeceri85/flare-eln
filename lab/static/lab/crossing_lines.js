document.addEventListener("DOMContentLoaded", function () {
        const crossingForm = document.querySelector(
            'form input[name="action"][value="create_crossing"]'
        ).closest("form");
        const speciesSelect = crossingForm.querySelector('[name="species"]');
        const lineInput = document.querySelector("[data-line-lookup-input]");
        const lineResults = document.querySelector("[data-line-lookup-results]");
        const selectedInput = crossingForm.querySelector('[name="selected_lines"]');
        const selectedList = document.querySelector("[data-selected-lines]");
        const lookupUrl = lineInput.dataset.lookupUrl;
        const selectedLines = new Map();
        let lookupTimeout;

        function syncSelectedLines() {
            selectedInput.value = Array.from(selectedLines.keys()).join(",");
            selectedList.innerHTML = "";

            selectedLines.forEach(function (line) {
                const item = document.createElement("li");
                item.className = "selected-list-item";
                item.textContent = line.name + " ";

                const removeButton = document.createElement("button");
                removeButton.type = "button";
                removeButton.className = "button";
                removeButton.textContent = "Remove";
                removeButton.addEventListener("click", function () {
                    selectedLines.delete(String(line.id));
                    syncSelectedLines();
                });

                item.appendChild(removeButton);
                selectedList.appendChild(item);
            });
        }

        function showResults(results) {
            lineResults.innerHTML = "";

            results.forEach(function (line) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "button";
                button.textContent = line.label;
                button.addEventListener("click", function () {
                    selectedLines.set(String(line.id), line);
                    lineInput.value = "";
                    lineResults.innerHTML = "";
                    syncSelectedLines();
                });
                lineResults.appendChild(button);
            });
        }

        lineInput.addEventListener("input", function () {
            clearTimeout(lookupTimeout);
            const query = lineInput.value.trim();

            if (!query) {
                lineResults.innerHTML = "";
                return;
            }

            lookupTimeout = setTimeout(async function () {
                const params = new URLSearchParams({
                    species: speciesSelect.value,
                    q: query,
                });
                const response = await fetch(lookupUrl + "?" + params.toString());

                if (!response.ok) {
                    lineResults.innerHTML = "";
                    return;
                }

                const data = await response.json();
                showResults(data.results || []);
            }, 200);
        });

        speciesSelect.addEventListener("change", function () {
            selectedLines.clear();
            syncSelectedLines();
        });

        syncSelectedLines();
    });
