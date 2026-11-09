// Enhance text cells without truncating their contents or changing exports.
document.addEventListener("DOMContentLoaded", function () {
    const selector = ".data-table td.notes-cell, .data-table td.path-cell";
    let nextId = 0;

    function enhance(cell) {
        if (cell.dataset.compactTextReady) return;
        cell.dataset.compactTextReady = "true";

        const content = document.createElement("div");
        content.className = "table-cell-text";
        content.id = "table-cell-text-" + (++nextId);
        while (cell.firstChild) content.appendChild(cell.firstChild);

        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "table-cell-text-toggle";
        toggle.textContent = "Show more";
        toggle.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-controls", content.id);
        cell.append(content, toggle);

        function update() {
            if (toggle.getAttribute("aria-expanded") === "true") return;
            toggle.hidden = content.scrollHeight <= content.clientHeight + 1;
        }

        toggle.addEventListener("click", function () {
            const expanded = toggle.getAttribute("aria-expanded") !== "true";
            content.classList.toggle("is-expanded", expanded);
            toggle.setAttribute("aria-expanded", String(expanded));
            toggle.textContent = expanded ? "Show less" : "Show more";
            if (!expanded) update();
        });

        if (typeof ResizeObserver !== "undefined") {
            const observer = new ResizeObserver(update);
            observer.observe(content);
        } else {
            window.addEventListener("resize", update);
        }
        update();
    }

    function scan(root) {
        if (root.matches?.(selector)) enhance(root);
        root.querySelectorAll?.(selector).forEach(enhance);
    }

    scan(document);
    // Family/detail panels can insert new tables after the first page load.
    const observer = new MutationObserver(function (mutations) {
        mutations.forEach(mutation => mutation.addedNodes.forEach(scan));
    });
    observer.observe(document.body, {childList: true, subtree: true});
});
