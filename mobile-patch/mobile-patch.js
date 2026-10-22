/* ============================================================
   DeepSeek Harness — mobile patch companion script (2026-10-22)
   The file/review pane is a full-screen tabbed pane on phones,
   but its only exits are a 20px tab "x" and an unlabeled
   collapse icon — and the app navigates by React state
   (history.length never changes), so there is no back button
   and history.back() does nothing. This injects a floating
   "‹ Kembali" pill, visible only at phone width while the
   pane is open, which presses the app's own native Close /
   Collapse controls.
   ============================================================ */
(function () {
  if (window.__dshMobileBackPatch) return;
  window.__dshMobileBackPatch = true;

  var MQ = window.matchMedia('(max-width: 760px)');
  var btn = null;

  function ensureBtn() {
    if (btn) return btn;
    btn = document.createElement('button');
    btn.id = 'dsh-mobile-back';
    btn.type = 'button';
    btn.textContent = '‹ Kembali';
    btn.setAttribute('aria-label', 'Kembali');
    btn.style.cssText = [
      'position:fixed', 'left:14px', 'bottom:18px', 'z-index:9999',
      'display:none', 'align-items:center', 'gap:6px',
      'padding:11px 18px', 'border-radius:999px',
      'border:1px solid rgba(255,255,255,0.22)',
      'background:rgba(38,38,42,0.94)', 'color:#f2f2f2',
      'font:600 14.5px/1 system-ui,-apple-system,sans-serif',
      'box-shadow:0 6px 24px rgba(0,0,0,0.5)', 'cursor:pointer',
      '-webkit-tap-highlight-color:transparent'
    ].join(';');
    btn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      closePane();
    });
    document.body.appendChild(btn);
    return btn;
  }

  function paneHost() {
    return document.querySelector('[class*="_tabHost_"]');
  }

  function paneOpen() {
    var host = paneHost();
    if (!host) return false;
    var r = host.getBoundingClientRect();
    return r.width > window.innerWidth * 0.6 && r.height > 200;
  }

  function closePane() {
    var host = paneHost();
    var scope = host || document;
    var closeBtn = scope.querySelector('button[aria-label="Close"]');
    if (closeBtn) closeBtn.click();
    setTimeout(function () {
      if (paneOpen()) {
        var col = document.querySelector('button[aria-label="Collapse right sidebar"]');
        if (col) col.click();
      }
      tick();
    }, 350);
  }

  function tick() {
    if (!document.body) return;
    var b = ensureBtn();
    var show = MQ.matches && paneOpen();
    b.style.display = show ? 'inline-flex' : 'none';
  }

  var moTimer = null;
  var mo = new MutationObserver(function () {
    if (moTimer) return;
    moTimer = setTimeout(function () { moTimer = null; tick(); }, 700);
  });

  function start() {
    if (!document.body) { setTimeout(start, 300); return; }
    ensureBtn();
    try {
      mo.observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) { /* interval below still covers us */ }
    setInterval(tick, 1000);
    window.addEventListener('resize', tick);
    tick();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
