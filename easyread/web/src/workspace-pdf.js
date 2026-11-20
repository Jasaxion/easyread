import { getDocument, GlobalWorkerOptions, TextLayer } from 'pdfjs-dist/legacy/build/pdf.mjs';

const assets = '/web/vendor/pdfjs/';
GlobalWorkerOptions.workerSrc = assets + 'pdf.worker.min.mjs';
const CSS_UNITS = 96 / 72, MAX_PAGES = 6, MAX_JOBS = 2, MAX_PIXELS = 8_000_000;

// SyncTeX uses 1-based pages and unscaled, top-left PDF points, not canvas pixels.
// PDF.js owns rotation/scaling; the crop-box origin is removed at this boundary.
export function create(container, options = {}) {
  let documentTask, pdf, generation = 0, pages = [], jobs = new Set(), wanted = new Set();
  let zoom = 100, fit = true, currentPage = 1, frame = 0, destroyed = false, highlights = [], lastPosition = null;
  const notify = () => options.onChange?.({ page: currentPage, pages: pages.length, zoom: Math.round(zoom), fit });
  const visible = () => container.clientWidth > 0 && container.clientHeight > 0;
  const cancel = item => {
    item.epoch++; item.renderTask?.cancel(); item.textTask?.cancel();
    item.renderTask = item.textTask = null;
    if (item.canvas) { item.canvas.width = item.canvas.height = 0; item.canvas.remove(); item.canvas = null; }
    item.node.querySelectorAll('.textLayer, .ws-pdf-highlight, .ws-pdf-page-error').forEach(node => node.remove());
    item.ready = false; item.page?.cleanup();
  };
  const dimensions = item => {
    const scale = zoom / 100 * CSS_UNITS;
    item.node.style.width = item.width * scale + 'px'; item.node.style.height = item.height * scale + 'px';
    item.node.style.setProperty('--total-scale-factor', scale * (item.page?.userUnit || 1));
    if (item.page) item.viewport = item.page.getViewport({ scale });
  };
  const schedule = () => { if (!frame && !destroyed) frame = requestAnimationFrame(update); };
  const anchor = () => {
    const item = pages[currentPage - 1];
    return item ? { number: currentPage, fraction: (container.scrollTop - item.node.offsetTop) / item.node.offsetHeight, left: container.scrollLeft } : null;
  };
  const restore = position => {
    const item = pages[Math.min(pages.length, position?.number || 1) - 1];
    if (item) { container.scrollTop = item.node.offsetTop + (position?.fraction || 0) * item.node.offsetHeight; container.scrollLeft = position?.left || 0; }
  };
  function resize(nextZoom = zoom) {
    if (!pages.length) return;
    const position = anchor();
    zoom = Math.max(25, Math.min(400, nextZoom));
    pages.forEach(item => { cancel(item); item.failed = false; dimensions(item); });
    restore(position); notify(); schedule();
  }
  function fitWidth() {
    fit = true;
    const item = pages[currentPage - 1] || pages[0];
    if (item && visible()) resize((container.clientWidth - 32) / (item.width * CSS_UNITS) * 100);
  }
  function mark(item) {
    item.node.querySelectorAll('.ws-pdf-highlight').forEach(node => node.remove());
    if (!item.page || !item.viewport) return;
    for (const hit of highlights.filter(hit => hit.page === item.number)) {
      const [left, , , top] = item.page.view;
      const a = item.viewport.convertToViewportPoint(left + hit.x, top - hit.y);
      const b = item.viewport.convertToViewportPoint(left + hit.x + hit.width, top - hit.y - hit.height);
      const rect = [...a, ...b];
      const node = document.createElement('div'); node.className = 'ws-pdf-highlight';
      Object.assign(node.style, { left: Math.min(rect[0], rect[2]) + 'px', top: Math.min(rect[1], rect[3]) + 'px', width: Math.max(4, Math.abs(rect[2] - rect[0])) + 'px', height: Math.max(8, Math.abs(rect[3] - rect[1])) + 'px' });
      item.node.append(node);
    }
  }
  async function render(item) {
    const token = generation, epoch = item.epoch, document = pdf;
    jobs.add(item); item.rendering = true;
    const outdated = () => token !== generation || epoch !== item.epoch || !wanted.has(item);
    try {
      item.page ||= await document.getPage(item.number);
      if (outdated()) return;
      const size = item.page.getViewport({ scale: 1 });
      if (item.width !== size.width || item.height !== size.height) {
        const position = anchor(); item.width = size.width; item.height = size.height; dimensions(item); restore(position);
      } else dimensions(item);
      const viewport = item.viewport;
      const canvas = window.document.createElement('canvas'); item.canvas = canvas;
      const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(MAX_PIXELS / (viewport.width * viewport.height)));
      canvas.width = Math.max(1, Math.floor(viewport.width * ratio)); canvas.height = Math.max(1, Math.floor(viewport.height * ratio));
      canvas.style.width = viewport.width + 'px'; canvas.style.height = viewport.height + 'px';
      canvas.setAttribute('aria-hidden', 'true'); item.node.append(canvas);
      item.renderTask = item.page.render({ canvasContext: canvas.getContext('2d'), viewport, transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0] });
      await item.renderTask.promise; item.renderTask = null;
      if (outdated()) return;
      const layer = window.document.createElement('div'); layer.className = 'textLayer'; item.node.append(layer);
      item.textTask = new TextLayer({ textContentSource: item.page.streamTextContent(), container: layer, viewport });
      // Explicit dimensions also work on browsers without CSS round(). Text coordinates
      // remain unrotated; the layer's data-main-rotation transform rotates it as a whole.
      const [x0, y0, x1, y1] = item.page.view, factor = viewport.scale * item.page.userUnit;
      layer.style.width = (x1 - x0) * factor + 'px'; layer.style.height = (y1 - y0) * factor + 'px';
      await item.textTask.render(); item.textTask = null;
      if (outdated()) return;
      item.ready = true; mark(item);
    } catch (error) {
      if (!outdated() && !['RenderingCancelledException', 'AbortException'].includes(error.name)) {
        item.failed = true;
        const note = window.document.createElement('p'); note.className = 'ws-pdf-page-error';
        note.textContent = window.PR.t('此页渲染失败，请重新载入 PDF。'); item.node.append(note); options.onError?.(error);
      }
    } finally {
      if (outdated()) item.page?.cleanup();
      item.rendering = false; jobs.delete(item); schedule();
    }
  }
  function update() {
    frame = 0;
    if (!pdf || destroyed) return;
    if (!visible()) { wanted.clear(); pages.forEach(item => { if (item.canvas || item.rendering) cancel(item); }); return; }
    const top = container.scrollTop, bottom = top + container.clientHeight;
    const candidates = pages.filter(item => item.node.offsetTop + item.node.offsetHeight >= top - container.clientHeight && item.node.offsetTop <= bottom + container.clientHeight);
    const distance = item => Math.abs(item.node.offsetTop + item.node.offsetHeight / 2 - (top + bottom) / 2);
    candidates.sort((a, b) => distance(a) - distance(b));
    wanted = new Set(candidates.slice(0, MAX_PAGES));
    const shown = candidates.filter(item => item.node.offsetTop < bottom && item.node.offsetTop + item.node.offsetHeight > top).sort((a, b) => distance(a) - distance(b))[0];
    if (shown && currentPage !== shown.number) { currentPage = shown.number; notify(); }
    pages.forEach(item => { if (!wanted.has(item) && (item.canvas || item.rendering)) cancel(item); });
    for (const item of wanted) {
      if (jobs.size >= MAX_JOBS) break;
      if (!item.rendering && !item.ready && !item.failed) void render(item);
    }
  }
  async function load(url) {
    const previous = anchor() || lastPosition; clear();
    const token = generation;
    documentTask = getDocument({ url, withCredentials: true, cMapUrl: assets + 'cmaps/', cMapPacked: true,
      standardFontDataUrl: assets + 'standard_fonts/', wasmUrl: assets + 'wasm/',
      isEvalSupported: false, disableAutoFetch: true, disableStream: true, rangeChunkSize: 65536 });
    try {
      const loaded = await documentTask.promise;
      if (token !== generation) return;
      pdf = loaded;
      const first = await pdf.getPage(1);
      if (token !== generation) return;
      const size = first.getViewport({ scale: 1 }), fragment = document.createDocumentFragment();
      for (let number = 1; number <= pdf.numPages; number++) {
        const node = document.createElement('div'); node.className = 'ws-pdf-page'; node.dataset.page = number;
        node.setAttribute('role', 'region'); node.setAttribute('aria-label', window.PR.t('第 {n} 页', { n: number }));
        const item = { node, number, width: size.width, height: size.height, epoch: 0, page: number === 1 ? first : null };
        pages.push(item); dimensions(item); fragment.append(node);
      }
      container.append(fragment); currentPage = Math.min(previous?.number || 1, pages.length);
      if (fit) fitWidth(); restore(previous); notify(); schedule();
    } catch (error) { if (token === generation) { clear(); throw error; } }
  }
  function clear() {
    lastPosition = anchor() || lastPosition;
    generation++; cancelAnimationFrame(frame); frame = 0;
    pages.forEach(cancel); pages = []; wanted.clear(); highlights = []; pdf = null; currentPage = 1;
    const task = documentTask; documentTask = null; task?.destroy().catch(() => {});
    container.replaceChildren(); notify();
  }
  async function gotoPage(number) {
    const item = pages[Math.max(0, Math.min(pages.length - 1, Math.round(Number(number) || 1) - 1))];
    if (!item) return;
    currentPage = item.number; container.scrollTop = item.node.offsetTop; notify(); schedule();
  }
  async function highlight(matches) {
    const token = generation;
    highlights = matches.filter(hit => Number.isInteger(hit.page) && hit.page >= 1 && hit.page <= pages.length && [hit.x, hit.y, hit.width, hit.height].every(Number.isFinite));
    const first = highlights[0]; if (!first || !pdf) return false;
    const item = pages[first.page - 1];
    item.page ||= await pdf.getPage(first.page);
    if (token !== generation) return false;
    const size = item.page.getViewport({ scale: 1 }); item.width = size.width; item.height = size.height; dimensions(item);
    await gotoPage(first.page); mark(item);
    const point = item.viewport.convertToViewportPoint(item.page.view[0] + first.x, item.page.view[3] - first.y);
    container.scrollTop = item.node.offsetTop + point[1] - container.clientHeight / 3;
    container.scrollLeft = Math.max(0, item.node.offsetLeft + point[0] - container.clientWidth / 2);
    schedule(); return true;
  }
  const inverse = event => {
    const node = event.target.closest('.ws-pdf-page'), item = pages[Number(node?.dataset.page) - 1];
    if (!item?.viewport || !item.ready || !options.onInverseSync) return;
    const rect = node.getBoundingClientRect();
    const [x, y] = item.viewport.convertToPdfPoint((event.clientX - rect.left) * item.viewport.width / rect.width, (event.clientY - rect.top) * item.viewport.height / rect.height);
    options.onInverseSync({ page: item.number, x: x - item.page.view[0], y: item.page.view[3] - y });
  };
  const keydown = event => {
    if (!(event.ctrlKey || event.metaKey)) return;
    if (['+', '=', '-', '0'].includes(event.key)) {
      event.preventDefault(); if (event.key === '0') fitWidth(); else { fit = false; resize(zoom + (event.key === '-' ? -10 : 10)); }
    }
  };
  let width = 0;
  const observer = new ResizeObserver(() => {
    if (container.clientWidth && width !== container.clientWidth) { width = container.clientWidth; if (fit) fitWidth(); }
    schedule();
  });
  observer.observe(container); container.addEventListener('scroll', schedule, { passive: true });
  container.addEventListener('dblclick', inverse); container.addEventListener('keydown', keydown);
  return { load, clear, gotoPage, highlight, fitWidth,
    setZoom(percent) { fit = false; resize(Number(percent) || 100); },
    refresh: schedule,
    destroy() { destroyed = true; clear(); observer.disconnect(); container.removeEventListener('scroll', schedule); container.removeEventListener('dblclick', inverse); container.removeEventListener('keydown', keydown); }
  };
}
window.PR.workspacePdf = { create };
