const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

const source = readFileSync(join(__dirname, '../../static/lab/sticky_table_headers.js'), 'utf8');

class ClassList {
  constructor(el) { this.el = el; }
  add(name) { this.el.className += ' ' + name; }
  remove(name) { this.el.className = this.el.className.split(' ').filter(c => c && c !== name).join(' '); }
  contains(name) { return this.el.className.split(' ').includes(name); }
}

class Element {
  constructor(props = {}) {
    Object.assign(this, {
      className: '', attrs: {}, style: {}, listeners: {}, parentElement: null,
      children: [], scrollLeft: 0, offsetWidth: 0,
    }, props);
    this.classList = new ClassList(this);
  }
  appendChild(node) { this.children.push(node); node.parentElement = this; return node; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  emit(name, event = {}) { (this.listeners[name] || []).forEach(fn => fn({ target: this, preventDefault() {}, ...event })); }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  removeAttribute(key) { delete this.attrs[key]; }
  remove() {
    if (this.parentElement) {
      const list = this.parentElement.children;
      const i = list.indexOf(this);
      if (i !== -1) list.splice(i, 1);
      this.parentElement = null;
    }
  }
  getBoundingClientRect() {
    const rect = this.rect;
    if (!rect) return { top: 0, bottom: 0, left: 0, width: 0 };
    return typeof rect === 'function' ? rect() : rect;
  }
  cloneNode() { throw new Error('cloneNode not stubbed'); }
  querySelector(selector) { throw new Error('querySelector not stubbed for ' + selector); }
  querySelectorAll() { throw new Error('querySelectorAll not stubbed'); }
  closest() { throw new Error('closest not stubbed'); }
}

function fixture(opts = {}) {
  // CSS zoom applied to the document root (the app uses zoom: 90%). When > 1
  // the fixed strip must divide its measured left/top/width by this ratio.
  const zoom = opts.zoom ?? 1;
  const state = {
    theadRect: { top: 400, bottom: 430, height: 30 },
    tableRect: { top: 400, bottom: 1200 },
    scrollerRect: { left: 16, width: 768 },
    scrollLeft: 0,
  };

  const appHeader = new Element({
    rect: { top: 0, bottom: 56, width: 800 }, closest: () => null,
    contains: () => false, compareDocumentPosition: () => 4,
  });
  const actionBar = new Element({
    rect: { top: 56, bottom: 130, width: 800 }, closest: () => null,
    contains: () => false, compareDocumentPosition: () => 4,
  });
  // A sticky bar below the table and a sticky cell inside a table must be ignored.
  const barBelow = new Element({
    rect: { top: 900, bottom: 940, width: 800 }, closest: () => null,
    contains: () => false, compareDocumentPosition: () => 2,
  });
  const pinnedCell = new Element({
    rect: { top: 410, bottom: 440, width: 100 }, closest: () => 'table',
    contains: () => false, compareDocumentPosition: () => 2,
  });
  const body = new Element();

  const sourceThs = [120, 200, 340].map(width => new Element({ offsetWidth: width }));
  const thead = new Element({ rect: () => state.theadRect, children: sourceThs });
  thead.querySelectorAll = selector => selector === 'th' ? sourceThs : [];

  const cloneThs = [10, 10, 10].map(() => new Element());
  const cloneThead = new Element({ children: cloneThs });
  // The real thead has a "select all" checkbox in its first cell; the sticky
  // clone must drop it (a duplicate control that is misleading over a view
  // that shows only the header row, not the selectable rows).
  const cloneSelectAll = new Element({ tagName: 'input' });
  cloneSelectAll.attrs['data-select-all'] = '';
  cloneThs[0].appendChild(cloneSelectAll);
  cloneThead.querySelectorAll = selector => selector === 'th' ? cloneThs : [];
  cloneThead.querySelector = selector =>
    selector === '[data-select-all]' ? cloneSelectAll : null;
  thead.cloneNode = deep => { if (!deep) throw new Error('deep thead clone expected'); return cloneThead; };

  const clone = new Element({ className: 'data-table sticky-first procedure-matrix' });
  clone.querySelectorAll = selector => selector === 'th' ? cloneThs : [];

  const table = new Element({ offsetWidth: 600, rect: () => state.tableRect, contains: () => false });
  // A colgroup exists on the source table (e.g. the genotype table's
  // <col> minimum widths) — the float must not copy it, because under
  // table-layout: fixed <col> widths would override the measured cell
  // widths and drift away from the body columns.
  const colgroup = new Element({ tagName: 'colgroup' });
  colgroup.cloneNode = () => new Element({ tagName: 'colgroup' });
  table.querySelector = selector => {
    if (selector === 'thead') return thead;
    if (selector === 'colgroup') return colgroup;
    throw new Error('unexpected table.querySelector(' + selector + ')');
  };
  table.cloneNode = shallow => { if (shallow) throw new Error('shallow table clone expected'); return clone; };

  const scroller = new Element({ rect: () => state.scrollerRect, scrollLeft: 0 });
  table.closest = selector => (selector === '.table-scroll, .litter-rows-scroll' ? scroller : null);

  const created = [];
  const raf = [];
  const documentElement = new Element({ tagName: 'html' });
  const document = {
    addEventListener(name, callback) { if (name === 'DOMContentLoaded') callback(); },
    documentElement,
    querySelectorAll: selector => {
      if (selector === 'table[data-sticky-header]') return [table];
      if (selector === '*') return [appHeader, actionBar, barBelow, pinnedCell];
      return [];
    },
    createElement(tag) { const el = new Element({ tagName: tag }); created.push(el); return el; },
    body,
  };

  let resizeObserverCallback = null;
  runInNewContext(source, {
    document,
    window: {
      addEventListener(name, handler) {
        if (name === 'scroll') state.onWindowScroll = handler;
        if (name === 'resize') state.onWindowResize = handler;
      },
      getComputedStyle: el => {
        if (el === documentElement) {
          return { position: 'static', zoom: zoom === 1 ? '' : String(zoom) };
        }
        return { position: 'sticky' };
      },
    },
    requestAnimationFrame(callback) { raf.push(callback); return raf.length; },
    ResizeObserver: class { constructor(callback) { resizeObserverCallback = callback; } observe() {} },
  });

  const [float, floatScroll] = created;
  return {
    state, float, floatScroll, clone, cloneThs, cloneSelectAll, sourceThs, scroller, raf,
    resizeObserverCallback,
    flush() { while (raf.length) raf.shift()(); },
    scrollTo(theadTop, tableBottom) {
      state.theadRect = { top: theadTop, bottom: theadTop + 30, height: 30 };
      state.tableRect = { top: theadTop, bottom: tableBottom };
      state.onWindowScroll({});
      this.flush();
    },
  };
}

test('float is hidden while the real header is below the sticky bar stack', () => {
  const ui = fixture();
  assert.equal(ui.float.classList.contains('is-visible'), false);
});

test('float docks below the action bar with the scroller geometry while the table extends past it', () => {
  const ui = fixture();
  ui.scrollTo(100, 800);
  assert.equal(ui.float.classList.contains('is-visible'), true);
  assert.equal(ui.float.style.top, '130px');
  assert.equal(ui.float.style.left, '16px');
  assert.equal(ui.float.style.width, '768px');
});

test('zoom on :root is compensated by dividing the fixed coordinates by the root zoom ratio', () => {
  const ui = fixture({ zoom: 0.5 });
  ui.scrollTo(100, 800);
  assert.equal(ui.float.classList.contains('is-visible'), true);
  // Measured (un-zoomed) values: pin line 130, scroller left 16, width 768.
  // position: fixed is placed in zoomed px, so each is divided by the 0.5 zoom.
  assert.equal(ui.float.style.top, '260px');
  assert.equal(ui.float.style.left, '32px');
  assert.equal(ui.float.style.width, '1536px');
});

test('float leaves with the table once the body has scrolled past the pin line', () => {
  const ui = fixture();
  ui.scrollTo(100, 800);
  assert.equal(ui.float.classList.contains('is-visible'), true);
  ui.scrollTo(-200, 90);
  assert.equal(ui.float.classList.contains('is-visible'), false);
});

test('the clone is an aria-hidden fixed copy of the thead with fixed column widths', () => {
  const ui = fixture();
  assert.equal(ui.float.getAttribute('aria-hidden'), 'true');
  assert.equal(ui.clone.getAttribute('data-sticky-header'), null);
  // Widths come from the measured body columns (120 + 200 + 340), not from
  // the table's offsetWidth (600), so fixed layout cannot rescale them.
  assert.equal(ui.clone.style.width, '660px');
  assert.deepEqual(ui.cloneThs.map(cell => cell.style.width), ['120px', '200px', '340px']);
  assert.ok(
    !ui.clone.children.some(child => child.tagName === 'colgroup'),
    'the source colgroup must not be copied into the fixed-layout clone',
  );
});

test('the clone drops the "select all" checkbox so the sticky bar shows only the header', () => {
  const ui = fixture();
  // The checkbox that was in the clone's first cell is detached from the
  // clone (the real one stays in the table body).
  assert.equal(ui.cloneSelectAll.parentElement, null, 'clone checkbox must be detached');
  assert.ok(
    !ui.cloneThs[0].children.includes(ui.cloneSelectAll),
    'the select-all checkbox must not remain in the sticky clone',
  );
  // The cell itself is kept so the column still lines up with the body.
  assert.equal(ui.cloneThs[0].style.width, '120px');
});

test('horizontal scrolling stays in sync in both directions', () => {
  const ui = fixture();
  ui.scrollTo(100, 800);
  ui.scroller.scrollLeft = 120;
  ui.scroller.emit('scroll');
  assert.equal(ui.floatScroll.scrollLeft, 120);
  ui.floatScroll.scrollLeft = 300;
  ui.floatScroll.emit('scroll');
  assert.equal(ui.scroller.scrollLeft, 300);
});

test('body scrolls do not move the float when it is hidden', () => {
  const ui = fixture();
  ui.scroller.scrollLeft = 120;
  ui.scroller.emit('scroll');
  assert.equal(ui.floatScroll.scrollLeft, 0);
});

test('window resize re-measures column widths from the source table', () => {
  const ui = fixture();
  ui.sourceThs[0].offsetWidth = 140;
  ui.sourceThs[2].offsetWidth = 320;
  ui.state.onWindowResize({});
  ui.flush();
  assert.deepEqual(ui.cloneThs.map(cell => cell.style.width), ['140px', '200px', '320px']);
});

test('ResizeObserver changes re-measure and reposition the float', () => {
  const ui = fixture();
  assert.equal(typeof ui.resizeObserverCallback, 'function');
  ui.state.theadRect = { top: 100, bottom: 130, height: 30 };
  ui.state.tableRect = { top: 100, bottom: 800 };
  ui.sourceThs[1].offsetWidth = 250;
  ui.resizeObserverCallback();
  assert.equal(ui.cloneThs[1].style.width, '250px');
  assert.equal(ui.float.style.top, '130px');
});

test('hidden tab panels (zero header height) never show the float', () => {
  const ui = fixture();
  ui.state.theadRect = { top: 0, bottom: 0, height: 0 };
  ui.state.tableRect = { top: 0, bottom: 0 };
  ui.state.onWindowScroll({});
  ui.flush();
  assert.equal(ui.float.classList.contains('is-visible'), false);
});

test('without marked tables the module does nothing', () => {
  const created = [];
  runInNewContext(source, {
    document: {
      addEventListener(name, callback) { if (name === 'DOMContentLoaded') callback(); },
      querySelectorAll: () => [],
      querySelector: () => null,
      createElement(tag) { const el = new Element({ tagName: tag }); created.push(el); return el; },
      body: new Element(),
    },
    window: { addEventListener() {} },
    requestAnimationFrame() { return 0; },
    ResizeObserver: class { constructor() {} observe() {} },
  });
  assert.equal(created.length, 0);
});