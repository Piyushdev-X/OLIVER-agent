/* OLIVER website: keyboard navigation (progressive enhancement).
   "/" or ctrl+k (cmd+k on macOS) opens the command palette, "?" lists the
   shortcuts, "g" followed by a letter jumps to a page (chords wait three
   seconds for the second key), j and k move between sections, "[" and "]"
   switch spreadsheet tabs, Escape closes any open dialog. Pages work fully
   without this script. */
(function () {
  'use strict';

  const CHORD_WINDOW_MS = 3000;
  const PAGES = { h: 'index.html', a: 'architecture.html', e: 'evaluation.html', m: 'memory.html' };
  const state = { chordAt: 0, chordTimer: 0 };

  function byId(id) {
    return document.querySelector('#' + id);
  }

  function isTyping(element) {
    if (!element || !element.tagName) {
      return false;
    }
    const tag = element.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || element.isContentEditable;
  }

  /* --- dialogs ------------------------------------------------------------ */

  function openDialog(id) {
    const dialog = byId(id);
    if (!dialog || dialog.open || typeof dialog.showModal !== 'function') {
      return;
    }
    dialog.showModal();
    const input = dialog.querySelector('input');
    if (input) {
      input.value = '';
      filterPalette('');
      input.focus();
    }
  }

  function closeOnBackdrop(event) {
    if (event.target === event.currentTarget) {
      event.currentTarget.close();
    }
  }

  /* --- palette ------------------------------------------------------------ */

  function paletteItems() {
    return Array.prototype.slice.call(document.querySelectorAll('#palette .palette-item'));
  }

  function visibleItems() {
    return paletteItems().filter(function (item) {
      return !item.parentElement.hidden;
    });
  }

  function select(item) {
    paletteItems().forEach(function (other) {
      other.classList.remove('is-selected');
      other.removeAttribute('aria-selected');
    });
    if (item) {
      item.classList.add('is-selected');
      item.setAttribute('aria-selected', 'true');
      item.scrollIntoView({ block: 'nearest' });
    }
  }

  function filterPalette(query) {
    const needle = query.trim().toLowerCase();
    paletteItems().forEach(function (item) {
      const text = (item.textContent + ' ' + (item.dataset.keywords || '')).toLowerCase();
      item.parentElement.hidden = needle !== '' && text.indexOf(needle) === -1;
    });
    select(visibleItems()[0] || null);
  }

  function moveSelection(step) {
    const items = visibleItems();
    if (!items.length) {
      return;
    }
    const current = items.indexOf(document.querySelector('#palette .is-selected'));
    const next = current === -1 ? 0 : (current + step + items.length) % items.length;
    select(items[next]);
  }

  function openSelection() {
    const selected = document.querySelector('#palette .is-selected');
    if (selected) {
      selected.click();
    }
  }

  /* --- sections and sheets -------------------------------------------------- */

  function jumpSection(step) {
    const sections = Array.prototype.slice.call(document.querySelectorAll('[data-section]'));
    if (!sections.length) {
      return;
    }
    const position = window.scrollY + 90;
    let index = -1;
    sections.forEach(function (section, i) {
      if (section.offsetTop <= position) {
        index = i;
      }
    });
    const target = sections[Math.min(sections.length - 1, Math.max(0, index + step))];
    target.scrollIntoView({ block: 'start' });
  }

  function sheetTabs() {
    return Array.prototype.slice.call(document.querySelectorAll('[data-sheet-tab]'));
  }

  function showSheet(id) {
    const tabs = sheetTabs();
    if (!tabs.length) {
      return;
    }
    tabs.forEach(function (tab) {
      const active = tab.dataset.sheetTab === id;
      tab.classList.toggle('is-current', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      const sheet = byId(tab.dataset.sheetTab);
      if (sheet) {
        sheet.hidden = !active;
      }
    });
    const name = document.querySelector('[data-sheet-name]');
    const tab = document.querySelector('[data-sheet-tab="' + id + '"]');
    if (name && tab) {
      name.textContent = tab.dataset.cell || 'A1';
    }
    const expr = document.querySelector('[data-sheet-expr]');
    if (expr && tab) {
      expr.textContent = tab.dataset.expr || '';
    }
  }

  function switchSheet(step) {
    const tabs = sheetTabs();
    if (!tabs.length) {
      return;
    }
    const current = tabs.findIndex(function (tab) {
      return tab.classList.contains('is-current');
    });
    const next = tabs[(current + step + tabs.length) % tabs.length];
    showSheet(next.dataset.sheetTab);
    history.replaceState(null, '', '#' + next.dataset.sheetTab);
  }

  /* --- chords ------------------------------------------------------------- */

  function showChordHint(visible) {
    const hint = byId('chord-hint');
    if (hint) {
      hint.hidden = !visible;
    }
  }

  function startChord() {
    state.chordAt = Date.now();
    showChordHint(true);
    window.clearTimeout(state.chordTimer);
    state.chordTimer = window.setTimeout(function () {
      state.chordAt = 0;
      showChordHint(false);
    }, CHORD_WINDOW_MS);
  }

  function finishChord(key) {
    state.chordAt = 0;
    showChordHint(false);
    const page = Object.prototype.hasOwnProperty.call(PAGES, key) ? PAGES[key] : '';
    if (page) {
      window.location.href = page;
    }
  }

  /* --- key handling --------------------------------------------------------- */

  function onKeyDown(event) {
    const open = document.querySelector('dialog[open]');
    if (open) {
      if (open.id === 'palette') {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          moveSelection(1);
        } else if (event.key === 'ArrowUp') {
          event.preventDefault();
          moveSelection(-1);
        } else if (event.key === 'Enter') {
          event.preventDefault();
          openSelection();
        }
      }
      return;
    }
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      openDialog('palette');
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey || isTyping(event.target)) {
      return;
    }
    if (state.chordAt && Date.now() - state.chordAt <= CHORD_WINDOW_MS) {
      event.preventDefault();
      finishChord(event.key.toLowerCase());
      return;
    }
    switch (event.key) {
      case 'g':
        startChord();
        break;
      case '/':
        event.preventDefault();
        openDialog('palette');
        break;
      case '?':
        event.preventDefault();
        openDialog('shortcuts');
        break;
      case 'j':
        jumpSection(1);
        break;
      case 'k':
        jumpSection(-1);
        break;
      case ']':
        switchSheet(1);
        break;
      case '[':
        switchSheet(-1);
        break;
      default:
        break;
    }
  }

  function init() {
    document.documentElement.classList.add('has-js');
    document.addEventListener('keydown', onKeyDown);
    document.querySelectorAll('[data-open]').forEach(function (button) {
      button.addEventListener('click', function () {
        openDialog(button.dataset.open);
      });
    });
    document.querySelectorAll('dialog').forEach(function (dialog) {
      dialog.addEventListener('click', closeOnBackdrop);
    });
    const input = document.querySelector('#palette input');
    if (input) {
      input.addEventListener('input', function () {
        filterPalette(input.value);
      });
    }
    paletteItems().forEach(function (item) {
      item.addEventListener('mousemove', function () {
        select(item);
      });
    });
    sheetTabs().forEach(function (tab) {
      tab.addEventListener('click', function (event) {
        event.preventDefault();
        showSheet(tab.dataset.sheetTab);
        history.replaceState(null, '', '#' + tab.dataset.sheetTab);
      });
    });
    const tabs = sheetTabs();
    if (tabs.length) {
      const wanted = window.location.hash.slice(1);
      const match = tabs.some(function (tab) {
        return tab.dataset.sheetTab === wanted;
      });
      showSheet(match ? wanted : tabs[0].dataset.sheetTab);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
