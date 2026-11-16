(() => {
  'use strict';

  const menu = document.getElementById('frontMenuDialog');
  const menuButton = document.getElementById('frontMenuBtn');
  const exploreButton = document.getElementById('explorePropsBtn');
  const board = document.getElementById('fullPlayerBoard');
  if (!menu || !menuButton || !exploreButton || !board) return;

  const header = document.querySelector('.pw-terminal-chrome');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  function scrollToSection(section) {
    if (!section) return;
    const top = section.getBoundingClientRect().top + window.scrollY - (header?.offsetHeight || 0) - 12;
    window.scrollTo({ top, behavior: reduceMotion.matches ? 'instant' : 'smooth' });
  }

  function setBoardOpen(open, scroll = false) {
    document.body.classList.toggle('front-board-open', open);
    if (open && !exploreButton.matches('[aria-expanded=true]')) document.getElementById('modeAllBtn')?.click();
    exploreButton.setAttribute('aria-expanded', String(open));
    exploreButton.firstChild.textContent = open ? 'Hide full player board ' : 'Explore all player props ';
    if (scroll) scrollToSection(open ? board : document.getElementById('todaySchedule'));
  }

  menuButton.addEventListener('click', () => menu.showModal());
  document.getElementById('frontMenuClose').addEventListener('click', () => menu.close());
  menu.addEventListener('click', (event) => {
    if (event.target === menu) menu.close();
  });
  exploreButton.addEventListener('click', () => setBoardOpen(!document.body.classList.contains('front-board-open'), true));
  document.getElementById('homeEdgeOpenBoard')?.addEventListener('click', () => setBoardOpen(true, true));
  const inlineSearch = document.getElementById('frontInlineSearch');
  const boardSearch = document.getElementById('searchInput');
  const searchDialog = document.getElementById('terminalSearchDialog');

  function applySearch() {
    const query = inlineSearch.value.toLowerCase();
    const market = /receiving yards|\byards\b/.test(query) ? 'receiving_yards'
      : /\breceptions?\b/.test(query) ? 'receptions'
      : /\bpoints?\b/.test(query) ? 'points'
      : /anytime (touchdown|td)|\btouchdowns?\b/.test(query) ? 'anytime_td' : null;
    if (market) document.querySelector(`#marketChipGroup [data-market="${market}"]`)?.click();
    if (boardSearch) {
      boardSearch.value = inlineSearch.value;
      boardSearch.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
  document.getElementById('homeEdgeAudit')?.addEventListener('click', () => document.getElementById('clvReportBtn')?.click());

  function openSearch() {
    if (!searchDialog || searchDialog.open) return;
    inlineSearch.value = boardSearch?.value || '';
    searchDialog.showModal();
  }

  document.getElementById('terminalSearchClose')?.addEventListener('click', () => searchDialog.close());
  searchDialog?.addEventListener('close', () => {
    if (searchDialog.returnValue === 'results') {
      setBoardOpen(true);
      applySearch();
      scrollToSection(board);
      document.getElementById('terminalBoardTitle')?.focus({ preventScroll: true });
    }
    searchDialog.returnValue = '';
  });
  searchDialog?.addEventListener('click', (event) => {
    if (event.target === searchDialog) searchDialog.close();
  });
  document.getElementById('headerSearchTrigger')?.addEventListener('click', openSearch);
  document.getElementById('bottomNavSlate')?.addEventListener('click', () => setBoardOpen(true, true));
  document.getElementById('bottomNavWatchlist')?.addEventListener('click', () => setBoardOpen(true, true));
  document.getElementById('navMarkets')?.addEventListener('click', (e) => {
    e.preventDefault();
    setBoardOpen(true, true);
    document.getElementById('marketChipGroup')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  document.getElementById('navOverview')?.addEventListener('click', (e) => {
    e.preventDefault();
    document.getElementById('slateBoard')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) {
      event.preventDefault();
      openSearch();
    }
  });
  menu.querySelector('.front-menu-actions').addEventListener('click', (event) => {
    const action = event.target.closest('[data-front-action]')?.dataset.frontAction;
    if (!action) return;
    menu.close();
    if (action === 'props') setBoardOpen(true, true);
    if (action === 'search') {
      openSearch();
    }
    if (action === 'saved') document.getElementById('portfolioBtn')?.click();
    if (action === 'alerts') {
      setBoardOpen(true);
      document.getElementById('availabilityToggle')?.click();
      document.getElementById('availabilityDetails')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (action === 'settings') document.getElementById('mobileMenuBtn')?.click();
    if (action === 'membership') document.getElementById('pricingBtn')?.click();
    if (action === 'theme') document.getElementById('themeToggle')?.click();
  });
})();
