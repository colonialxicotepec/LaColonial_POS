'use strict';

const input = document.getElementById('q');
const tbody = document.getElementById('tbody');
const detail = document.getElementById('detail');
const empty = document.getElementById('empty');
const count = document.getElementById('count');
const truncated = document.getElementById('truncated');
const statusEl = document.getElementById('status');
const loadedEl = document.getElementById('loaded');
const clearBtn = document.getElementById('clear');

let timer = null;
let lastQuery = null;
let lastExactKey = null;
let audioCtx = null;

// ---------- utilidades ----------
const fmtMoney = (n) =>
  Number(n || 0).toLocaleString('es-MX', { style: 'currency', currency: 'MXN' });

const fmtQty = (n) => {
  const v = Number(n || 0);
  return Number.isInteger(v)
    ? v.toLocaleString('es-MX')
    : v.toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
    osc.stop(now + (found ? 0.08 : 0.18));
  } catch (_) { /* sin audio, no pasa nada */ }
}

// ---------- ficha ----------
function renderDetail(p, kind) {
  detail.className = 'detail' + (kind ? ' ' + kind : '');
  detail.hidden = false;

  const loc = (p.location || '').trim();
  const locationHtml = loc
    ? `<div class="d-location">${escapeHtml(loc)}</div>`
    : '<div class="d-location none">Sin ubicación registrada</div>';

  const stockClass = Number(p.stock) <= 0 ? ' zero' : '';
  const code = p.barcode || p.sku || '—';

  const itemNumber = (p.item_number || '').trim();
  const altCodes = (Array.isArray(p.alt_codes) ? p.alt_codes : [])
    .filter(Boolean)
    .filter((c) => c.trim().toUpperCase() !== itemNumber.toUpperCase());
  const codesParts = [];
  if (itemNumber) codesParts.push(`UPC/EAN/ISBN: ${escapeHtml(itemNumber)}`);
  if (altCodes.length) codesParts.push(`Adicionales: ${altCodes.map(escapeHtml).join(' · ')}`);
  const codesHtml = codesParts.length
    ? `<div class="d-codes">${codesParts.join('<span class="sep">|</span>')}</div>`
    : '';

  detail.innerHTML = `
    <h2 class="d-name">${escapeHtml(p.name)}</h2>
    <div class="d-cat">${escapeHtml(p.category || 'Sin categoría')} · SKU ${escapeHtml(p.sku || '—')}</div>
    ${codesHtml}
    <div class="d-grid">
      <div class="d-box">
        <div class="d-label">Ubicación en tienda</div>
        ${locationHtml}
      </div>
      <div class="d-box">
        <div class="d-label">Precio</div>
        <div class="d-value d-price">${fmtMoney(p.price)}</div>
      </div>
      <div class="d-box">
        <div class="d-label">Stock</div>
        <div class="d-value d-stock${stockClass}">${fmtQty(p.stock)}</div>
      </div>
      <div class="d-box">
        <div class="d-label">Código</div>
        <div class="d-value small">${escapeHtml(code)}</div>
      </div>
    </div>
  `;
}

function hideDetail() {
  detail.hidden = true;
  detail.innerHTML = '';
  detail.className = 'detail';
}

// ---------- resultados ----------
function renderResults(data) {
  tbody.innerHTML = '';
  const rows = data.results || [];

  if (!data.query) {
    hiddenEmpty('Escribe o escanea un producto', 'Puedes buscar por código de barras, SKU o nombre.');
    count.textContent = '';
    truncated.textContent = '';
    hideDetail();
    return;
  }

  count.textContent = data.total === 1 ? '1 resultado' : `${data.total} resultados`;
  truncated.textContent = data.total > data.returned
    ? `Mostrando los primeros ${data.returned}. Refina la búsqueda.`
    : '';

  if (!rows.length) {
    hiddenEmpty(`Sin resultados para “${data.query}”`,
      'Revisa el código escaneado o busca por una parte del nombre.');
    hideDetail();
    return;
  }

  empty.hidden = true;

  rows.forEach((p, i) => {
    const tr = document.createElement('tr');
    if (i === 0) tr.classList.add('active');

    const loc = (p.location || '').trim();
    const locHtml = loc
      ? `<span class="loc-badge">${escapeHtml(loc)}</span>`
      : '<span class="loc-badge none">Sin ubicación</span>';

    const stockClass = Number(p.stock) <= 0 ? ' stock-zero' : '';
    const altCodes = (p.alt_codes || []).filter(Boolean);
    const altHtml = altCodes.length
      ? `<div class="row-codes">Cód. alt.: ${altCodes.map(escapeHtml).join(' · ')}</div>`
      : '';

    tr.innerHTML = `
      <td class="c-name">${escapeHtml(p.name)}${altHtml}</td>
      <td class="c-loc">${locHtml}</td>
      <td class="c-price">${fmtMoney(p.price)}</td>
      <td class="c-stock${stockClass}">${fmtQty(p.stock)}</td>
      <td class="c-code">${escapeHtml(p.barcode || p.sku || '—')}</td>
    `;

    tr.addEventListener('click', () => {
      tbody.querySelectorAll('tr').forEach((r) => r.classList.remove('active'));
      tr.classList.add('active');
      renderDetail(p, 'found');
    });

    tbody.appendChild(tr);
  });
}

function hiddenEmpty(title, sub) {
  empty.innerHTML = `<strong>${escapeHtml(title)}</strong>${escapeHtml(sub || '')}`;
  empty.hidden = false;
  tbody.innerHTML = '';
}

// ---------- búsqueda ----------
async function runSearch() {
  const q = input.value.trim();
  if (q === lastQuery) return;
  lastQuery = q;

  if (!q) {
    renderResults({ query: '', results: [], total: 0, returned: 0 });
    lastExactKey = null;
    return;
  }

  try {
    const data = await api('/api/search?q=' + encodeURIComponent(q));
    renderResults(data);

    if (data.exact) {
      const key = data.exact.barcode || data.exact.sku;
      renderDetail(data.exact, 'found');
      if (key !== lastExactKey) {
        lastExactKey = key;
        beep(true);
      }
    } else {
      lastExactKey = null;
      hideDetail();
    }
  } catch (err) {
    statusEl.textContent = 'Error de conexión con el buscador';
    statusEl.classList.add('error');
  }
}

function scheduleSearch() {
  clearTimeout(timer);
  timer = setTimeout(runSearch, 120);
}

// ---------- eventos ----------
input.addEventListener('input', () => {
  lastQuery = null;
  scheduleSearch();
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    e.preventDefault();
    input.value = '';
    lastQuery = null;
    runSearch();
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    clearTimeout(timer);
    lastQuery = null;
    runSearch().then(() => {
      if (!detail.hidden && detail.classList.contains('found')) return;
      if (tbody.children.length) {
        const first = tbody.querySelector('tr');
        first.click();
      } else {
        beep(false);
        detail.className = 'detail notfound';
        detail.hidden = false;
        detail.innerHTML = `
          <h2 class="d-name">Producto no encontrado</h2>
          <div class="d-cat">Verifica el código escaneado o intenta por nombre.</div>`;
      }
    });
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
  runSearch();
});

// Si el foco se pierde y llega un escaneo, recuperamos el primer carácter.
document.addEventListener('keydown', (e) => {
  if (document.activeElement === input) return;
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.key.length === 1) {
    input.focus();
    input.value += e.key;
    lastQuery = null;
    scheduleSearch();
    e.preventDefault();
  } else if (e.key === 'F2') {
    e.preventDefault();
    input.focus();
  }
});

// El buscador siempre vuelve a estar listo para el siguiente escaneo.
window.addEventListener('focus', () => input.focus());

// ---------- estado ----------
async function refreshStatus() {
  try {
    const s = await api('/api/status');
    if (s.ok) {
      statusEl.classList.remove('error');
      statusEl.textContent = `${s.products.toLocaleString('es-MX')} productos · base actualizada ${s.db_updated || '—'}`;
      loadedEl.textContent = s.loaded_at ? `Cargado ${s.loaded_at}` : '';
    } else {
      statusEl.classList.add('error');
      statusEl.textContent = 'Base no disponible: ' + (s.error || 'ejecuta la sincronización');
    }
  } catch (_) {
    statusEl.classList.add('error');
    statusEl.textContent = 'Servidor local no disponible';
  }
}

window.addEventListener('DOMContentLoaded', () => {
  input.focus();
  refreshStatus();
  setInterval(refreshStatus, 30000);
});

// también por si el DOM ya estaba listo
if (document.readyState !== 'loading') {
  input.focus();
  refreshStatus();
}
