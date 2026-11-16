/* Graphite Bloom control behavior. Data and pricing remain in app.js. */
(() => {
  'use strict';
  const search = document.getElementById('terminalHeaderSearch');
  const trigger = document.getElementById('headerSearchTrigger');
  const boardSearch = document.getElementById('searchInput');
  const boardTitle = document.getElementById('terminalBoardTitle');

  // Reading a receipt must not activate its parent player card.
  document.addEventListener('click', (event) => {
    if (event.target.closest('.terminal-receipt')) event.stopPropagation();
  }, true);

  trigger?.addEventListener('click', (event) => {
    event.stopImmediatePropagation();
    search.value = boardSearch?.value || '';
    search.focus();
  }, true);

  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k'
        && !document.querySelector('dialog[open]')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      search?.focus();
      search?.select();
    }
    if (event.key === 'Escape' && document.activeElement === search) {
      search.blur();
      trigger?.focus();
    }
  }, true);

  search?.addEventListener('input', () => {
    if (!boardSearch) return;
    boardSearch.value = search.value;
    boardSearch.dispatchEvent(new Event('input', { bubbles: true }));
  });
  search?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    document.getElementById('homeEdgeOpenBoard')?.click();
    boardTitle?.scrollIntoView({ block: 'start' });
    boardTitle?.focus({ preventScroll: true });
  });

  const navMap = { slate: 'bottomNavSlate', saved: 'bottomNavWatchlist',
    record: 'bottomNavLedger', audit: 'bottomNavAudit' };
  document.querySelector('.terminal-desktop-nav')?.addEventListener('click', (event) => {
    const key = event.target.closest('[data-terminal-nav]')?.dataset.terminalNav;
    if (key && navMap[key]) document.getElementById(navMap[key])?.click();
  });

  const wells = ['sportSwitcher', 'mobileSportSwitcher', 'marketChipGroup']
    .map((id) => document.getElementById(id))
    .concat([...document.querySelectorAll('.terminal-board-controls .chip-segment-group[data-eyebrow="MODE"], .terminal-board-controls .view-switch')])
    .filter(Boolean);
  function updateThumb(well) {
    let thumb = well.querySelector(':scope > .terminal-well-thumb');
    if (!thumb) {
      thumb = document.createElement('span');
      thumb.className = 'terminal-well-thumb';
      thumb.setAttribute('aria-hidden', 'true');
      well.prepend(thumb);
    }
    const active = well.querySelector('button.active, button[aria-checked="true"]');
    if (!active) return;
    const box = well.getBoundingClientRect();
    const target = active.getBoundingClientRect();
    well.style.setProperty('--thumb-x', `${target.left - box.left}px`);
    well.style.setProperty('--thumb-w', `${target.width}px`);
  }
  wells.forEach((well) => {
    well.classList.add('terminal-well');
    well.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      const buttons = [...well.querySelectorAll(':scope > button:not([disabled])')];
      const current = buttons.indexOf(document.activeElement);
      if (current < 0 || buttons.length < 2) return;
      event.preventDefault();
      const step = /Right|Down/.test(event.key) ? 1 : -1;
      const next = buttons[(current + step + buttons.length) % buttons.length];
      next.focus();
      next.click();
    });
    well.addEventListener('click', () => requestAnimationFrame(() => updateThumb(well)));
    new MutationObserver(() => requestAnimationFrame(() => updateThumb(well)))
      .observe(well, { childList: true, subtree: true, attributes: true,
        attributeFilter: ['class', 'aria-checked'] });
    updateThumb(well);
  });
  window.addEventListener('resize', () => wells.forEach(updateThumb), { passive: true });
})();
