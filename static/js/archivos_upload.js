/* ============================================================
   Modal de upload para Archivos / Contratos.
   Vanilla JS, sin libs. Cumple CSP estricto (no inline scripts).
   ============================================================ */
(function () {
  'use strict';

  var modal = document.getElementById('upload-modal');
  if (!modal) return;

  function open() {
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
  }
  function close() {
    modal.hidden = true;
    document.body.style.overflow = '';
  }

  // Botones que abren el modal (puede haber más de uno: header + empty state)
  var openers = document.querySelectorAll(
    '#btn-subir-archivo, #btn-subir-archivo-empty'
  );
  openers.forEach(function (b) {
    b.addEventListener('click', open);
  });

  // Botones que cierran el modal (backdrop, X, cancelar)
  modal.querySelectorAll('[data-close-modal]').forEach(function (b) {
    b.addEventListener('click', close);
  });

  // Esc cierra
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !modal.hidden) close();
  });

  // Mostrar campos contrato solo cuando tipo=contrato (en form de Archivos
  // donde el select de tipo existe). En la vista Contratos siempre van.
  var tipoSelect = modal.querySelector('select[name="tipo"]');
  var contratoFields = modal.querySelectorAll('[data-contrato-only]');
  if (tipoSelect && contratoFields.length) {
    function syncContratoFields() {
      var show = tipoSelect.value === 'contrato';
      contratoFields.forEach(function (el) {
        el.hidden = !show;
      });
    }
    tipoSelect.addEventListener('change', syncContratoFields);
    syncContratoFields();
  }
})();
