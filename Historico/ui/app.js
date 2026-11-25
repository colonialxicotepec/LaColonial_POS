'use strict';

const input = document.getElementById('q');
const tbody = document.getElementById('tbody');
const detail = document.getElementById('detail');
const empty = document.getElementById('empty');
const countEl = document.getElementById('count');
const truncatedEl = document.getElementById('truncated');
const statusEl = document.getElementById('status');
const loadedEl = document.getElementById('loaded');
const clearBtn = document.getElementById('clear');
const incluirEl = document.getElementById('incluir');
const redTag = document.getElementById('tag-red');

// ---- pestaña de terceros ----
const input3 = document.getElementById('q3');
const tbody3 = document.getElementById('tbody3');
const detail3 = document.getElementById('detail3');
const empty3 = document.getElementById('empty3');
const countEl3 = document.getElementById('count3');
const truncatedEl3 = document.getElementById('truncated3');
const clearBtn3 = document.getElementById('clear3');
const incluirEl3 = document.getElementById('incluir3');
const tipoEl3 = document.getElementById('tipo3');

let timer = null;
let lastQuery = null;
let lastDeleted = null;
let lastExactKey = null;
let audioCtx = null;
let ready = false;
let firstFocus = false;

let timer3 = null;
let lastQuery3 = null;
let lastType3 = null;
let lastDeleted3 = null;
let activeTab = 'productos';
let selectedRow3 = 0;

// ------------------------------------------------------------------ utilidades
const fmtMoney = (n) =>
  Number(n || 0).toLocaleString('es-MX', { style: 'currency', currency: 'MXN' });

const fmtQty = (n) => {
  const v = Number(n || 0);
  return Number.isInteger(v)
    ? v.toLocaleString('es-MX')
    : v.toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const fmtSigned = (n) => {
  const v = Number(n || 0);
  const sign = v > 0 ? '+' : (v < 0 ? '−' : '');
  return sign + fmtQty(Math.abs(v));
};

const fmtDate = (s) => {
  if (!s) return '—';
  const m = String(s).match(/(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
  if (!m) return String(s);
  return `${m[3]}/${m[2]}/${m[1]}${m[4] ? ` ${m[4]}:${m[5]}` : ''}`;
};

const escapeHtml = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));

async function api(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}

function beep(found) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = 'sine';
    osc.frequency.value = found ? 880 : 220;
    gain.gain.value = 0.05;
    osc.connect(gain).connect(audioCtx.destination);
    const now = audioCtx.currentTime;
    osc.start(now);
    osc.stop(now + (found ? 0.08 : 0.2));
  } catch (_) { /* sin audio, no pasa nada */ }
}

function stateInfo(p) {
  if (p.deleted) return { cls: 'bad', text: 'Eliminado' };
  if (p.inactive) return { cls: 'warn', text: 'Inactivo' };
  return { cls: 'ok', text: 'Activo' };
}

// ------------------------------------------------------------------ ficha
function renderDetail(p, kind, options) {
  const st = stateInfo(p);
  detail.className = 'detail' + (kind ? ' ' + kind : '') + (p.deleted ? ' deleted-item' : '');
  detail.hidden = false;
  detail.dataset.id = p.id;

  const badges = [`<span class="badge ${st.cls}">${st.text}</span>`];
  if (p.deleted) badges.push('<span class="badge neutral">No está activo en 1box</span>');
  if (Number(p.stock) <= 0) badges.push('<span class="badge neutral">Sin existencia</span>');
  if (p.promo) badges.push('<span class="badge warn">Precio promoción</span>');

  const meta = [];
  if (p.category) meta.push(`<b>Categoría</b> ${escapeHtml(p.category)}`);
  if (p.supplier) meta.push(`<b>Proveedor</b> ${escapeHtml(p.supplier)}`);
  meta.push(`<b>ID 1box</b> ${escapeHtml(p.id)}`);
  if (p.last_modified) meta.push(`<b>Última modificación</b> ${fmtDate(p.last_modified)}`);

  const codes = [];
  if (p.barcode) {
    codes.push(`<span class="k">Código de barras</span><b>${escapeHtml(p.barcode)}</b>`);
  }
  if (p.sku) codes.push(`<span class="k">SKU</span>${escapeHtml(p.sku)}`);
  (p.alt_codes || []).forEach((c) => {
    if (String(c).toUpperCase() !== String(p.barcode).toUpperCase()) {
      codes.push(`<span class="k">Código adicional</span>${escapeHtml(c)}`);
    }
  });
  (p.variations || []).forEach((v) => {
    const precio = v.price ? ` · ${fmtMoney(v.price)}` : '';
    const nombre = v.name ? ` (${escapeHtml(v.name)})` : '';
    codes.push(`<span class="k">Variación</span>${escapeHtml(v.code)}${nombre}${precio}`);
  });
  const codesHtml = codes.length
    ? `<div class="d-codes">${codes.join('<span class="sep">|</span>')}</div>`
    : '';

  const loc = (p.location || '').trim();
  const locHtml = loc
    ? `<div class="d-location">${escapeHtml(loc)}</div>`
    : '<div class="d-location none">Sin ubicación</div>';

  const stockClass = Number(p.stock) <= 0 ? ' zero' : '';

  const grid = `
    <div class="d-box">
      <div class="d-label">Existencia (histórica)</div>
      <div class="d-value d-stock${stockClass}">${fmtQty(p.stock)}</div>
    </div>
    <div class="d-box">
      <div class="d-label">Ubicación en tienda</div>
      ${locHtml}
    </div>
    <div class="d-box">
      <div class="d-label">Precio</div>
      <div class="d-value d-price">${fmtMoney(p.price)}</div>
    </div>
    <div class="d-box">
      <div class="d-label">Costo</div>
      <div class="d-value small">${fmtMoney(p.cost)}</div>
    </div>
    <div class="d-box">
      <div class="d-label">Promoción</div>
      <div class="d-value small">${p.promo ? fmtMoney(p.promo) : '—'}</div>
    </div>
    <div class="d-box">
      <div class="d-label">Última venta</div>
      <div class="d-value small">${p.last_sold ? fmtDate(p.last_sold) : 'Sin ventas'}</div>
    </div>
    <div class="d-box">
      <div class="d-label">Unidades vendidas</div>
      <div class="d-value small mono">${fmtQty(p.sold)}</div>
    </div>
    <div class="d-box">
      <div class="d-label">Importe vendido</div>
      <div class="d-value small mono">${fmtMoney(p.sales_total)}</div>
    </div>
  `;

  let dupHtml = '';
  if (Array.isArray(options) && options.length > 1) {
    const buttons = options
      .map((o) => `<button type="button" data-id="${escapeHtml(o.id)}">${escapeHtml(o.name)}</button>`)
      .join('');
    dupHtml = `<div class="d-dup"><b>Este código está en ${options.length} productos.</b>
      Elige el correcto: ${buttons}</div>`;
  }

  detail.innerHTML = `
    <div class="d-head">
      <h2 class="d-name">${escapeHtml(p.name || 'Sin nombre')}</h2>
      <div class="badges">${badges.join('')}</div>
    </div>
    <div class="d-cat">${meta.join(' · ')}</div>
    ${codesHtml}
    <div class="d-grid">${grid}</div>
    ${dupHtml}
    <div class="d-extra" id="d-extra">
      <p class="loading">Cargando historial del producto…</p>
    </div>
  `;

  if (dupHtml) {
    detail.querySelectorAll('.d-dup button').forEach((btn) => {
      btn.addEventListener('click', () => {
        const chosen = options.find((o) => String(o.id) === btn.dataset.id);
        if (chosen) {
          renderDetail(chosen, 'found', null);
          loadExtra(chosen.id);
        }
      });
    });
  }

  markActiveRow(p.id);
  loadExtra(p.id);
}

function hideDetail() {
  detail.hidden = true;
  detail.innerHTML = '';
  detail.className = 'detail';
  delete detail.dataset.id;
}

function markActiveRow(id) {
  tbody.querySelectorAll('tr').forEach((tr) => {
    tr.classList.toggle('active', String(tr.dataset.id) === String(id));
  });
}

function tableSection(title, heads, rows, emptyText) {
  if (!rows || !rows.length) {
    return `<h3>${escapeHtml(title)}</h3><p class="none-sub">${escapeHtml(emptyText)}</p>`;
  }
  const th = heads.map((h) => `<th>${escapeHtml(h)}</th>`).join('');
  const body = rows.map((cells) => {
    const tds = cells.map((c) => {
      if (c && typeof c === 'object') {
        return `<td class="${c.cls || ''}">${c.html}</td>`;
      }
      return `<td>${escapeHtml(c)}</td>`;
    }).join('');
    return `<tr>${tds}</tr>`;
  }).join('');
  return `<h3>${escapeHtml(title)}</h3>
    <table class="mini-table">
      <thead><tr>${th}</tr></thead>
      <tbody>${body}</tbody>
    </table>`;
}

async function loadExtra(id) {
  const box = document.getElementById('d-extra');
  if (!box) return;
  let data;
  try {
    data = await api('/api/item?id=' + encodeURIComponent(id));
  } catch (_) {
    box.innerHTML = '<p class="none-sub">No se pudo cargar el historial del producto.</p>';
    return;
  }
  if (!document.getElementById('d-extra') || String(detail.dataset.id) !== String(id)) {
    return; // el usuario ya seleccionó otro producto
  }
  if (!data.ok) {
    box.innerHTML = '<p class="none-sub">No se pudo cargar el historial del producto.</p>';
    return;
  }

  const it = data.item;

  const precios = (it.price_history || []).map((r) => [
    fmtDate(r.at),
    { cls: 'num', html: fmtMoney(r.price) },
    { cls: 'num', html: fmtMoney(r.cost) },
    r.employee || '—',
  ]);

  const movimientos = (it.movements || []).map((r) => [
    fmtDate(r.at),
    { cls: 'num', html: fmtSigned(r.change) },
    { cls: 'num', html: fmtQty(r.current) },
    r.comment || '—',
    r.employee || '—',
  ]);

  const ventas = (it.recent_sales || []).map((r) => [
    fmtDate(r.at),
    `#${r.sale_id}`,
    { cls: 'num', html: fmtQty(r.qty) },
    { cls: 'num', html: fmtMoney(r.price) },
    { cls: 'num', html: fmtMoney(r.total) },
  ]);

  box.innerHTML = [
    tableSection(
      'Historial de precios (últimos cambios)',
      ['Fecha', 'Precio', 'Costo', 'Usuario'],
      precios,
      'Sin cambios de precio registrados.'
    ),
    tableSection(
      'Últimos movimientos de existencia',
      ['Fecha', 'Cambio', 'Existencia', 'Motivo', 'Usuario'],
      movimientos,
      'Sin movimientos registrados.'
    ),
    tableSection(
      'Últimas ventas',
      ['Fecha', 'Ticket', 'Cant.', 'Precio', 'Total'],
      ventas,
      'Sin ventas registradas.'
    ),
  ].join('');
}

// ------------------------------------------------------------------ resultados
function showEmpty(title, sub) {
  empty.innerHTML = `<strong>${escapeHtml(title)}</strong>${escapeHtml(sub || '')}`;
  empty.hidden = false;
  tbody.innerHTML = '';
}

function renderResults(data) {
  tbody.innerHTML = '';

  if (!data.query) {
    countEl.textContent = '';
    truncatedEl.textContent = '';
    hideDetail();
    showEmpty(
      'Escanea o busca un producto del inventario viejo',
      'Código de barras, SKU, código adicional o nombre. Se incluyen los productos eliminados.'
    );
    return;
  }

  const rows = data.results || [];
  countEl.textContent = data.total === 1 ? '1 resultado' : `${data.total.toLocaleString('es-MX')} resultados`;
  truncatedEl.textContent = data.total > data.returned
    ? `Mostrando los primeros ${data.returned}. Refina la búsqueda.`
    : '';

  if (!rows.length) {
    showEmpty(
      `Sin resultados para “${data.query}”`,
      'Revisa el código escaneado o intenta con otra parte del nombre.'
    );
    return;
  }

  empty.hidden = true;

  rows.forEach((p) => {
    const tr = document.createElement('tr');
    tr.dataset.id = p.id;

    const st = stateInfo(p);
    const loc = (p.location || '').trim();
    const locHtml = loc
      ? `<span class="loc-badge">${escapeHtml(loc)}</span>`
      : '<span class="loc-badge none">Sin ubicación</span>';

    const stockClass = Number(p.stock) <= 0 ? ' stock-zero' : '';
    const sub = [];
    if (p.sku) sub.push('SKU ' + p.sku);
    if (p.deleted) sub.push('Eliminado en 1box');
    else if (p.inactive) sub.push('Inactivo');
    if (p.alt_codes && p.alt_codes.length) sub.push('Cód. adicional: ' + p.alt_codes.join(' · '));

    tr.innerHTML = `
      <td class="c-name">${escapeHtml(p.name)}
        ${sub.length ? `<div class="row-sub">${escapeHtml(sub.join(' · '))}</div>` : ''}
      </td>
      <td class="c-state"><span class="state ${st.cls}">${st.text}</span></td>
      <td class="c-loc">${locHtml}</td>
      <td class="c-price">${fmtMoney(p.price)}</td>
      <td class="c-stock${stockClass}">${fmtQty(p.stock)}</td>
      <td class="c-code">${escapeHtml(p.barcode || p.sku || '—')}</td>
    `;

    tr.addEventListener('click', () => {
      renderDetail(p, 'found', null);
    });

    tbody.appendChild(tr);
  });
}

function renderNotFound(q) {
  detail.className = 'detail notfound';
  detail.hidden = false;
  detail.innerHTML = `
    <h2 class="d-name">No está en el inventario viejo</h2>
    <div class="d-cat">No se encontró “${escapeHtml(q)}” en historico_1box.db.</div>
  `;
}

// ------------------------------------------------------------------ búsqueda
async function runSearch(explicit) {
  const q = input.value.trim();
  const inc = incluirEl.checked ? 1 : 0;

  if (q === lastQuery && inc === lastDeleted && lastQuery !== null) return null;
  lastQuery = q;
  lastDeleted = inc;

  if (!q) {
    renderResults({ query: '', results: [], total: 0, returned: 0 });
    lastExactKey = null;
    return null;
  }

  try {
    const data = await api(
      `/api/search?q=${encodeURIComponent(q)}&eliminados=${inc}`
    );

    if (data.loading) return null;

    if (data.ok === false) {
      statusEl.classList.add('error');
      statusEl.textContent = 'Error en la base histórica: ' + (data.error || 'desconocido');
      return null;
    }

    renderResults(data);

    if (data.exact) {
      const key = String(data.exact.id);
      renderDetail(data.exact, 'found', data.exact_options || []);
      if (key !== lastExactKey) {
        lastExactKey = key;
        beep(true);
      }
    } else {
      lastExactKey = null;
      if (explicit && !data.total) {
        beep(false);
        renderNotFound(q);
      } else {
        hideDetail();
      }
    }
    return data;
  } catch (_) {
    statusEl.classList.add('error');
    statusEl.textContent = 'Servidor local no disponible';
    return null;
  }
}

function scheduleSearch() {
  clearTimeout(timer);
  timer = setTimeout(() => runSearch(false), 130);
}

// ------------------------------------------------------------------ eventos
input.addEventListener('input', () => {
  lastQuery = null;
  scheduleSearch();
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    e.preventDefault();
    input.value = '';
    lastQuery = null;
    runSearch(false);
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    clearTimeout(timer);
    lastQuery = null;
    runSearch(true);
    return;
  }
  if (e.key === 'F2') {
    e.preventDefault();
    input.focus();
    input.select();
  }
});

clearBtn.addEventListener('click', () => {
  input.value = '';
  lastQuery = null;
  input.focus();
  runSearch(false);
});

incluirEl.addEventListener('change', () => {
  lastQuery = null;
  if (input.value.trim()) runSearch(false);
});

// ---- pestañas y terceros ----
document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => setTab(btn.dataset.tab));
});

input3.addEventListener('input', () => {
  lastQuery3 = null;
  scheduleSearchThirds();
});

input3.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    e.preventDefault();
    input3.value = '';
    lastQuery3 = null;
    runSearchThirds(false);
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    clearTimeout(timer3);
    lastQuery3 = null;
    runSearchThirds(true);
    return;
  }
  if (e.key === 'F2') {
    e.preventDefault();
    input3.focus();
    input3.select();
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    const rows = [...tbody3.querySelectorAll('tr')];
    if (!rows.length) return;
    e.preventDefault();
    selectedRow3 = Math.max(0, Math.min(rows.length - 1,
      selectedRow3 + (e.key === 'ArrowDown' ? 1 : -1)));
    rows.forEach((r, i) => r.classList.toggle('active', i === selectedRow3));
    rows[selectedRow3].scrollIntoView({ block: 'nearest' });
  }
});

clearBtn3.addEventListener('click', () => {
  input3.value = '';
  lastQuery3 = null;
  hideThirdDetail();
  input3.focus();
  runSearchThirds(false);
});

tipoEl3.addEventListener('change', () => {
  lastQuery3 = null;
  selectedRow3 = 0;
  hideThirdDetail();
  runSearchThirds(false);
});

incluirEl3.addEventListener('change', () => {
  lastQuery3 = null;
  runSearchThirds(false);
});

// Si el foco se pierde y llega un escaneo, recuperamos el primer carácter.
document.addEventListener('keydown', (e) => {
  const active = activeTab === 'terceros' ? input3 : input;
  if (document.activeElement === active) return;
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.key.length === 1) {
    active.focus();
    active.value += e.key;
    if (activeTab === 'terceros') {
      lastQuery3 = null;
      scheduleSearchThirds();
    } else {
      lastQuery = null;
      scheduleSearch();
    }
    e.preventDefault();
  } else if (e.key === 'F2') {
    e.preventDefault();
    active.focus();
  }
});

window.addEventListener('focus', () => {
  if (ready) (activeTab === 'terceros' ? input3 : input).focus();
});

// ------------------------------------------------------------------ terceros
const TIPOS_LABEL = {
  cliente: 'Cliente',
  proveedor: 'Proveedor',
  fabricante: 'Marca',
  persona: 'Persona',
};

function tipoBadge(tipo) {
  const cls = {
    cliente: 'ok', proveedor: 'warn', fabricante: 'neutral', persona: 'neutral',
  }[tipo] || 'neutral';
  return `<span class="state ${cls}">${TIPOS_LABEL[tipo] || tipo}</span>`;
}

function renderThirdDetail(t) {
  detail3.className = 'detail found' + (t.eliminado ? ' deleted-item' : '');
  detail3.hidden = false;

  const badges = [tipoBadge(t.tipo)];
  if (t.eliminado) badges.push('<span class="badge neutral">Eliminado en 1box</span>');

  const meta = [];
  if (t.razon_social && t.razon_social !== t.nombre) {
    meta.push(`<b>Razón social</b> ${escapeHtml(t.razon_social)}`);
  }
  if (t.cuenta) meta.push(`<b>Cuenta</b> ${escapeHtml(t.cuenta)}`);
  if (t.rfc) meta.push(`<b>RFC</b> ${escapeHtml(t.rfc)}`);
  meta.push(`<b>ID 1box</b> ${escapeHtml(t.id)}`);

  const contact = [];
  if (t.contacto) contact.push(`<b>Teléfono</b> ${escapeHtml(t.contacto)}`);
  if (t.email) contact.push(`<b>Correo</b> ${escapeHtml(t.email)}`);
  if (t.direccion) contact.push(`<b>Dirección</b> ${escapeHtml(t.direccion)}`);

  const saldo = Number(t.saldo) || 0;
  const saldoCls = saldo > 0 ? 'saldo-pos' : (saldo < 0 ? 'saldo-neg' : '');

  const grid = `
    <div class="d-box">
      <div class="d-label">Saldo</div>
      <div class="d-value d-price ${saldoCls}">${fmtMoney(saldo)}</div>
    </div>
    <div class="d-box">
      <div class="d-label">Operaciones</div>
      <div class="d-value small mono">${Number(t.operaciones || 0).toLocaleString('es-MX')}</div>
    </div>
    <div class="d-box">
      <div class="d-label">Importe acumulado</div>
      <div class="d-value small mono">${fmtMoney(t.importe)}</div>
    </div>
    <div class="d-box">
      <div class="d-label">Última operación</div>
      <div class="d-value small">${t.ultima ? fmtDate(t.ultima) : 'Sin operaciones'}</div>
    </div>
    ${t.limite ? `<div class="d-box">
      <div class="d-label">Límite de crédito</div>
      <div class="d-value small">${fmtMoney(t.limite)}</div>
    </div>` : ''}
    ${t.puntos ? `<div class="d-box">
      <div class="d-label">Puntos</div>
      <div class="d-value small mono">${fmtQty(t.puntos)}</div>
    </div>` : ''}
  `;

  const contactHtml = contact.length
    ? `<div class="d-codes">${contact.map((c) => `<span class="k">${c}</span>`).join('<span class="sep">|</span>')}</div>`
    : '';

  const extraInfo = t.extra ? `<div class="d-cat">${escapeHtml(t.extra)}</div>` : '';

  detail3.innerHTML = `
    <div class="d-head">
      <h2 class="d-name">${escapeHtml(t.nombre || 'Sin nombre')}</h2>
      <div class="badges">${badges.join('')}</div>
    </div>
    <div class="d-cat">${meta.join(' · ')}</div>
    ${extraInfo}
    ${contactHtml}
    <div class="d-grid">${grid}</div>
    <div id="d-extra3">
      <p class="loading">Cargando historial…</p>
    </div>
  `;

  loadThirdExtra(t.key);
}

function hideThirdDetail() {
  detail3.hidden = true;
  detail3.innerHTML = '';
  detail3.className = 'detail';
}

async function loadThirdExtra(key) {
  const box = document.getElementById('d-extra3');
  if (!box) return;
  let data;
  try {
    data = await api('/api/tercero?key=' + encodeURIComponent(key));
  } catch (_) {
    box.innerHTML = '<p class="none-sub">No se pudo cargar el historial.</p>';
    return;
  }
  if (!document.getElementById('d-extra3')) return;
  if (!data.ok) {
    box.innerHTML = '<p class="none-sub">No se pudo cargar el historial.</p>';
    return;
  }
  const t = data.tercero;

  const historial = (t.historial || []).map((r) => [
    fmtDate(r.at),
    r.ref || '—',
    { cls: 'num', html: fmtMoney(r.total) },
    r.pago || '—',
    r.detalle || '—',
  ]);

  const cuenta = (t.cuenta_corriente || []).map((r) => [
    fmtDate(r.at),
    r.ref || '—',
    { cls: 'num', html: fmtMoney(r.monto) },
    { cls: 'num', html: fmtMoney(r.saldo) },
    r.detalle || '—',
  ]);

  const articulos = (t.articulos || []).map((r) => [
    escapeHtml(r.nombre || ''),
    escapeHtml(r.sku || '—'),
    { cls: 'num', html: fmtMoney(r.precio) },
    { cls: 'num', html: fmtMoney(r.costo) },
  ]);

  box.innerHTML = [
    tableSection(
      'Últimas operaciones',
      ['Fecha', 'Folio', 'Importe', 'Forma de pago', 'Comentario'],
      historial,
      'Sin operaciones registradas.'
    ),
    tableSection(
      'Movimientos de cuenta corriente',
      ['Fecha', 'Folio', 'Movimiento', 'Saldo', 'Comentario'],
      cuenta,
      'Sin movimientos en cuenta corriente.'
    ),
    tableSection(
      'Artículos relacionados',
      ['Artículo', 'SKU', 'Precio', 'Costo'],
      articulos,
      'Sin artículos relacionados.'
    ),
  ].join('');
}

function showEmpty3(title, sub) {
  empty3.innerHTML = `<strong>${escapeHtml(title)}</strong>${escapeHtml(sub || '')}`;
  empty3.hidden = false;
  tbody3.innerHTML = '';
}

function renderThirds(data) {
  tbody3.innerHTML = '';
  const rows = data.results || [];

  if (!data.query && !data.tipo || data.tipo === 'todos') {
    // sin filtro: se listan todos
  }

  countEl3.textContent = data.total === 1
    ? '1 registro'
    : `${Number(data.total || 0).toLocaleString('es-MX')} registros`;
  const counts = data.counts || {};
  const resumen = Object.keys(counts)
    .filter((k) => counts[k])
    .map((k) => `${counts[k]} ${TIPOS_LABEL[k].toLowerCase()}${counts[k] === 1 ? '' : 's'}`)
    .join(' · ');
  truncatedEl3.textContent = resumen || '';

  if (!rows.length) {
    showEmpty3(
      'Sin resultados',
      'Escribe un nombre, teléfono, correo o número de cuenta.'
    );
    return;
  }

  empty3.hidden = true;

  rows.forEach((t, i) => {
    const tr = document.createElement('tr');
    tr.dataset.key = t.key;

    const sub = [];
    if (t.contacto) sub.push(t.contacto);
    if (t.email) sub.push(t.email);
    if (t.direccion) sub.push(t.direccion);
    if (t.cuenta) sub.push('Cuenta ' + t.cuenta);

    const saldo = Number(t.saldo) || 0;
    const saldoCls = saldo > 0 ? 'saldo-pos' : (saldo < 0 ? 'saldo-neg' : '');

    tr.innerHTML = `
      <td class="c-name">${escapeHtml(t.nombre || 'Sin nombre')}
        ${sub.length ? `<div class="row-sub">${escapeHtml(sub.join(' · '))}</div>` : ''}
      </td>
      <td class="c-state">${tipoBadge(t.tipo)}${t.eliminado
        ? '<div class="row-sub">Eliminado</div>' : ''}</td>
      <td class="c-contact">${escapeHtml(t.extra || '')}</td>
      <td class="c-price ${saldoCls}">${fmtMoney(saldo)}</td>
      <td class="c-stock">${Number(t.operaciones || 0).toLocaleString('es-MX')}</td>
      <td class="c-price">${fmtMoney(t.importe)}</td>
      <td class="c-code">${t.ultima ? fmtDate(t.ultima) : '—'}</td>
    `;

    tr.addEventListener('click', () => {
      selectedRow3 = i;
      tbody3.querySelectorAll('tr').forEach((x) => x.classList.remove('active'));
      tr.classList.add('active');
      renderThirdDetail(t);
    });

    tbody3.appendChild(tr);
  });

  if (selectedRow3 >= rows.length) selectedRow3 = 0;
}

async function runSearchThirds(explicit) {
  const q = input3.value.trim();
  const tipo = tipoEl3.value;
  const inc = incluirEl3.checked ? 1 : 0;

  if (q === lastQuery3 && tipo === lastType3 && inc === lastDeleted3 && lastQuery3 !== null) {
    return null;
  }
  lastQuery3 = q;
  lastType3 = tipo;
  lastDeleted3 = inc;

  try {
    const data = await api(
      `/api/terceros?q=${encodeURIComponent(q)}&tipo=${encodeURIComponent(tipo)}&eliminados=${inc}`
    );

    if (data.loading) return null;
    if (data.ok === false) {
      statusEl.classList.add('error');
      statusEl.textContent = 'Error en la base histórica: ' + (data.error || 'desconocido');
      return null;
    }

    renderThirds(data);

    if (explicit && !data.total) beep(false);
    return data;
  } catch (_) {
    statusEl.classList.add('error');
    statusEl.textContent = 'Servidor local no disponible';
    return null;
  }
}

function scheduleSearchThirds() {
  clearTimeout(timer3);
  timer3 = setTimeout(() => runSearchThirds(false), 130);
}

function setTab(name) {
  activeTab = name;
  document.querySelectorAll('.tab').forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.getElementById('tab-productos').hidden = name !== 'productos';
  document.getElementById('tab-terceros').hidden = name !== 'terceros';
  if (name === 'terceros') {
    input3.focus();
    runSearchThirds(false);
  } else {
    input.focus();
  }
}

// ------------------------------------------------------------------ estado
async function refreshStatus() {
  try {
    const s = await api('/api/status');

    if (s.loading) {
      statusEl.classList.remove('error');
      statusEl.textContent = 'Cargando historico_1box.db…';
      input.disabled = true;
      setTimeout(refreshStatus, 700);
      return;
    }

    if (!s.ok) {
      statusEl.classList.add('error');
      statusEl.textContent = (s.error || 'No se pudo leer historico_1box.db');
      loadedEl.textContent = s.db_file || '';
      return;
    }

    ready = true;
    input.disabled = false;
    input3.disabled = false;
    statusEl.classList.remove('error');
    redTag.hidden = !s.allow_lan;
    statusEl.textContent = `${s.products.toLocaleString('es-MX')} productos · `
      + `${s.with_stock.toLocaleString('es-MX')} con existencia`;
    loadedEl.textContent = s.db_updated
      ? `Base del ${fmtDate(s.db_updated)} · cargada ${fmtDate(s.loaded_at)}`
      : '';
    if (s.third_parties) {
      loadedEl.textContent += ` · ${s.third_parties.toLocaleString('es-MX')} registros de terceros`;
    }

    if (!firstFocus) {
      firstFocus = true;
      input.focus();
    }
  } catch (_) {
    statusEl.classList.add('error');
    statusEl.textContent = 'Servidor local no disponible';
    setTimeout(refreshStatus, 1500);
  }
}

window.addEventListener('DOMContentLoaded', () => {
  refreshStatus();
  renderResults({ query: '', results: [], total: 0, returned: 0 });
  renderThirds({ query: '', results: [], total: 0, returned: 0, counts: {} });
});

if (document.readyState !== 'loading') {
  refreshStatus();
  renderResults({ query: '', results: [], total: 0, returned: 0 });
  renderThirds({ query: '', results: [], total: 0, returned: 0, counts: {} });
}
