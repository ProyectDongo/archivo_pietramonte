/* ==========================================================================
   Login + captcha emoji
   - Activa el botón solo cuando hay al menos 1 celda seleccionada Y email válido.
   - Refresh del captcha sin recargar (AJAX a /intranet/captcha/).
   - Marca el momento real en el cliente (no confiar en el server-side).
   ========================================================================== */

(function () {
  'use strict';

  const form    = document.getElementById('login-form');
  const email   = document.getElementById('email');
  const password = document.getElementById('password');
  const grid    = document.getElementById('captcha-grid');
  const tokenIn = document.getElementById('captcha-token');
  const preg    = document.getElementById('captcha-pregunta');
  const hint    = document.getElementById('captcha-hint');
  const refresh = document.getElementById('captcha-refresh');
  const submit  = document.getElementById('btn-acceder');
  const loadedAt = document.getElementById('page-loaded-at');
  const passToggle = document.getElementById('pass-toggle');

  if (!form || !grid) return;

  // ─── Toggle mostrar/ocultar contraseña ──────────────────────────────────
  if (passToggle && password) {
    passToggle.addEventListener('click', function () {
      const showing = password.type === 'text';
      password.type = showing ? 'password' : 'text';
      passToggle.setAttribute('aria-label', showing ? 'Mostrar contraseña' : 'Ocultar contraseña');
    });
  }

  // ─── Estado interno ─────────────────────────────────────────────────────
  const seleccionadas = new Set();

  function actualizarBoton() {
    const okEmail = email.value.includes('@') && email.value.length >= 5;
    const okPassword = password && password.value.length >= 1;
    const okCaptcha = seleccionadas.size > 0;
    submit.disabled = !(okEmail && okPassword && okCaptcha);
  }

  // ─── Click en celdas ────────────────────────────────────────────────────
  grid.addEventListener('click', function (e) {
    const cell = e.target.closest('.captcha-cell');
    if (!cell) return;
    const idx = cell.dataset.idx;
    const wasSelected = cell.classList.toggle('on');
    if (wasSelected) seleccionadas.add(idx);
    else seleccionadas.delete(idx);

    // Hidden inputs con la selección actual
    sincronizarHiddenInputs();
    actualizarBoton();
  });

  function sincronizarHiddenInputs() {
    // Limpia previos
    form.querySelectorAll('input[name="captcha_seleccion[]"]').forEach(function (el) { el.remove(); });
    // Agrega uno por seleccionada
    seleccionadas.forEach(function (idx) {
      const inp = document.createElement('input');
      inp.type = 'hidden';
      inp.name = 'captcha_seleccion[]';
      inp.value = idx;
      form.appendChild(inp);
    });
  }

  // ─── Email/password cambian → reevalúa botón ────────────────────────────
  email.addEventListener('input', actualizarBoton);
  if (password) password.addEventListener('input', actualizarBoton);

  // ─── Refrescar captcha sin recargar ─────────────────────────────────────
  refresh.addEventListener('click', function () {
    refresh.classList.add('rotating');
    fetch('/intranet/captcha/', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) {
        // Pinta nuevo grid con SVG
        grid.innerHTML = '';
        const SVG_NS = 'http://www.w3.org/2000/svg';
        data.celdas.forEach(function (celda, i) {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'captcha-cell';
          btn.dataset.idx = String(i);
          btn.setAttribute('aria-label', celda.nombre);

          // Construye el <svg> con namespace correcto
          const svg = document.createElementNS(SVG_NS, 'svg');
          svg.setAttribute('viewBox', '0 0 24 24');
          svg.setAttribute('fill', 'none');
          svg.setAttribute('stroke', 'currentColor');
          svg.setAttribute('stroke-width', '1.8');
          svg.setAttribute('stroke-linecap', 'round');
          svg.setAttribute('stroke-linejoin', 'round');
          svg.setAttribute('aria-hidden', 'true');
          // celda.svg viene del servidor (constantes nuestras, no input de usuario)
          svg.innerHTML = celda.svg;
          btn.appendChild(svg);
          grid.appendChild(btn);
        });
        preg.textContent = data.pregunta;
        tokenIn.value = data.token;
        seleccionadas.clear();
        sincronizarHiddenInputs();
        actualizarBoton();
        if (hint) hint.textContent = 'Toca todas las casillas correctas y luego ingresa.';
      })
      .catch(function () {
        if (hint) hint.textContent = 'No pudimos cambiar el desafío. Recarga la página.';
      })
      .finally(function () {
        setTimeout(function () { refresh.classList.remove('rotating'); }, 400);
      });
  });

  // ─── Estado inicial ─────────────────────────────────────────────────────
  actualizarBoton();
})();
