/* ==========================================================================
   Mejoras de velocidad percibida en la navegación del portal:

   1. PREFETCH ON HOVER: cuando el mouse se queda 80ms sobre una fila del
      inbox (o cualquier link interno con `data-prefetch`), pre-fetcheamos
      la página de destino. El browser la cachea, así el click siguiente
      es instantáneo. No vale para mobile (no hay hover) ni para conexiones
      con `Save-Data` activado.

   2. TOP PROGRESS BAR: cuando el user hace click en un link interno,
      arrancamos una barra de progreso (rojo brand) arriba de la página.
      Da feedback visual de "estoy yendo" en vez de "no pasa nada".

   3. SCROLL RESTORE: al volver atrás (back), el browser por default
      restaura scroll. Pero si la página recarga porque cayó del bfcache,
      tenemos que persistir el scroll del inbox y restaurarlo on load.
      Usamos sessionStorage con TTL chico para no acumular basura.
   ========================================================================== */
(function () {
  'use strict';

  // ─── 1) PREFETCH ON HOVER ─────────────────────────────────────────────
  const prefetched = new Set();   // urls ya prefetched (no duplicar)

  function shouldPrefetch() {
    if (navigator.connection && navigator.connection.saveData) return false;
    const eff = navigator.connection && navigator.connection.effectiveType;
    if (eff && (eff === '2g' || eff === 'slow-2g')) return false;
    return true;
  }

  function prefetch(url) {
    if (!url || prefetched.has(url)) return;
    prefetched.add(url);
    const link = document.createElement('link');
    link.rel = 'prefetch';
    link.href = url;
    link.as = 'document';
    document.head.appendChild(link);
  }

  if (shouldPrefetch()) {
    let hoverTimer = null;
    document.addEventListener('mouseenter', function (e) {
      const a = e.target.closest && e.target.closest('a.correo-row, a[data-prefetch]');
      if (!a || !a.href) return;
      try {
        const u = new URL(a.href);
        if (u.origin !== location.origin) return;
      } catch (_) { return; }
      clearTimeout(hoverTimer);
      hoverTimer = setTimeout(function () { prefetch(a.href); }, 80);
    }, true);
    document.addEventListener('mouseleave', function () {
      clearTimeout(hoverTimer);
    }, true);
    document.addEventListener('touchstart', function (e) {
      const a = e.target.closest && e.target.closest('a.correo-row, a[data-prefetch]');
      if (a && a.href) prefetch(a.href);
    }, { passive: true });
  }

  // ─── 2) TOP PROGRESS BAR ──────────────────────────────────────────────
  function buildProgressBar() {
    const bar = document.createElement('div');
    bar.id = 'nav-progress-bar';
    bar.style.cssText = (
      'position:fixed;top:0;left:0;height:2px;width:0;' +
      'background:#C80C0F;z-index:99999;' +
      'transition:width .25s ease-out, opacity .3s ease-out;' +
      'opacity:0;pointer-events:none;'
    );
    document.body.appendChild(bar);
    return bar;
  }

  let bar = null;
  function startProgress() {
    if (!bar) bar = buildProgressBar();
    bar.style.opacity = '1';
    bar.style.width = '20%';
    setTimeout(function () { if (bar) bar.style.width = '60%'; }, 200);
    setTimeout(function () { if (bar) bar.style.width = '80%'; }, 600);
  }
  function finishProgress() {
    if (!bar) return;
    bar.style.width = '100%';
    setTimeout(function () {
      if (bar) { bar.style.opacity = '0'; bar.style.width = '0'; }
    }, 250);
  }

  document.addEventListener('click', function (e) {
    const a = e.target.closest && e.target.closest('a');
    if (!a || !a.href) return;
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    if (a.target === '_blank') return;
    try {
      const u = new URL(a.href);
      if (u.origin !== location.origin) return;
      if (u.href === location.href + '#') return;
      if (a.hasAttribute('download')) return;
      if (u.pathname === location.pathname && u.search === location.search && u.hash) return;
    } catch (_) { return; }
    startProgress();
  });
  window.addEventListener('pageshow', function (e) {
    if (bar) { bar.style.opacity = '0'; bar.style.width = '0'; }
  });

  // ─── 3) SCROLL RESTORE EN INBOX ────────────────────────────────────────
  if ('scrollRestoration' in history) {
    history.scrollRestoration = 'auto';
  }

  const SCROLL_KEY = 'inbox_scroll_y';
  const isInbox = location.pathname.indexOf('/intranet/bandeja') === 0;

  if (isInbox) {
    document.addEventListener('click', function (e) {
      const a = e.target.closest && e.target.closest('a.correo-row');
      if (!a) return;
      try {
        sessionStorage.setItem(SCROLL_KEY, JSON.stringify({
          y: window.scrollY, t: Date.now(),
        }));
      } catch (_) { /* quota lleno o privado, ignorar */ }
    });

    window.addEventListener('load', function () {
      try {
        const raw = sessionStorage.getItem(SCROLL_KEY);
        if (!raw) return;
        const data = JSON.parse(raw);
        if (!data || typeof data.y !== 'number') return;
        if (Date.now() - data.t > 30 * 60 * 1000) {
          sessionStorage.removeItem(SCROLL_KEY);
          return;
        }
        const navType = (performance.getEntriesByType('navigation')[0] || {}).type;
        if (navType === 'back_forward' || navType === 'reload') {
          window.scrollTo(0, data.y);
        }
      } catch (_) { /* ignorar */ }
    });
  }
})();
