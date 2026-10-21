document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".table-card").forEach(function (tableCard) {
        const selectAll = tableCard.querySelector("[data-select-all]");
        const selectAllButton = tableCard.querySelector(
            "[data-table-select-all-button]"
        );
        const checkboxes = Array.from(
            tableCard.querySelectorAll("[data-row-checkbox]")
        );

        if (checkboxes.length === 0) {
            return;
        }

        function checkedCount() {
            return checkboxes.filter(function (checkbox) {
                return checkbox.checked;
            }).length;
        }

        function updateSelectAllState() {
            const count = checkedCount();
            const allSelected = count === checkboxes.length;

            if (selectAll) {
                selectAll.checked = allSelected;
                selectAll.indeterminate =
                    count > 0 && count < checkboxes.length;
            }

            if (selectAllButton) {
                selectAllButton.textContent = allSelected
                    ? "Clear selection"
                    : "Select all";
            }
            const selectionCount = tableCard.querySelector("[data-selection-count]");
            if (selectionCount) {
                selectionCount.textContent = count ? count + " selected" : "No rows selected";
            }
        }

        function setAllRows(checked) {
            checkboxes.forEach(function (checkbox) {
                checkbox.checked = checked;
            });

            updateSelectAllState();
        }

        if (selectAll) {
            selectAll.addEventListener("change", function () {
                setAllRows(selectAll.checked);
            });
        }

        if (selectAllButton) {
            selectAllButton.addEventListener("click", function () {
                setAllRows(checkedCount() !== checkboxes.length);
            });
        }

        checkboxes.forEach(function (checkbox) {
            checkbox.addEventListener("change", updateSelectAllState);
        });

        updateSelectAllState();
    });
});

document.addEventListener("DOMContentLoaded", function () {
    const header = document.querySelector("[data-app-header]");
    const actions = document.querySelector("[data-page-actions]");
    const tabs = document.querySelector("[data-mouse-tab-list]");
    function measureChrome() {
        if (header) document.documentElement.style.setProperty("--header-height", header.offsetHeight + "px");
        document.documentElement.style.setProperty("--action-height", (actions ? actions.offsetHeight : 0) + "px");
        document.documentElement.style.setProperty("--tabs-height", (tabs ? tabs.offsetHeight : 0) + "px");
    }
    measureChrome();
    if (window.ResizeObserver) {
        const observer = new ResizeObserver(measureChrome);
        if (header) observer.observe(header);
        if (actions) observer.observe(actions);
        if (tabs) observer.observe(tabs);
    } else {
        window.addEventListener("resize", measureChrome);
    }
    const nav = document.querySelector(".primary-nav");
    const active = nav && nav.querySelector("[aria-current]");
    if (active) {
        const rightEdge = active.getBoundingClientRect().right;
        const navEdge = nav.getBoundingClientRect().right;
        if (rightEdge > navEdge) nav.scrollLeft += rightEdge - navEdge;
    }
    document.querySelectorAll(".table-scroll").forEach(function (region) {
        region.tabIndex = 0;
        region.setAttribute("role", "region");
        region.setAttribute("aria-label", "Scrollable data table");
    });
    const summary = document.querySelector("[data-error-summary]");
    document.querySelectorAll("[data-error-summary] a[href^='#']").forEach(function (link) {
        const field = document.getElementById(link.getAttribute("href").slice(1));
        if (field) field.setAttribute("aria-invalid", "true");
    });
    document.querySelectorAll(".form-field, .bulk-input").forEach(function (group) {
        const errors = group.querySelector(".error-list");
        const field = group.querySelector("input, select, textarea");
        if (errors && field && field.id) {
            errors.id = field.id + "-errors";
            const describedBy = field.getAttribute("aria-describedby") || "";
            field.setAttribute("aria-describedby", (describedBy + " " + errors.id).trim());
            field.setAttribute("aria-invalid", "true");
        }
    });
    if (summary) summary.focus();
    document.querySelectorAll("[data-error-summary] a[href^='#']").forEach(function (link) {
        link.addEventListener("click", function (event) {
            const field = document.getElementById(link.getAttribute("href").slice(1));
            if (field) {
                event.preventDefault();
                field.focus();
                field.scrollIntoView({ block: "center" });
            }
        });
    });
});
