/* ==========================================================================
   Inbox: split view + AJAX para destacar / etiquetas / notas + atajos
   Depende de: portal_helpers.js (PM.post, PM.debounce)
   ========================================================================== */

(function () {
  'use strict';

  // ─── Pintar avatares deterministas ──────────────────────────────────────
  function pintarAvatares(scope) {
    (scope || document).querySelectorAll('.avatar[data-color]').forEach(function (el) {
      el.style.backgroundColor = el.dataset.color;
    });
  }
  pintarAvatares();

  // ─── Pintar mini chips de etiqueta con su color ─────────────────────────
  function pintarTagChips(scope) {
    (scope || document).querySelectorAll(
      '.tag-chip-mini[data-color], .tag-chip[data-color], .filter-tag[data-color], .active-chip-tag[data-color]'
    ).forEach(function (el) {
      const color = el.dataset.color;
      if (el.classList.contains('filter-tag') || el.classList.contains('active-chip-tag')) {
        el.style.color = color;
        el.style.borderColor = color;
      } else {
        el.style.backgroundColor = color;
      }
    });
  }
  pintarTagChips();

  // ─── Barras del chart ───────────────────────────────────────────────────
  document.querySelectorAll('.chart-bar[data-h]').forEach(function (el) {
    el.style.height = el.dataset.h + '%';
  });

  // ─── Top remitentes: click filtra ───────────────────────────────────────
  document.querySelectorAll('.sender-chip[data-remitente]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      window.location.href = '?q=' + encodeURIComponent(chip.dataset.remitente);
    });
  });

  // ─── Lista (split) ──────────────────────────────────────────────────────
  const lista   = document.getElementById('split-list');
  const preview = document.getElementById('split-preview');

  // Etiquetas del buzón actual (cargadas desde JSON inline)
  let etiquetasBuzon = [];
  const dataEl = document.getElementById('etiquetas-disponibles-data');
  if (dataEl) {
    try { etiquetasBuzon = JSON.parse(dataEl.textContent); } catch (e) { etiquetasBuzon = []; }
  }

  if (!lista) return;

  let cargando = false;

  // ─── Toggle estrella en una fila ────────────────────────────────────────
  function toggleStarRow(correoId, rowEl) {
    PM.post('/intranet/correo/' + correoId + '/destacar/').then(function (data) {
      const svg = rowEl.querySelector('.row-star svg');
      if (svg) svg.setAttribute('fill', data.destacado ? 'currentColor' : 'none');
      rowEl.classList.toggle('is-starred', data.destacado);
    }).catch(function () { /* silencio: el usuario reintentará */ });
  }

  lista.addEventListener('click', function (e) {
    const star = e.target.closest('.row-star');
    if (star) {
      e.preventDefault();
      e.stopPropagation();
      const row = star.closest('.correo-row');
      toggleStarRow(star.dataset.correoId, row);
      return;
    }

    const row = e.target.closest('.correo-row');
    if (!row) return;
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) {
      // Ctrl/middle/shift click → abrir detalle en nueva pestaña
      window.open(row.dataset.href, '_blank');
      return;
    }
    cargarPreview(row);
  });

  function cargarPreview(row, opts) {
    opts = opts || {};
    const url = row.dataset.previewUrl;
    if (!url || cargando) return;
    cargando = true;

    lista.querySelectorAll('.correo-row.active').forEach(function (r) { r.classList.remove('active'); });
    row.classList.add('active');

    preview.innerHTML = '<div class="preview-loading">Cargando…</div>';
    if (window.innerWidth <= 900) preview.classList.add('show');

    fetch(url, {
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch' },
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (html) {
        preview.innerHTML = html;
        pintarAvatares(preview);
        pintarTagChips(preview);
        wireUpPreview();
        // El server marcó como leído al servir el preview. Reflejarlo en la
        // fila y, si era no-leído, decrementar el badge del buzón actual.
        const wasUnread = !row.classList.contains('is-read');
        row.classList.add('is-read');
        if (wasUnread) ajustarBadgeBuzonActivo(-1);
        if (opts.pushState !== false) {
          history.pushState({ correoId: row.dataset.correoId }, '', row.dataset.href);
        }
      })
      .catch(function () {
        preview.innerHTML = '<div class="preview-empty"><div class="preview-empty-icon">⚠️</div><p>No se pudo cargar el correo. Intenta de nuevo.</p></div>';
      })
      .finally(function () { cargando = false; });
  }

  // ─── Conectar interactividad del preview cuando llega por AJAX ──────────
  function wireUpPreview() {
    // Estrella prominente
    const star = preview.querySelector('.preview-star');
    if (star) {
      star.addEventListener('click', function () {
        const cid = star.dataset.correoId;
        PM.post('/intranet/correo/' + cid + '/destacar/').then(function (data) {
          const svg = star.querySelector('svg');
          svg.setAttribute('fill', data.destacado ? 'currentColor' : 'none');
          star.classList.toggle('is-active', data.destacado);
          // Refleja en la lista también
          const row = lista.querySelector('.correo-row[data-correo-id="' + cid + '"]');
          if (row) {
            row.classList.toggle('is-starred', data.destacado);
            const rowSvg = row.querySelector('.row-star svg');
            if (rowSvg) rowSvg.setAttribute('fill', data.destacado ? 'currentColor' : 'none');
          }
        });
      });
    }

    // Quitar etiqueta (botón × en cada chip)
    preview.querySelectorAll('.tag-remove').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        const chip = btn.closest('.tag-chip');
        const correoId = chip.closest('.preview-tags-wrap').dataset.correoId;
        const tagId = btn.dataset.tagId;
        PM.post('/intranet/correo/' + correoId + '/etiqueta/', {
          etiqueta_id: tagId,
          accion: 'quitar',
        }).then(function () {
          chip.remove();
          // Si no quedan chips, mostrar "Sin etiquetas"
          const wrap = preview.querySelector('.preview-tags-list');
          if (wrap && !wrap.querySelector('.tag-chip')) {
            const empty = document.createElement('span');
            empty.className = 'preview-tags-empty';
            empty.textContent = 'Sin etiquetas';
            wrap.insertBefore(empty, wrap.querySelector('.tag-add-btn'));
          }
          // Y eliminar el chip mini de la fila correspondiente
          const row = lista.querySelector('.correo-row[data-correo-id="' + correoId + '"]');
          if (row) {
            const miniChip = row.querySelector('.tag-chip-mini');
            // No tenemos un mapa exacto, así que omitimos el update preciso aquí.
            // El usuario verá el cambio al recargar la lista.
          }
        });
      });
    });

    // Botón "+ Asignar" → muestra el picker
    const addBtn = preview.querySelector('#tag-add-btn');
    const picker = preview.querySelector('#tag-picker');
    if (addBtn && picker) {
      addBtn.addEventListener('click', function () {
        const correoId = addBtn.dataset.correoId;
        if (picker.hidden) {
          // Construir el picker con etiquetas del buzón que NO estén ya asignadas
          const yaAsignadas = new Set();
          preview.querySelectorAll('.tag-chip[data-tag-id]').forEach(function (el) {
            yaAsignadas.add(el.dataset.tagId);
          });
          picker.innerHTML = '';
          const disponibles = etiquetasBuzon.filter(function (et) {
            return !yaAsignadas.has(String(et.id));
          });
          if (disponibles.length === 0) {
            const empty = document.createElement('span');
            empty.className = 'tag-picker-empty';
            empty.textContent = 'Todas asignadas. Crea una nueva en la barra de filtros.';
            picker.appendChild(empty);
          } else {
            disponibles.forEach(function (et) {
              const b = document.createElement('button');
              b.type = 'button';
              b.className = 'tag-chip';
              b.style.backgroundColor = et.color;
              b.style.cursor = 'pointer';
              b.style.border = 'none';
              b.dataset.tagId = et.id;
              b.innerHTML = '<span class="tag-dot"></span>' + et.nombre;
              b.addEventListener('click', function () {
                PM.post('/intranet/correo/' + correoId + '/etiqueta/', {
                  etiqueta_id: et.id,
                  accion: 'asignar',
                }).then(function (data) {
                  // Insertar el chip en la lista del preview
                  const wrap = preview.querySelector('.preview-tags-list');
                  const empty = wrap.querySelector('.preview-tags-empty');
                  if (empty) empty.remove();
                  const chip = document.createElement('span');
                  chip.className = 'tag-chip';
                  chip.dataset.tagId = data.etiqueta.id;
                  chip.dataset.color = data.etiqueta.color;
                  chip.style.backgroundColor = data.etiqueta.color;
                  chip.innerHTML = '<span class="tag-dot"></span>' + data.etiqueta.nombre +
                    ' <button type="button" class="tag-remove" data-tag-id="' + data.etiqueta.id + '" aria-label="Quitar etiqueta">×</button>';
                  wrap.insertBefore(chip, addBtn);
                  // Conectar el remove del nuevo chip
                  chip.querySelector('.tag-remove').addEventListener('click', function (e) {
                    e.stopPropagation();
                    PM.post('/intranet/correo/' + correoId + '/etiqueta/', {
                      etiqueta_id: data.etiqueta.id, accion: 'quitar',
                    }).then(function () { chip.remove(); });
                  });
                  picker.hidden = true;
                });
              });
              picker.appendChild(b);
            });
          }
          picker.hidden = false;
        } else {
          picker.hidden = true;
        }
      });
    }

    // Botón "marcar como no leído" → vuelve la fila a negrita y suma al badge.
    const unreadBtn = preview.querySelector('.preview-unread-btn');
    if (unreadBtn) {
      unreadBtn.addEventListener('click', function () {
        const cid = unreadBtn.dataset.correoId;
        PM.post('/intranet/correo/' + cid + '/leido/').then(function (data) {
          const row = lista.querySelector('.correo-row[data-correo-id="' + cid + '"]');
          if (row) row.classList.toggle('is-read', data.is_leido);
          // Refresca el badge del buzón actual al valor exacto que devolvió el server
          setBadgeBuzonActivo(data.no_leidos_buzon);
          // Si lo dejamos en no leído, cerramos el preview para refuerzo visual
          if (!data.is_leido) {
            preview.innerHTML = '<div class="preview-empty"><div class="preview-empty-icon">✉️</div>' +
              '<p>Marcado como no leído.</p></div>';
            if (window.innerWidth <= 900) preview.classList.remove('show');
            history.replaceState(null, '', '/intranet/bandeja/');
            lista.querySelectorAll('.correo-row.active').forEach(function (r) {
              r.classList.remove('active');
            });
          }
        }).catch(function () { /* silencio: el usuario reintentará */ });
      });
    }

    // Notas: autosave al perder foco
    const nota = preview.querySelector('.preview-notas-input');
    const status = preview.querySelector('#notas-status');
    if (nota) {
      const guardar = function () {
        if (status) { status.textContent = 'Guardando…'; status.className = 'notas-status saving'; }
        PM.post('/intranet/correo/' + nota.dataset.correoId + '/notas/', {
          notas: nota.value,
        }).then(function () {
          if (status) { status.textContent = 'Guardado ✓'; status.className = 'notas-status saved'; }
          setTimeout(function () { if (status) status.textContent = ''; }, 2000);
        }).catch(function () {
          if (status) { status.textContent = 'Error al guardar'; status.className = 'notas-status'; }
        });
      };
      nota.addEventListener('blur', guardar);
      nota.addEventListener('input', PM.debounce(guardar, 1500));
    }
  }

  // ─── Atajos de teclado ──────────────────────────────────────────────────
  function filaActiva() { return lista.querySelector('.correo-row.active'); }
  function todasFilas() { return Array.from(lista.querySelectorAll('.correo-row')); }

  document.addEventListener('keydown', function (e) {
    const t = e.target;
    const isInput = t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable;
    if (isInput) return;

    const filas = todasFilas();
    if (!filas.length) return;
    let idx = filas.indexOf(filaActiva());

    if (e.key === 'j') {
      e.preventDefault();
      idx = (idx + 1) % filas.length;
      cargarPreview(filas[idx]);
      filas[idx].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'k') {
      e.preventDefault();
      idx = idx <= 0 ? filas.length - 1 : idx - 1;
      cargarPreview(filas[idx]);
      filas[idx].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && idx >= 0) {
      e.preventDefault();
      window.location.href = filas[idx].dataset.href;
    } else if (e.key === 's' && idx >= 0) {
      e.preventDefault();
      toggleStarRow(filas[idx].dataset.correoId, filas[idx]);
    }
  });

  // ─── Sidebar de buzones: badge helpers + drawer en mobile ──────────────
  function badgeNodoActivo() {
    const item = document.querySelector('.sidebar-buzon-item.active');
    return item ? item.querySelector('.sidebar-buzon-badge') : null;
  }
  function setBadgeBuzonActivo(n) {
    const item = document.querySelector('.sidebar-buzon-item.active');
    if (!item) return;
    let badge = item.querySelector('.sidebar-buzon-badge');
    if (n > 0) {
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'sidebar-buzon-badge';
        item.appendChild(badge);
      }
      badge.textContent = n;
      item.classList.add('has-unread');
    } else if (badge) {
      badge.remove();
      item.classList.remove('has-unread');
    }
  }
  function ajustarBadgeBuzonActivo(delta) {
    const badge = badgeNodoActivo();
    const actual = badge ? parseInt(badge.textContent, 10) || 0 : 0;
    setBadgeBuzonActivo(Math.max(0, actual + delta));
  }

  const sidebar = document.getElementById('inbox-sidebar');
  const sidebarToggle = document.getElementById('sidebar-toggle');
  const sidebarClose = document.getElementById('sidebar-close');
  const backdrop = document.getElementById('sidebar-backdrop');

  function abrirSidebar() {
    if (!sidebar) return;
    sidebar.classList.add('open');
    if (backdrop) backdrop.hidden = false;
  }
  function cerrarSidebar() {
    if (!sidebar) return;
    sidebar.classList.remove('open');
    if (backdrop) backdrop.hidden = true;
  }
  if (sidebarToggle)  sidebarToggle.addEventListener('click', abrirSidebar);
  if (sidebarClose)   sidebarClose.addEventListener('click', cerrarSidebar);
  if (backdrop)       backdrop.addEventListener('click', cerrarSidebar);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && sidebar && sidebar.classList.contains('open')) {
      cerrarSidebar();
    }
  });

  // ─── Restaurar al volver atrás ──────────────────────────────────────────
  window.addEventListener('popstate', function (ev) {
    if (ev.state && ev.state.correoId) {
      const row = lista.querySelector('.correo-row[data-correo-id="' + ev.state.correoId + '"]');
      if (row) cargarPreview(row, { pushState: false });
    }
  });

  // ─── Crear etiqueta nueva (diálogo en barra de filtros) ─────────────────
  const btnNew = document.getElementById('btn-new-tag');
  const dlg = document.getElementById('new-tag-dialog');
  const colorBtns = document.getElementById('new-tag-colors');
  const nameInput = document.getElementById('new-tag-name');
  const createBtn = document.getElementById('new-tag-create');
  const cancelBtn = document.getElementById('new-tag-cancel');

  if (btnNew && dlg) {
    let colorElegido = '#C80C0F';
    btnNew.addEventListener('click', function () {
      dlg.hidden = !dlg.hidden;
      if (!dlg.hidden) nameInput.focus();
    });
    cancelBtn.addEventListener('click', function () {
      dlg.hidden = true;
      nameInput.value = '';
    });
    colorBtns.querySelectorAll('button[data-color]').forEach(function (b) {
      b.addEventListener('click', function () {
        colorBtns.querySelectorAll('button').forEach(function (x) { x.classList.remove('selected'); });
        b.classList.add('selected');
        colorElegido = b.dataset.color;
      });
    });
    // Selecciona el primero por default
    colorBtns.querySelector('button').classList.add('selected');

    createBtn.addEventListener('click', function () {
      const nombre = nameInput.value.trim();
      if (!nombre) { nameInput.focus(); return; }
      PM.post('/intranet/buzon/etiqueta-nueva/', {
        nombre: nombre,
        color: colorElegido,
      }).then(function () {
        // Recarga para que aparezca en la barra de filtros (más simple que reconstruir DOM)
        window.location.reload();
      });
    });

    nameInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); createBtn.click(); }
    });
  }
})();
