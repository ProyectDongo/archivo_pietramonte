// Lightbox liviano para previsualizar imágenes adjuntas sin recargar.
// Sin dependencias. CSP-safe (todo via addEventListener).
(function () {
  'use strict';

  const lb = document.getElementById('lightbox');
  if (!lb) return;
  const img = lb.querySelector('.lightbox-img');
  const cap = lb.querySelector('.lightbox-caption');
  const closeBtn = lb.querySelector('.lightbox-close');

  function abrir(src, name, size) {
    img.src = src;
    img.alt = name || '';
    cap.textContent = (name || '') + (size ? ' · ' + size : '');
    lb.hidden = false;
    document.body.style.overflow = 'hidden';
  }

  function cerrar() {
    lb.hidden = true;
    img.src = '';
    document.body.style.overflow = '';
  }

  // Intercepta clicks en thumbnails (delegación: funciona aunque el thumbnail
  // se inserte después del DOMContentLoaded, ej. via fetch del preview).
  document.addEventListener('click', function (e) {
    const t = e.target.closest('.adj-thumb');
    if (!t) return;
    e.preventDefault();
    abrir(
      t.getAttribute('href'),
      t.getAttribute('data-name') || '',
      t.getAttribute('data-size') || ''
    );
  });

  // Cerrar: click en backdrop, en la X, o ESC.
  lb.addEventListener('click', function (e) {
    if (e.target === lb) cerrar();
  });
  closeBtn.addEventListener('click', cerrar);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !lb.hidden) cerrar();
  });
})();
