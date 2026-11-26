'use strict';

/* =====================================================================
   Caja La Colonial - Fase 0
   Carrito en el navegador, cobro y validación en el servidor local.
   ===================================================================== */

const $ = (id) => document.getElementById(id);
const CART_KEY = 'pos_cart_v1';
const TOKEN_KEY = 'pos_token_v1';

const S = {
  token: localStorage.getItem(TOKEN_KEY) || '',
  user: null,
  shift: null,
  today: null,
  store: {},
  printer: {},
  rules: { allow_zero_stock: true, max_cash_input: 50000, max_qty_per_line: 999 },
  security: { manager_required_for_cancel: true },
  catalog: null,
  pending: 0,
  cart: [],
  customer: null,
  customerMode: 'browse',
  customerRows: [],
  customerDetail: null,
  results: [],
  units: [],
  search: null,
  selected: 0,
  method: 'efectivo',
  pin: '',
  busy: false,
  lastSale: null,
  cancelTarget: null,
  lastWarn: '',
  version: '',
  notifications: 0,
  notifRows: [],
  // Impresora local de ESTA PC (agente impresora_local.py). Si está viva,
  // cada ticket sale en la impresora USB conectada a esta computadora.
  localPrinter: { ok: false, name: '', checked: 0 },
};

/* ------------------------------------------------------------ impresora local */
// Cada PC corre su propio agente (impresora_local.py) en 127.0.0.1:8099.
// Si esta PC abrió el POS desde http://127.0.0.1:8099/pos, usa el mismo origen
// (evita bloqueos de Chrome/Edge en red privada).

const AGENTE_URL = (location.port === '8099' && (location.hostname === '127.0.0.1' || location.hostname === 'localhost'))
  ? ''
  : 'http://127.0.0.1:8099';

async function pingImpresoraLocal(forzar) {
  const ahora = Date.now();
  if (!forzar && ahora - (S.localPrinter.checked || 0) < 4000) return S.localPrinter.ok;
  S.localPrinter.checked = ahora;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    const res = await fetch(`${AGENTE_URL}/estado`, {
      cache: 'no-store',
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error('estado ' + res.status);
    const data = await res.json();
    S.localPrinter.ok = !!data.ok;
    S.localPrinter.name = data.impresora || '';
    S.localPrinter.printers = data.impresoras || [];
    S.localPrinter.defaultName = data.predeterminada || '';
  } catch (_) {
    S.localPrinter.ok = false;
    S.localPrinter.name = '';
    S.localPrinter.printers = [];
  }
  renderChipImpresora();
  return S.localPrinter.ok;
}

/** Manda el ticket a la impresora local de esta PC. */
async function imprimirEnLocal(lineas, texto, drawer = false) {
  try {
    const res = await fetch(`${AGENTE_URL}/imprimir`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tipo: lineas && lineas.length ? 'ticket' : 'texto',
        lineas: lineas || null,
        texto: texto || '',
        drawer: !!drawer,
      }),
    });
    const data = await res.json();
    return data;
  } catch (_) {
    return { ok: false, error: 'No hay impresora local en esta computadora.' };
  }
}

/** Envía bytes ESC/POS pre-renderizados al agente local. */
async function imprimirRawLocal(escpos_b64) {
  try {
    const res = await fetch(`${AGENTE_URL}/imprimir-raw`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ escpos_b64 }),
    });
    return await res.json();
  } catch (_) {
    return { ok: false, error: 'No hay impresora local en esta computadora.' };
  }
}

/** Imprime el ticket en la impresora local si la hay; si no, avisa. */
async function imprimirTicketLocal(data) {
  const lineas = data && data.ticket_lines;
  if (!lineas || !lineas.length) return null;
  // Si el servidor ya envió los bytes ESC/POS pre-renderizados, usarlos
  // directamente: el diseño del ticket siempre viene del servidor principal.
  if (data.escpos_b64) {
    const res = await imprimirRawLocal(data.escpos_b64);
    if (res && res.ok) return { ok: true, printer: res.impresora || S.localPrinter.name };
    // Fallback: intentar con las líneas tipadas (versión antigua)
  }
  const isCash = isCashRegister() && !(data.sale && data.sale.status === 'pending');
  const res = await imprimirEnLocal(lineas, data.ticket_text, isCash);
  if (res && res.ok) {
    return { ok: true, printer: res.impresora || S.localPrinter.name };
  }
  return { ok: false, error: (res && res.error) || 'No se pudo imprimir.' };
}

async function openPrinterModal() {
  const ok = await pingImpresoraLocal(true);
  const st = $('printerLocalStatus');
  const sel = $('localPrinterSelect');
  $('printerModalError').textContent = '';
  if (!ok) {
    st.className = 'printstatus bad';
    st.textContent = 'No se detectó impresora_local.py en esta PC. Ejecuta impresora.bat y entra por http://127.0.0.1:8099/pos';
    sel.innerHTML = '<option value="">(Sin agente local)</option>';
    $('localPrinterSaveBtn').disabled = true;
    $('localPrinterTestBtn').disabled = true;
  } else {
    st.className = 'printstatus ok';
    st.textContent = `Agente activo · Impresora actual: ${S.localPrinter.name || '(sin impresora)'}`;
    const list = S.localPrinter.printers || [];
    const opts = ['<option value="">(Automática / predeterminada de Windows)</option>'];
    list.forEach((name) => {
      const selected = name === S.localPrinter.name ? ' selected' : '';
      opts.push(`<option value="${esc(name)}"${selected}>${esc(name)}</option>`);
    });
    sel.innerHTML = opts.join('');
    $('localPrinterSaveBtn').disabled = false;
    $('localPrinterTestBtn').disabled = false;
  }
  openModal('printerModal');
}

async function saveLocalPrinter() {
  const name = $('localPrinterSelect').value;
  $('printerModalError').textContent = '';
  try {
    const res = await fetch(`${AGENTE_URL}/impresora`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ impresora: name }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'No se pudo guardar.');
    S.localPrinter.name = data.impresora || '';
    renderChipImpresora();
    $('printerLocalStatus').className = 'printstatus ok';
    $('printerLocalStatus').textContent = `Impresora guardada: ${S.localPrinter.name || '(automática)'}`;
    toast(`Impresora local: ${S.localPrinter.name || 'automática'}.`, 'ok');
  } catch (err) {
    $('printerModalError').textContent = err.message;
  }
}

/* ------------------------------------------------------------ utilidades */
const fmtMoney = (n) =>
  Number(n || 0).toLocaleString('es-MX', { style: 'currency', currency: 'MXN' });
const fmtQty = (n) => {
  const v = Number(n || 0);
  return Number.isInteger(v) ? v.toLocaleString('es-MX')
    : v.toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const num = (v) => {
  const n = Number(String(v == null ? '' : v).replace(',', '.'));
  return Number.isFinite(n) ? n : 0;
};
const round2 = (n) => Math.round((Number(n) + Number.EPSILON) * 100) / 100;

// Evita que la rueda del ratón cambie el valor de los campos numéricos
// (Chrome/Edge incrementan o decrementan el input al hacer scroll encima).
// La rueda sigue desplazando el panel contenedor; el valor solo cambia con el
// teclado (escritura directa o flechas ↑/↓).
function scrollWheelContainer(el, dy) {
  let p = el.parentElement;
  while (p) {
    const st = getComputedStyle(p);
    if (/(auto|scroll|overlay)/.test(st.overflowY) && p.scrollHeight > p.clientHeight) {
      p.scrollTop += dy;
      return;
    }
    p = p.parentElement;
  }
  window.scrollBy(0, dy);
}

function installNumberWheelLock() {
  document.addEventListener('wheel', (e) => {
    const el = e.target;
    if (!el || el.tagName !== 'INPUT' || el.type !== 'number') return;
    e.preventDefault();
    if (e.deltaY) scrollWheelContainer(el, e.deltaY);
  }, { passive: false });
}

function ticketUrl(sale, withPrint = false) {
  const id = typeof sale === 'object' && sale ? sale.id : sale;
  const token = (typeof sale === 'object' && sale && sale.ticket_token)
    || (S.lastSale && String(S.lastSale.id) === String(id) ? S.lastSale.ticket_token : '');
  const params = new URLSearchParams();
  if (token) params.set('token', token);
  if (withPrint) params.set('print', '1');
  const query = params.toString();
  return `/pos/ticket/${id}${query ? '?' + query : ''}`;
}

// Se carga después de definir num/round2 (loadCart los usa).
S.cart = loadCart();

let audioCtx = null;
function beep(ok = true) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = 'sine';
    osc.frequency.value = ok ? 880 : 220;
    gain.gain.value = 0.05;
    osc.connect(gain).connect(audioCtx.destination);
    const t = audioCtx.currentTime;
    osc.start(t);
    osc.stop(t + (ok ? 0.08 : 0.18));
  } catch (_) { /* sin audio */ }
}

let toastTimer = null;
function toast(message, kind = '') {
  const el = $('toast');
  el.textContent = message;
  el.className = 'toast' + (kind ? ' ' + kind : '');
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

/* ------------------------------------------------------------ carrito */
function loadCart() {
  try {
    const raw = JSON.parse(localStorage.getItem(CART_KEY) || '[]');
    if (!Array.isArray(raw)) return [];
    // `key` es la clave del producto en el inventario que usa la caja
    // (id propio en pos.db). Los carritos guardados antes de la migración
    // no la traen: se descartan en vez de fallar al cobrar.
    return raw.filter((i) => i && i.key !== undefined && i.key !== null
                            && Number.isFinite(Number(i.qty)) && Number(i.qty) > 0)
      .map((i) => ({
        key: String(i.key),
        legacy_id: i.legacy_id != null ? Number(i.legacy_id) : null,
        name: String(i.name || ''),
        sku: String(i.sku || ''),
        barcode: String(i.barcode || ''),
        price: num(i.price),
        qty: Math.max(0.01, round2(Number(i.qty))),
        stock: num(i.stock),
        unit: String(i.unit || 'Pieza'),
        unit_symbol: String(i.unit_symbol || 'pza'),
      }));
  } catch (_) { return []; }
}
function saveCart() {
  try { localStorage.setItem(CART_KEY, JSON.stringify(S.cart)); } catch (_) { /* sin espacio */ }
}
const lineTotal = (i) => round2(Number(i.price || 0) * Number(i.qty || 0));
const maxQty = () => Number(S.rules && S.rules.max_qty_per_line) || 999;
function cartTotal() { return round2(S.cart.reduce((sum, i) => sum + lineTotal(i), 0)); }
function customerTicketLabel() {
  if (!S.customer) return 'PÚBLICO EN GENERAL';
  return `${S.customer.nombre || 'Cliente'}${S.customer.rfc ? ` · RFC: ${S.customer.rfc}` : ''}`;
}
function countText(cart) {
  const groups = {};
  (cart || S.cart).forEach((i) => {
    const unit = i.unit_symbol || 'pza';
    groups[unit] = (groups[unit] || 0) + Number(i.qty || 0);
  });
  return Object.keys(groups)
    .map((unit) => `${fmtQty(round2(groups[unit]))} ${unit}`)
    .join(' · ') || '0';
}

/* ------------------------------------------------------------ API */
async function api(path, body) {
  const opts = { cache: 'no-store', headers: {} };
  if (S.token) opts.headers['X-Pos-Token'] = S.token;
  if (body !== undefined) {
    opts.method = 'POST';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (_) {
    throw new Error('No hay conexión con el servidor local de la caja.');
  }
  let data = {};
  try { data = await res.json(); } catch (_) { /* respuesta sin JSON */ }
  if (res.status === 401 && !path.includes('/login') && !path.includes('/bootstrap')) {
    logoutLocal();
    showLogin('Tu sesión expiró. Vuelve a entrar con tu PIN.');
  }
  if (!res.ok || data.ok === false) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

