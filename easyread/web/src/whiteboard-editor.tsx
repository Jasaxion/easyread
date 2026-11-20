import React from 'react';
import { createRoot } from 'react-dom/client';
import { Excalidraw, MainMenu, CaptureUpdateAction, convertToExcalidrawElements,
  getSceneVersion, serializeAsJSON, viewportCoordsToSceneCoords } from '@excalidraw/excalidraw';
import '@excalidraw/excalidraw/index.css';

// A local same-origin document isolates reader shortcuts, Pencil capture and CSS.
const root = createRoot(document.getElementById('root')!);
let api: any, options: any, libraryItems: any[] = [], timer: any, signature = '', hydrated = false, ready: (() => void) | undefined;
const MIME = 'application/x-easyread-whiteboard-source';
const appKeys = ['viewBackgroundColor', 'gridSize', 'gridStep', 'gridModeEnabled', 'scrollX', 'scrollY', 'zoom',
  'exportBackground', 'exportEmbedScene', 'exportWithDarkMode', 'exportScale', 'name',
  'currentItemStrokeColor', 'currentItemBackgroundColor', 'currentItemFillStyle', 'currentItemStrokeWidth',
  'currentItemStrokeStyle', 'currentItemRoughness', 'currentItemOpacity', 'currentItemFontFamily',
  'currentItemFontSize', 'currentItemTextAlign', 'currentItemStartArrowhead', 'currentItemEndArrowhead', 'currentItemRoundness'];
function safeLink(link: any) {
  if (!link) return true;
  if (typeof link !== 'string' || /[\x00-\x20\\]/.test(link) || link.length > 4096) return false;
  if (link.startsWith('#b-') || link.startsWith('/read/')) return true;
  try { const u = new URL(link); return ['http:', 'https:'].includes(u.protocol) && !u.username; } catch { return false; }
}
function sanitize(elements: any[]) {
  return elements.filter(e => !['iframe', 'embeddable'].includes(e.type)).map(e => safeLink(e.link) ? e : { ...e, link: null });
}
function snapshot() {
  const scene = JSON.parse(serializeAsJSON(api.getSceneElementsIncludingDeleted(), api.getAppState(), api.getFiles(), 'local'));
  scene.appState = Object.fromEntries(appKeys.filter(k => k in api.getAppState()).map(k => [k, api.getAppState()[k]]));
  scene.elements = sanitize(scene.elements);
  scene.libraryItems = libraryItems.map(item => ({ ...item, elements: sanitize(item.elements) }));
  return scene;
}
function fingerprint() {
  const state = api.getAppState();
  return JSON.stringify([getSceneVersion(api.getSceneElementsIncludingDeleted()),
    appKeys.map(k => state[k]), Object.keys(api.getFiles()), libraryItems]);
}
function flush() {
  clearTimeout(timer);
  if (!api || !options || !hydrated) return;
  const next = fingerprint();
  if (next === signature) return;
  signature = next;
  options.onChange(snapshot());
}
function render() {
  root.render(<Excalidraw initialData={{ ...options.scene, appState: { ...options.scene.appState, exportEmbedScene: true } }}
    name={options.name} langCode={options.lang} theme={options.theme} autoFocus handleKeyboardGlobally
    aiEnabled={false} validateEmbeddable={false} UIOptions={{ canvasActions: { toggleTheme: false }, dockedSidebarBreakpoint: 900 }}
    excalidrawAPI={value => { api = value; }}
    onChange={(_elements, state) => {
      if (!api || state.isLoading) return;
      if (!hydrated) { hydrated = true; signature = fingerprint(); queueMicrotask(() => { ready?.(); ready = undefined; }); return; }
      clearTimeout(timer); timer = setTimeout(flush, 200);
    }}
    onLibraryChange={items => { libraryItems = items; flush(); }}
    onLinkOpen={(element, event) => { event.preventDefault(); if (safeLink(element.link)) options.onLink(element.link); }}>
    <MainMenu>
      <MainMenu.DefaultItems.LoadScene />
      <MainMenu.DefaultItems.SaveToActiveFile />
      <MainMenu.DefaultItems.Export />
      <MainMenu.DefaultItems.SaveAsImage />
      <MainMenu.Separator />
      <MainMenu.DefaultItems.SearchMenu />
      <MainMenu.DefaultItems.CommandPalette />
      <MainMenu.DefaultItems.ClearCanvas />
      <MainMenu.DefaultItems.ChangeCanvasBackground />
      <MainMenu.DefaultItems.Help />
    </MainMenu>
  </Excalidraw>);
}
const bridge = {
  async mount(config: any) {
    options = config; libraryItems = config.scene.libraryItems || []; signature = ''; hydrated = false;
    const promise = new Promise<void>(resolve => { ready = resolve; }); render(); await promise;
  },
  theme(theme: string) { if (options) { options.theme = theme; render(); } },
  flush,
  snapshot,
  insert(source: any, point?: { x: number; y: number }) {
    if (!api || !source?.text) return;
    const p = viewportCoordsToSceneCoords({ clientX: point?.x ?? innerWidth / 2, clientY: point?.y ?? innerHeight / 2 }, api.getAppState());
    const text = [source.label, source.text].filter(Boolean).join('\n\n');
    const elements = convertToExcalidrawElements([{ type: 'rectangle', x: p.x, y: p.y, width: 360,
      backgroundColor: '#fff3bf', fillStyle: 'solid', strokeColor: '#9c5f1c', roundness: { type: 3 },
      label: { text, fontSize: 18, fontFamily: 2, textAlign: 'left' } }]);
    const linked = elements.map(el => ({ ...el, link: source.link || null,
      customData: { easyread: { paper: options.paper, anchor: source.anchor || '', kind: source.kind || 'quote' } } }));
    api.updateScene({ elements: [...api.getSceneElementsIncludingDeleted(), ...linked], captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    // A source action can reveal a previously hidden dock. Refresh its cached
    // dimensions before fitting; otherwise Excalidraw fits into a zero-size view.
    api.refresh();
    requestAnimationFrame(() => { api.refresh(); requestAnimationFrame(() => {
      api.scrollToContent(linked, { fitToContent: true, maxZoom: 1 }); flush();
    }); });
    flush();
  },
};
(window as any).EasyReadWhiteboardEditor = bridge;
// Handle only EasyRead references. Image, .excalidraw and library drops remain native.
window.addEventListener('dragover', event => {
  if (event.dataTransfer?.types.includes(MIME)) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }
});
window.addEventListener('drop', event => {
  const raw = event.dataTransfer?.getData(MIME);
  if (!raw || !options) return;
  event.preventDefault(); event.stopImmediatePropagation();
  try {
    const source = options.resolveSource(JSON.parse(raw));
    if (source) bridge.insert(source, { x: event.clientX, y: event.clientY });
  } catch { /* Ignore foreign/malformed drags. */ }
}, true);
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !event.isComposing && api) {
    // The upstream mobile main menu does not dismiss on Escape by itself.
    if (document.querySelector('.dropdown-menu-container [data-testid="load-button"]')) {
      (document.querySelector('[data-testid="main-menu-trigger"]') as HTMLButtonElement)?.click();
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    // Snapshot before Excalidraw dismisses its own menu/dialog with the same key.
    const state = api.getAppState();
    const editable = (event.target as Element)?.closest?.('textarea,[contenteditable="true"],input:not([type="radio"]):not([type="checkbox"])');
    const busy = editable || state.openMenu || state.openDialog || state.openSidebar || state.editingTextElement || state.editingLinearElement || state.multiElement || state.contextMenu;
    if (!busy) queueMicrotask(() => options?.onEscape());
  }
}, true);
let resizeFrame = 0;
new ResizeObserver(() => {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
}).observe(document.getElementById('root')!);
window.addEventListener('pagehide', flush);
document.addEventListener('visibilitychange', () => { if (document.hidden) flush(); });
