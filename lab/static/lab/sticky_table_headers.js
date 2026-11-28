// Floating column headers for wide tables (marked with data-sticky-header).
//
// A table's column titles live inside its horizontal scroll wrapper
// (.table-scroll / .litter-rows-scroll), which makes position: sticky
// relative to that wrapper — so the titles cannot stick to the page
// viewport in pure CSS while horizontal scrolling is preserved.
//
// Instead, for each marked table this module renders a clone of its
// <thead> as a position: fixed strip that:
//   - appears exactly when the real header has scrolled past the page's
//     sticky bar stack — the stack is discovered by computed style, so the
//     strip always docks just below the bars that are actually there and
//     never overlaps them,
//   - sits at the left edge of the table's horizontal scroller and always
//     follows its scrollLeft; the strip itself shows no scrollbar, so the
//     table's own scrollbar is the only one,
//   - disappears again once the table body has scrolled past it,
//   - is column-aligned with the body by copying the table width and
//     each column's rendered width (table-layout: fixed), re-measured
//     on resize and whenever the table changes size.
document.addEventListener('DOMContentLoaded', function () {
    const sources = Array.from(document.querySelectorAll('table[data-sticky-header]'));
    if (!sources.length) {
        return;
    }

    // Bars that stay put while the page scrolls (app header, action bar, …).
    // Discovered once by computed style so the strip never overlaps them,
    // whatever the page's exact bar layout is.
    const stickyBars = Array.from(document.querySelectorAll('*')).filter(function (bar) {
        return window.getComputedStyle(bar).position === 'sticky';
    });

    // The app applies zoom: 90% to :root. getBoundingClientRect() reports the
    // un-zoomed viewport, but position: fixed is placed in zoomed CSS px, so
    // any measured left/top/width must be divided by this ratio — otherwise the
    // strip drifts left by (1 - zoom) * distance from the scroller.
    function zoomRatio() {
        const z = parseFloat(window.getComputedStyle(document.documentElement).zoom);
        return Number.isFinite(z) && z > 0 ? z : 1;
    }

    let rafPending = false;

    const headers = sources.map(function (table) {
        // Sticky bars that sit above this table in the page. Document order
        // (not live geometry) decides "above", so the answer stays correct
        // while the page scrolls.
        const bars = stickyBars.filter(function (bar) {
            if (bar.closest('table') || bar.contains(table) || table.contains(bar)) {
                return false;
            }

            return bar.compareDocumentPosition(table) & 4; // 4 = table follows bar
        });

        const scroller =
            table.closest('.table-scroll, .litter-rows-scroll') || table.parentElement;
        const thead = table.querySelector('thead');

        const float = document.createElement('div');
        float.className = 'sticky-header-float';
        float.setAttribute('aria-hidden', 'true');

        const floatScroll = document.createElement('div');
        floatScroll.className = 'sticky-header-float-scroll';

        const clone = table.cloneNode(false);
        clone.removeAttribute('data-sticky-header');
        // Deliberately no <colgroup> copy: the source table is auto-layout,
        // where <col> widths are only minimums and content decides the real
        // column sizes. Under the clone's table-layout: fixed, <col> widths
        // would instead take precedence over the measured cell widths below
        // and the titles would drift away from their body columns.
        const cloneThead = thead.cloneNode(true);
        // The sticky bar is a clone of the header row. Its "select all"
        // checkbox would be a second copy of the same control (same
        // aria-label) and is misleading, because the bar shows only the
        // header — not the rows it would select. Drop it from the clone; the
        // real checkbox stays in the table body. The empty <th> is kept so
        // the column stays aligned with the body.
        const clonedSelectAll = cloneThead.querySelector('[data-select-all]');
        if (clonedSelectAll) {
            clonedSelectAll.remove();
        }
        clone.appendChild(cloneThead);
        floatScroll.appendChild(clone);
        float.appendChild(floatScroll);
        document.body.appendChild(float);

        const cloneCells = Array.from(clone.querySelectorAll('th'));

        function measure() {
            // The clone's columns must equal the source table's rendered
            // column widths; sizing the clone to their exact sum keeps
            // table-layout: fixed from stretching or shrinking them.
            let total = 0;
            const sourceCells = Array.from(thead.querySelectorAll('th'));

            sourceCells.forEach(function (cell, index) {
                total += cell.offsetWidth;
                if (cloneCells[index]) {
                    cloneCells[index].style.width = cell.offsetWidth + 'px';
                }
            });

            clone.style.width = total + 'px';
        }

        // Bottom edge of the sticky bars above the table — the line the
        // strip docks to.
        function pinLine() {
            let line = 0;

            for (const bar of bars) {
                const rect = bar.getBoundingClientRect();

                if (rect.width) {
                    line = Math.max(line, rect.bottom);
                }
            }

            return line;
        }

        function update() {
            const headerRect = thead.getBoundingClientRect();

            // Hidden tab panels report zero height — leave the float hidden.
            if (!headerRect.height) {
                float.classList.remove('is-visible');
                return;
            }

            const line = pinLine();
            const tableRect = table.getBoundingClientRect();

            if (headerRect.top >= line || tableRect.bottom <= line) {
                float.classList.remove('is-visible');
                return;
            }

            const anchor = scroller.getBoundingClientRect();
            const zoom = zoomRatio();

            float.style.top = (line / zoom) + 'px';
            float.style.left = (anchor.left / zoom) + 'px';
            float.style.width = (anchor.width / zoom) + 'px';
            floatScroll.scrollLeft = scroller.scrollLeft;
            float.classList.add('is-visible');
        }

        scroller.addEventListener(
            'scroll',
            function () {
                if (float.classList.contains('is-visible')) {
                    floatScroll.scrollLeft = scroller.scrollLeft;
                }
            },
            { passive: true },
        );
        floatScroll.addEventListener(
            'scroll',
            function () {
                scroller.scrollLeft = floatScroll.scrollLeft;
            },
            { passive: true },
        );

        if (typeof ResizeObserver !== 'undefined') {
            const observer = new ResizeObserver(function () {
                measure();
                update();
            });

            observer.observe(table);
        }

        measure();
        update();

        return { measure, update };
    });

    function refresh() {
        headers.forEach(function (header) {
            header.update();
        });
    }

    function requestRefresh() {
        if (rafPending) {
            return;
        }

        rafPending = true;
        requestAnimationFrame(function () {
            rafPending = false;
            refresh();
        });
    }

    window.addEventListener('scroll', requestRefresh, { passive: true });
    window.addEventListener('resize', function () {
        headers.forEach(function (header) {
            header.measure();
        });
        requestRefresh();
    }, { passive: true });
});
