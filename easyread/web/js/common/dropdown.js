(() => {
  let active = null, sequence = 0;
  const close = (restore = false) => {
    if (!active) return;
    const { button, menu } = active;
    active = null;
    button.setAttribute('aria-expanded', 'false');
    menu.remove();
    if (restore && button.isConnected) button.focus();
  };
  function sync(select) {
    const box = select.parentElement;
    if (!box || !box.classList.contains('pr-dropdown')) return;
    const button = box.querySelector('.pr-dropdown-trigger');
    button.querySelector('.pr-dropdown-value').textContent = select.selectedOptions[0]?.textContent || '';
    button.disabled = select.disabled;
    box.hidden = select.hidden || select.style.display === 'none';
    if (active?.select === select && (button.disabled || box.hidden)) close();
  }
  function open(select, button) {
    if (active?.select === select) { close(); return; }
    close();
    const menu = document.createElement('div');
    menu.className = 'pr-dropdown-menu';
    menu.setAttribute('role', 'listbox');
    menu.id = 'pr-dropdown-list-' + (++sequence);
    button.setAttribute('aria-controls', menu.id);
    button.setAttribute('aria-expanded', 'true');
    let search = null;
    const filterable = [];
    if (select.getAttribute('data-searchable') === 'true') {
      search = document.createElement('input');
      search.type = 'search'; search.className = 'input pr-dropdown-search';
      search.placeholder = window.PR?.t('搜索选项…') || 'Search…';
      search.setAttribute('aria-label', search.placeholder);
      search.autocomplete = 'off'; search.spellcheck = false;
      menu.append(search);
      search.addEventListener('input', () => {
        const query = search.value.trim().toLocaleLowerCase();
        filterable.forEach(entry => { entry.item.hidden = !entry.keep && !entry.text.includes(query); });
      });
    }
    Array.from(select.options).forEach((option, index) => {
      if (option.hidden) return;
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'pr-dropdown-option';
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', String(option.selected));
      item.disabled = option.disabled || (option.parentElement?.tagName === 'OPTGROUP' && option.parentElement.disabled);
      item.textContent = option.textContent;
      if (option.title) item.title = option.title;
      filterable.push({ item, text: (option.textContent + ' ' + (option.value || '')).toLocaleLowerCase(), keep: option.getAttribute?.('data-search-keep') === 'true' });
      item.addEventListener('click', () => {
        if (search) select.setAttribute('data-search-query', search.value);
        const changed = select.selectedIndex !== index;
        select.selectedIndex = index;
        close(true);
        if (changed || (search?.value.trim() && option.getAttribute?.("data-search-keep") === "true")) {
          select.dispatchEvent(new Event('input', { bubbles: true }));
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        sync(select);
      });
      menu.append(item);
    });
    (select.closest("dialog[open]") || document.body).append(menu);
    active = { select, button, menu, search };
    const rect = button.getBoundingClientRect();
    const width = Math.min(Math.max(rect.width, 180), window.innerWidth - 16);
    menu.style.width = width + 'px';
    menu.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) + 'px';
    menu.style.maxHeight = Math.max(80, Math.min(300, Math.max(window.innerHeight - rect.bottom - 12, rect.top - 12))) + 'px';
    const height = menu.getBoundingClientRect().height;
    menu.style.top = (rect.bottom + height + 8 <= window.innerHeight || rect.top < height + 8 ? rect.bottom + 4 : rect.top - height - 4) + 'px';
    const selected = menu.querySelector('[aria-selected="true"]:not(:disabled)') || menu.querySelector('button:not(:disabled)');
    (search || selected)?.focus();
  }
  function enhance(select) {
    if (select.parentElement?.classList.contains('pr-dropdown') || select.multiple || select.size > 1) return;
    const box = document.createElement('span');
    box.className = 'pr-dropdown';
    select.before(box);
    box.append(select);
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pr-dropdown-trigger';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    const parentLabel = select.labels?.[0];
    const label = select.getAttribute('aria-label') || select.title || parentLabel?.querySelector('span')?.textContent || Array.from(parentLabel?.childNodes || []).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
    if (label) button.setAttribute('aria-label', label.trim());
    button.innerHTML = '<span class="pr-dropdown-value"></span><svg aria-hidden="true" viewBox="0 0 20 20"><path d="m5 8 5 5 5-5"/></svg>';
    box.append(button);
    button.addEventListener('click', () => open(select, button));
    button.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); open(select, button); }
    });
    select.addEventListener('change', () => sync(select));
    for (const prop of ['value', 'selectedIndex']) {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, prop);
      Object.defineProperty(select, prop, { configurable: true, get() { return descriptor.get.call(this); }, set(value) { descriptor.set.call(this, value); sync(this); } });
    }
    sync(select);
  }
  const scan = node => {
    if (node.nodeType !== 1) return;
    if (node.matches('select')) enhance(node);
    node.querySelectorAll('select').forEach(enhance);
  };
  document.querySelectorAll('select').forEach(enhance);
  new MutationObserver(records => {
    for (const record of records) {
      const target = record.target.nodeType === 1 ? record.target : record.target.parentElement;
      const select = target?.closest('select');
      if (select) sync(select);
      record.addedNodes.forEach(scan);
    }
    if (active && !active.button.isConnected) close();
  }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['disabled', 'hidden', 'style', 'selected'] });
  document.addEventListener('pointerdown', e => { if (active && !active.menu.contains(e.target) && !active.button.contains(e.target)) close(); }, true);
  document.addEventListener('keydown', e => {
    if (!active || e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Tab') { close(true); return; }
    if (e.key === 'Escape') { e.preventDefault(); close(true); return; }
    if (active.search && document.activeElement === active.search) {
      if (e.key === 'Enter') {
        e.preventDefault();
        Array.from(active.menu.querySelectorAll('button:not(:disabled)')).find(item => !item.hidden)?.click();
        return;
      }
      if (e.key === 'Home' || e.key === 'End') return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const items = Array.from(active.menu.querySelectorAll('button:not(:disabled)')).filter(item => !item.hidden);
    if (!items.length) return;
    const current = items.indexOf(document.activeElement);
    const index = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : current < 0 ? (e.key === 'ArrowUp' ? items.length - 1 : 0) : (current + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[index].focus();
  });
  window.addEventListener('resize', () => close());
  document.addEventListener('scroll', e => { if (active && !active.menu.contains(e.target)) close(); }, true);
})();
