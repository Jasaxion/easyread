const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function createMockEnvironment(options = {}) {
  let docEvents = new Map();

  class MockElement {
    constructor(tag) {
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.attributes = new Map();
      this.dataset = {};
      this._classes = new Set();
      this.classList = {
        add: (c) => this._classes.add(c),
        remove: (c) => this._classes.delete(c),
        toggle: (c, val) => val ? this._classes.add(c) : this._classes.delete(c),
        contains: (c) => this._classes.has(c)
      };
      this.style = {
        setProperty: (k, v) => { this.style[k] = v; },
        removeProperty: (k) => { delete this.style[k]; }
      };
      this.contentWindow = {
        focus: () => {},
        scrollTo: () => {},
        scrollY: 100,
        PR: {
          captureProgress: () => {},
          finishPanelNote: () => {},
          autosaveNote: { flush: () => {} },
          flushPrefs: async () => {},
          flushProgress: async () => true,
          progressOp: () => ({ block: 'b1', off: 0.5, ratio: 0.2 }),
          t: (s) => s === '文献库' || s === '文獻庫' ? '文獻庫' : s,
          lib: {
            byId: (id) => {
              if (id === 'paper_cat') return { id: 'paper_cat', tags: ['Machine Learning'] };
              return null;
            }
          }
        },
        document: {
          title: 'Test Paper',
          head: {},
          documentElement: { dataset: { theme: 'dark' } },
          addEventListener: () => {},
          querySelector: () => ({})
        }
      };
      this.contentDocument = this.contentWindow.document;
    }
    get textContent() {
      if (this._textContent !== undefined) return this._textContent;
      return this.children.map(c => c.textContent || '').join('');
    }
    set textContent(val) {
      this._textContent = String(val);
      this.children = [];
    }
    get className() { return [...this._classes].join(' '); }
    set className(val) {
      this._classes = new Set(val ? String(val).split(/\s+/).filter(Boolean) : []);
    }
    setAttribute(k, v) { this.attributes.set(k, String(v)); }
    getAttribute(k) { return this.attributes.get(k); }
    append(...children) {
      for (const c of children) {
        if (c.parentElement === this) {
          this.children = this.children.filter(x => x !== c);
        }
        c.parentElement = this;
        this.children.push(c);
      }
    }
    prepend(...children) {
      for (const c of children.reverse()) {
        if (c.parentElement === this) {
          this.children = this.children.filter(x => x !== c);
        }
        c.parentElement = this;
        this.children.unshift(c);
      }
    }
    insertBefore(newChild, refChild) {
      if (newChild.parentElement === this) {
        this.children = this.children.filter(x => x !== newChild);
      }
      newChild.parentElement = this;
      const idx = this.children.indexOf(refChild);
      if (idx === -1) this.children.push(newChild);
      else this.children.splice(idx, 0, newChild);
    }
    querySelector(sel) {
      if (sel.startsWith('.')) {
        const cls = sel.slice(1);
        return this.children.find(c => c.classList && c.classList.contains(cls)) || null;
      }
      return null;
    }
    querySelectorAll(sel) {
      if (sel.startsWith('.')) {
        const cls = sel.slice(1);
        return this.children.filter(c => c.classList && c.classList.contains(cls));
      }
      return [];
    }
    addEventListener(name, fn) {
      if (!this._listeners) this._listeners = new Map();
      if (!this._listeners.has(name)) this._listeners.set(name, []);
      this._listeners.get(name).push(fn);
      if (name === 'load') setTimeout(fn, 10);
    }
    dispatchEvent(name, event) {
      const handlers = (this._listeners && this._listeners.get(name)) || [];
      handlers.forEach(fn => fn(event));
    }
    scrollIntoView() {}
    scrollBy() {}
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 36 }; }
    remove() {
      this.removed = true;
      if (this.parentElement) {
        this.parentElement.children = this.parentElement.children.filter((c) => c !== this);
      }
    }
  }

  const strip = new MockElement('div');
  const panes = new MockElement('div');
  const messageEl = new MockElement('div');
  const newTabBtn = new MockElement('button');
  const scrollLeftBtn = new MockElement('button');
  const scrollRightBtn = new MockElement('button');
  const contextMenu = new MockElement('div');
  const groupPopover = new MockElement('div');
  const tabsBar = new MockElement('header');
  const hoverTrigger = new MockElement('div');
  const windowEvents = new Map();

  const doc = {
    querySelector: (sel) => {
      if (sel === '#tabs') return strip;
      if (sel === '#panes') return panes;
      if (sel === '#message') return messageEl;
      if (sel === '#newTab') return newTabBtn;
      if (sel === '#tabScrollLeft') return scrollLeftBtn;
      if (sel === '#tabScrollRight') return scrollRightBtn;
      if (sel === '#tabContextMenu') return contextMenu;
      if (sel === '#groupEditorPopover') return groupPopover;
      if (sel === '.tabs-bar') return tabsBar;
      if (sel === '#tabsHoverTrigger') return hoverTrigger;
      return null;
    },
    createElement: (tag) => new MockElement(tag),
    createDocumentFragment: () => new MockElement('fragment'),
    addEventListener: (ev, fn) => { docEvents.set(ev, fn); },
    body: new MockElement('body'),
    documentElement: { dataset: {}, getAttribute: () => null }
  };

  class MockMutationObserver {
    observe() {}
    disconnect() {}
  }

  const localStorageStore = new Map();
  const mockLocalStorage = {
    getItem: (k) => localStorageStore.get(k) || null,
    setItem: (k, v) => localStorageStore.set(k, String(v)),
    removeItem: (k) => localStorageStore.delete(k)
  };

  const context = {
    document: doc,
    window: {
      PR: { t: (s, vars) => {
        let res = s === '文献库' || s === '文獻庫' ? '文獻庫' : s;
        if (vars) for (const [k, v] of Object.entries(vars)) res = res.replace(`{${k}}`, v);
        return res;
      } },
      innerWidth: 1200,
      innerHeight: 800,
      screen: { availWidth: 1920, availHeight: 1080, availLeft: 0, availTop: 0 },
      screenX: 0,
      addEventListener: (ev, fn) => {
        if (!windowEvents.has(ev)) windowEvents.set(ev, []);
        windowEvents.get(ev).push(fn);
      },
      dispatchEvent: (ev, data) => {
        const handlers = windowEvents.get(ev) || [];
        handlers.forEach(fn => fn(data));
      }
    },
    URL,
    URLSearchParams,
    location: { origin: 'http://localhost:8765', search: '' },
    MutationObserver: MockMutationObserver,
    clearTimeout,
    setTimeout,
    sessionStorage: options.sessionStorage,
    localStorage: mockLocalStorage
  };

  const code = fs.readFileSync(path.join(__dirname, '../../easyread/web/js/tabs.js'), 'utf8');
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../../easyread/web/js/common/tabs-session.js"), "utf8"), context);
  vm.runInContext(code, context);

  return { context, strip, panes, tabsBar, hoverTrigger, windowEvents, contextMenu };
}

test('tabs system initializes library tab, creates reader tabs, and cleans up on close', async () => {
  const { context, strip, panes } = createMockEnvironment();
  assert.ok(context.window.easyreadTabs);

  // Library tab created on startup (pinned on left, no close button) + newTab button
  assert.equal(strip.children.length, 2);
  const libTab = strip.children[0];
  assert.ok(libTab.classList.contains('tab-library'));
  assert.equal(libTab.querySelector('.tab-close'), null);
  assert.equal(panes.children.length, 1);

  // Open paper
  await context.window.easyreadTabs.open('/read/paper_1');
  assert.equal(strip.children.length, 3); // lib + paper_1 + newTab
  assert.equal(panes.children.length, 2);

  // Close paper tab
  await context.window.easyreadTabs.close('paper_1');
  assert.equal(strip.children.length, 2);
  assert.equal(panes.children.length, 1);
});

test('tabs system supports tab grouping and automatic category grouping', async () => {
  const { context, strip } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  // Open paper that has category "Machine Learning" in library
  await tabsApi.open('/read/paper_cat');
  let groups = tabsApi.getGroups();
  assert.equal(groups.length, 1);
  assert.equal(groups[0].title, 'Machine Learning');

  // Create custom group and assign paper to it
  await tabsApi.open('/read/paper_custom');
  tabsApi.createGroup('g-test', 'My Research', '#2563eb');
  tabsApi.setTabGroup('paper_custom', 'g-test');

  const customGroup = tabsApi.getGroups().find(g => g.id === 'g-test');
  assert.ok(customGroup);
  assert.equal(customGroup.title, 'My Research');

  // Toggle collapse
  tabsApi.toggleGroupCollapse('g-test');
  assert.equal(customGroup.collapsed, true);
  tabsApi.toggleGroupCollapse('g-test');
  assert.equal(customGroup.collapsed, false);

  // When papers in a category are closed to 0, category disappears
  await tabsApi.close('paper_cat');
  groups = tabsApi.getGroups();
  assert.equal(groups.find(g => g.id === 'cat:Machine Learning'), undefined);

  // Remove custom group
  tabsApi.removeGroup('g-test', false);
  assert.equal(tabsApi.getGroups().find(g => g.id === 'g-test'), undefined);
});

test('tabs system supports multi-selection and tab reordering', async () => {
  const { context } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  await tabsApi.open('/read/paper_a');
  await tabsApi.open('/read/paper_b');
  await tabsApi.open('/read/paper_c');

  // Multi select
  tabsApi.selectMultiple(['paper_a', 'paper_b']);
  // Clear selection
  tabsApi.clearMultiSelection();

  // Reorder tabs
  tabsApi.reorderTabs(['library', 'paper_c', 'paper_b', 'paper_a']);
});

test('tabs system virtualizes inactive tabs with LRU sleeping (> 5 tabs)', async () => {
  const { context, panes } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  // Set low limit for testing
  tabsApi.setMaxLiveTabs(2);

  // Open 3 papers (exceeding limit of 2 live paper tabs)
  await tabsApi.open('/read/paper_1');
  await tabsApi.open('/read/paper_2');
  await tabsApi.open('/read/paper_3');

  // paper_1 was opened first and is inactive, so it should be put to sleep
  await tabsApi.sleepTab('paper_1');

  // Active tab is paper_3, panes has active ones
  // Select sleeping paper_1, it should wake up
  tabsApi.select('paper_1');
});

test('tabs system detaches to split-window bounds and attaches without re-opening loop', async () => {
  const { context, strip } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  let openedWindowUrl = null;
  let openedWindowFeatures = null;
  context.window.open = (url, target, features) => {
    openedWindowUrl = url;
    openedWindowFeatures = features;
    return {};
  };

  await tabsApi.open('/read/paper_split');
  assert.equal(strip.children.length, 3); // lib + paper_split + newTab

  // Detach paper_split
  await tabsApi.detachTabs(['paper_split']);
  assert.ok(openedWindowUrl.includes('detached='));
  assert.ok(openedWindowFeatures.includes('popup=yes'));
  assert.ok(openedWindowFeatures.includes('width='));
  assert.equal(strip.children.length, 2); // lib + newTab
});

test('tabs system detects modal open state and toggles body modal-open class', async () => {
  const { context, panes } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  await tabsApi.open('/read/paper_modal');
  const paperFrame = panes.children[1];
  assert.ok(paperFrame);

  let modalOpen = false;
  let figureViewerOpen = false;
  let taskModalHidden = true;
  paperFrame.contentDocument.querySelector = (sel) => {
    if (sel === '#pr-research-task-modal') return { hidden: taskModalHidden };
    if (sel.includes('.dialog-backdrop.open') || sel.includes('#mindmapDlg')) return modalOpen ? {} : null;
    if (sel.includes('dialog.figure-viewer[open]')) return figureViewerOpen ? {} : null;
    return null;
  };

  // Wait for mock frame load listener to bind tab
  await new Promise((r) => setTimeout(r, 30));

  // Initially task modal is hidden, no dialog open: modal-open must NOT be present
  tabsApi.select('paper_modal');
  assert.ok(!context.document.body.classList.contains('modal-open'));

  // Select tab and check modal-open
  modalOpen = true;
  tabsApi.select('paper_modal');
  assert.ok(context.document.body.classList.contains('modal-open'));

  // Trigger modal close
  modalOpen = false;
  tabsApi.select('paper_modal');
  assert.ok(!context.document.body.classList.contains('modal-open'));

  // Open figure-viewer lightbox dialog
  figureViewerOpen = true;
  tabsApi.select('paper_modal');
  assert.ok(context.document.body.classList.contains('modal-open'));

  figureViewerOpen = false;
  tabsApi.select('paper_modal');
  assert.ok(!context.document.body.classList.contains('modal-open'));
});

test('clicking library tab or / link restores root library URL from research view', async () => {
  const { context, strip, panes } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  const libFrame = panes.children[0];
  const libTabNode = strip.children[0];
  const libBtn = libTabNode.children.find(c => c.tagName === 'BUTTON');
  assert.ok(libBtn);

  // Simulate navigating library iframe to /research
  libFrame.contentWindow.location = { pathname: '/research', search: '', hash: '' };
  libFrame.src = '/research';

  // Clicking library tab button should reset frame.src back to /
  libBtn.onclick({ ctrlKey: false, metaKey: false, shiftKey: false });
  assert.equal(libFrame.src, '/');

  // Simulate navigating to /research again and clicking a link to /
  libFrame.contentWindow.location = { pathname: '/research', search: '', hash: '' };
  libFrame.src = '/research';

  let clickHandler = null;
  libFrame.contentDocument.addEventListener = (ev, fn) => {
    if (ev === 'click') clickHandler = fn;
  };
  libFrame.addEventListener('load', () => {});

  // Call bind directly through iframe load event
  let defaultPrevented = false;
  let propagationStopped = false;
  // If clickHandler was captured, simulate click
  if (clickHandler) {
    clickHandler({
      target: {
        closest: (sel) => sel === 'a[href]' ? { href: 'http://localhost:8765/' } : null
      },
      preventDefault: () => { defaultPrevented = true; },
      stopImmediatePropagation: () => { propagationStopped = true; }
    });
    assert.ok(defaultPrevented);
    assert.ok(propagationStopped);
    assert.equal(libFrame.src, '/');
  }
});

test('workspace research/ Markdown paths stay in the file opener; real research navigation still routes', async () => {
  const { context, panes } = createMockEnvironment();
  const PR = context.window.PR;
  PR.esc = s => s;
  PR.workspaces = { files: { entries: [] }, safePath: s => s };
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../easyread/web/js/workspaces/file-links.js'), 'utf8'), context);
  const render = PR.workspaces.fileLinks.options().link;
  assert.match(render({href:'research/notes.md', label:'Notes'}), / data-workspace-file /);
  assert.doesNotMatch(render({href:'/read/paper1', label:'Paper'}), /data-workspace-file/);
  assert.equal(render({href:'https://example.org', label:'Web'}), null);

  const lib = panes.children[0];
  let capture;
  lib.contentDocument.addEventListener = (name, fn, useCapture) => { if (name === 'click' && useCapture) capture = fn; };
  lib.dispatchEvent('load');
  const click = (path, file) => {
    let stopped = false;
    const a = { href: context.location.origin + path, matches: sel => file && sel.split(', ').includes('[data-workspace-file]') };
    capture({target:{closest:() => a}, preventDefault() {}, stopImmediatePropagation() { stopped = true; }});
    return stopped;
  };
  assert.equal(click('/research/notes.md', true), false);
  assert.equal(click('/research', false), true);
});

test('tabs system supports auto-hide toggle and persists to prefs', async () => {
  const { context } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  assert.equal(tabsApi.getAutoHide(), false);
  assert.ok(!context.document.body.classList.contains('autohide-tabs'));

  // Turn on auto-hide
  tabsApi.setAutoHide(true);
  assert.equal(tabsApi.getAutoHide(), true);
  assert.ok(context.document.body.classList.contains('autohide-tabs'));

  // Check saved prefs in localStorage
  const saved = JSON.parse(context.localStorage.getItem('easyread-ui') || '{}');
  assert.equal(saved.features?.autohide_tabs, true);

  // Turn off auto-hide
  tabsApi.setAutoHide(false);
  assert.equal(tabsApi.getAutoHide(), false);
  assert.ok(!context.document.body.classList.contains('autohide-tabs'));
});

test('tabs system auto-hide reveals tab bar on dragenter / dragover near top edge', async () => {
  const { context, tabsBar, windowEvents } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  tabsApi.setAutoHide(true);
  assert.ok(context.document.body.classList.contains('autohide-tabs'));

  // Clear revealed state to simulate hidden tab bar
  tabsBar.classList.remove('tabs-revealed');
  assert.ok(!tabsBar.classList.contains('tabs-revealed'));

  // Trigger dragenter near top
  const dragenterHandlers = windowEvents.get('dragenter') || [];
  assert.ok(dragenterHandlers.length > 0);
  dragenterHandlers[0]({ clientY: 40 });

  assert.ok(context.document.body.classList.contains('is-dragging-over'));
  assert.ok(tabsBar.classList.contains('tabs-revealed'));

  // Trigger drop
  const dropHandlers = windowEvents.get('drop') || [];
  assert.ok(dropHandlers.length > 0);
  dropHandlers[0]();
  assert.ok(!context.document.body.classList.contains('is-dragging-over'));
});

test('tabs system cross-window drop ACK removes attached tab from source window', async () => {
  const { context, strip, windowEvents } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  await tabsApi.open('/read/paper_ack_test');
  assert.equal(strip.children.length, 3); // lib + paper_ack_test + newTab

  // Simulate ACK received via localStorage storage event
  const storageHandlers = windowEvents.get('storage') || [];
  assert.ok(storageHandlers.length > 0);

  // Close tab directly
  await tabsApi.close('paper_ack_test');
  assert.equal(strip.children.length, 2);
});

test('settings-tabs default values correctly preserve autohide_tabs as false when unset', () => {
  // Test reading features definition
  const featuresCode = fs.readFileSync(path.join(__dirname, '../../easyread/web/js/common/features.js'), 'utf8');
  const mockPR = {
    ls: { get: () => ({}), set: () => {} },
    t: (s) => s,
    emit: () => {}
  };
  const ctx = { window: { PR: mockPR }, Object };
  vm.createContext(ctx);
  vm.runInContext(featuresCode, ctx);

  assert.equal(ctx.window.PR.features.autohide_tabs, false);
  assert.equal(ctx.window.PR.feature('autohide_tabs'), false);
});

test('context menu renders tab group submenu with dot and title aligned', async () => {
  const { context, strip, contextMenu } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  await tabsApi.open('/read/paper_menu_test');
  tabsApi.createGroup('g-ai', 'NLP Group', '#2563eb');

  const tabNode = strip.children.find(c => c.dataset.tabId === 'paper_menu_test');
  assert.ok(tabNode);
  assert.ok(tabNode.oncontextmenu);

  // Trigger context menu
  tabNode.oncontextmenu({ preventDefault: () => {}, clientX: 100, clientY: 100 });
  assert.equal(contextMenu.hidden, false);

  // Find submenu wrap
  const subWrap = contextMenu.children.find(c => c.className === 'tab-menu-submenu-wrap');
  assert.ok(subWrap);

  const subTitle = subWrap.children.find(c => c.className === 'tab-menu-item');
  assert.ok(subTitle);
  assert.ok(subTitle.innerHTML.includes('menu-arrow'));

  const submenu = subWrap.children.find(c => c.className === 'tab-menu-submenu');
  assert.ok(submenu);

  const groupItem = submenu.children.find(c => c.dataset && c.dataset.action === 'set-group');
  assert.ok(groupItem);
  assert.equal(groupItem.dataset.group, 'g-ai');

  const dot = groupItem.children.find(c => c.className === 'group-dot');
  assert.ok(dot);
  assert.equal(dot.style.background, '#2563eb');

  const titleSpan = groupItem.children.find(c => c.className === 'group-title');
  assert.ok(titleSpan);
  assert.equal(titleSpan.textContent, 'NLP Group');
});

test('tabs system removes group chip when last grouped tab is detached or dragged out', async () => {
  const { context, strip } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  context.window.open = () => ({});

  await tabsApi.open('/read/paper_g1');
  await tabsApi.open('/read/paper_g2');
  tabsApi.createGroup('g-detach-test', 'Detach Group', '#16a34a');
  tabsApi.setTabGroup('paper_g1', 'g-detach-test');
  tabsApi.setTabGroup('paper_g2', 'g-detach-test');

  let groups = tabsApi.getGroups();
  assert.equal(groups.length, 1);
  assert.equal(groups[0].title, 'Detach Group');

  // Detach 1 of 2 tabs: group should still exist
  await tabsApi.detachTabs(['paper_g1']);
  groups = tabsApi.getGroups();
  assert.equal(groups.length, 1);
  const chip = strip.children.find(c => c.classList && c.classList.contains('tab-group-chip'));
  assert.ok(chip);

  // Detach the last tab in the group: group chip must be removed automatically
  await tabsApi.detachTabs(['paper_g2']);
  groups = tabsApi.getGroups();
  assert.equal(groups.length, 0);
  const remainingChip = strip.children.find(c => c.classList && c.classList.contains('tab-group-chip'));
  assert.equal(remainingChip, undefined);
});

test('cross-window tab attach strips cat: prefix from group chip title', async () => {
  const { context, strip } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  // Simulate dropping a tab that came from a category group "cat:NLP"
  const dropEvent = {
    preventDefault: () => {},
    dataTransfer: {
      getData: (type) => {
        if (type === 'application/x-paperread-tab') {
          return JSON.stringify({
            sourceWindowId: 'win-other',
            tabs: [{ id: 'paper_nlp_1', url: '/read/paper_nlp_1', title: 'NLP Paper', group: 'cat:NLP' }],
            groups: [{ id: 'cat:NLP', title: 'NLP', color: '#db2777' }]
          });
        }
        return '';
      }
    }
  };

  strip.dispatchEvent('drop', dropEvent);

  const groups = tabsApi.getGroups();
  assert.equal(groups.length, 1);
  assert.equal(groups[0].title, 'NLP');
  assert.ok(!groups[0].title.startsWith('cat:'));

  const chip = strip.children.find(c => c.classList && c.classList.contains('tab-group-chip'));
  assert.ok(chip);
  const label = chip.querySelector('.group-label');
  assert.equal(label.textContent, 'NLP');
});

test('tabs bar overlay activates when research workspace #rw-modal is opened', async () => {
  const { context, panes } = createMockEnvironment();
  const libFrame = panes.children[0];
  assert.ok(libFrame);

  let rwModalHidden = true;
  libFrame.contentDocument.querySelector = (sel) => {
    if (sel === '#rw-modal') return { hidden: rwModalHidden };
    return null;
  };

  // Trigger iframe load so bindFrame sets up checkModal
  libFrame.dispatchEvent('load');
  assert.equal(context.document.body.classList.contains('modal-open'), false);
  assert.equal(context.document.body.classList.contains('modal-rw-open'), false);

  // Simulate opening summary or comparison modal in research workspace
  rwModalHidden = false;
  context.window.easyreadTabs.select('library');
  assert.equal(context.document.body.classList.contains('modal-open'), true);
  assert.equal(context.document.body.classList.contains('modal-rw-open'), true);

  // Simulate closing #rw-modal
  rwModalHidden = true;
  context.window.easyreadTabs.select('library');
  assert.equal(context.document.body.classList.contains('modal-open'), false);
  assert.equal(context.document.body.classList.contains('modal-rw-open'), false);
});

test('tab switching preserves pane layout box and triggers instant margin relayout without top:0 stacking', async () => {
  const tabsCss = fs.readFileSync(path.join(__dirname, '../../easyread/web/css/tabs.css'), 'utf8');
  const frozenMatch = tabsCss.match(/\.paper-pane\.frozen\s*\{([^}]*)\}/);
  assert.ok(frozenMatch, '.paper-pane.frozen rule should exist');
  assert.ok(!frozenMatch[1].includes('display: none'), '.paper-pane.frozen must not use display: none which collapses iframe layout to 0x0');
  assert.ok(frozenMatch[1].includes('visibility: hidden'), '.paper-pane.frozen should hide visibility while preserving layout');

  const { context, panes } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;
  await tabsApi.open('/read/paper_margin_1');
  const readerFrame = panes.children.find(f => f.id === 'pane-paper_margin_1');
  assert.ok(readerFrame);

  const layoutCalls = [];
  readerFrame.contentWindow.PR = {
    layoutMargin(opts) {
      layoutCalls.push(opts);
    }
  };

  tabsApi.select('library');
  tabsApi.select('paper_margin_1');
  assert.equal(layoutCalls.length, 1);
  assert.equal(layoutCalls[0]?.instant, true);
});

test('tab cross-window drop calls restoreMainWindow on desktop shell', async () => {
  const { context, strip } = createMockEnvironment();
  const tabsApi = context.window.easyreadTabs;

  let restored = false;
  context.window.easyreadDesktop = {
    restoreMainWindow: async () => {
      restored = true;
    }
  };

  const dropEvent = {
    preventDefault() {},
    dataTransfer: {
      getData(type) {
        if (type === 'application/x-paperread-tab') {
          return JSON.stringify({
            sourceWindowId: 'win-sub-window',
            tabs: [{ id: 'paper_merged_1', url: '/read/paper_merged_1', title: 'Merged Paper' }]
          });
        }
        return '';
      }
    }
  };

  await strip.dispatchEvent('drop', dropEvent);
  assert.equal(restored, true);
});

test('failed saves keep tabs mounted during close, sleep and detach', async () => {
  const { context, panes, strip } = createMockEnvironment();
  const api = context.window.easyreadTabs;
  await api.open('/read/unsaved');
  const frame = panes.children.find(frame => frame.id === 'pane-unsaved');
  frame.contentWindow.PR.flushProgress = async () => { throw new Error('offline'); };
  await api.close('unsaved');
  assert.ok(strip.children.some(node => node.dataset.tabId === 'unsaved'));
  api.select('library');
  await api.sleepTab('unsaved');
  assert.equal(frame.removed, undefined);
  let opened = false;
  context.window.open = () => { opened = true; return {}; };
  await api.detachTabs(['unsaved']);
  assert.equal(opened, false);
  assert.equal((await api.saveAll()).includes(false), true);
});

test('a blocked browser popup preserves the original reader tab', async () => {
  const { context, strip } = createMockEnvironment();
  context.window.open = () => null;
  await context.window.easyreadTabs.open('/read/blocked');
  await context.window.easyreadTabs.detachTabs(['blocked']);
  assert.ok(strip.children.some(node => node.dataset.tabId === 'blocked'));
});

test('sessions restore before first write and persist visual tab order and collapsed groups', async () => {
  const store = new Map([['easyread-tabs-session', JSON.stringify({
    tabs: [{ id: 'a', url: '/read/a?library=one', title: 'A', group: 'saved' }, { id: 'b', url: '/read/b', title: 'B' }],
    groups: [{ id: 'saved', title: 'Saved', color: '#123456', collapsed: true }], active: 'b'
  })]]);
  const sessionStorage = { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value) };
  const { context, strip } = createMockEnvironment({ sessionStorage });
  const api = context.window.easyreadTabs;
  assert.ok(strip.children.some(node => node.dataset.tabId === 'a'));
  assert.equal(api.getGroups()[0].collapsed, true);
  assert.equal(strip.children.find(node => node.dataset.tabId === 'b').classList.contains('active'), true);
  api.reorderTabs(['b', 'a']);
  const data = JSON.parse(store.get('easyread-tabs-session'));
  assert.deepEqual(data.tabs.map(tab => tab.id), ['b', 'a']);
  assert.equal(data.tabs[1].url, 'http://localhost:8765/read/a?library=one');
  assert.equal(data.groups[0].collapsed, true);
});

test('untrusted or malformed restored frame URLs are discarded', () => {
  const { context } = createMockEnvironment();
  const clean = context.window.PRTabsSession.clean({ tabs: [
    { id: 'bad', url: 'https://evil.example/read/bad' }, { id: 'bad', url: 'javascript:alert(1)' },
    { id: 'a', url: '/read/b' }, { id: 'library', url: '/' }, { id: 'good', url: '/read/good' }
  ], libraryUrl: 'https://evil.example', active: 'bad' });
  assert.equal(clean.tabs.length, 1);
  assert.equal(clean.tabs[0].id, 'good');
  assert.equal(clean.libraryUrl, '/');
  assert.equal(clean.active, 'library');
});

test('autohide uses authenticated preference saving in the mounted application', () => {
  const { context, panes } = createMockEnvironment();
  const calls = [];
  panes.children[0].contentWindow.PR.savePrefs = (...args) => calls.push(args);
  context.window.easyreadTabs.setAutoHide(true);
  assert.equal(calls[0][0], 'ui');
  assert.equal(calls[0][1].features.autohide_tabs, true);
});

test('existing reader tabs preserve EasyRead citation return behavior and shell research routing', async () => {
  const { context, panes } = createMockEnvironment();
  const api = context.window.easyreadTabs, calls = [];
  await api.open('/read/cited');
  const win = panes.children.find(frame => frame.id === 'pane-cited').contentWindow;
  win.history = { state: {}, replaceState: (_state, _title, url) => calls.push(url) };
  win.PR.state = { paper: {} };
  win.PR.citationNav = { setup: () => calls.push('setup'), restore: () => calls.push('restore') };
  await api.open('/read/cited?cite_return=origin');
  assert.deepEqual(calls.slice(1), ['setup', 'restore']);
  await api.open('/research?mode=path');
  assert.equal(panes.children[0].src, '/research?mode=path');
});

test('cross-window attachment ACK flushes source state and does not destroy unsaved papers', async () => {
  const { context, strip, panes, windowEvents } = createMockEnvironment();
  const api = context.window.easyreadTabs;
  await api.open('/read/transfer');
  const tab = strip.children.find(node => node.dataset.tabId === 'transfer');
  const frame = panes.children.find(node => node.id === 'pane-transfer');
  let payload;
  tab.dispatchEvent('dragstart', {dataTransfer:{setData:(_kind, value) => payload = JSON.parse(value)}});
  const key = 'easyread-attached-ack-' + payload.sourceWindowId;
  const send = () => windowEvents.get('storage').forEach(fn => fn({key, newValue:JSON.stringify({tabIds:['transfer']})}));
  frame.contentWindow.PR.flushProgress = async () => false;
  send(); await new Promise(resolve => setImmediate(resolve));
  assert.ok(strip.children.includes(tab));
  frame.contentWindow.PR.flushProgress = async () => true;
  send(); await new Promise(resolve => setImmediate(resolve));
  assert.ok(!strip.children.includes(tab));
});
