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

/** Imprime el ticket en la impresora local si la hay; si no, avisa. */
async function imprimirTicketLocal(data) {
  const lineas = data && data.ticket_lines;
  if (!lineas || !lineas.length) return null;
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

/* ------------------------------------------------------------ sesión */
function logoutLocal() {
  S.token = '';
  S.user = null;
  S.shift = null;
  localStorage.removeItem(TOKEN_KEY);
}

function showLogin(message = '') {
  $('posMain').hidden = true;
  hideOverlays();
  $('loginScreen').hidden = false;
  $('loginPanel').hidden = false;
  $('bootstrapPanel').hidden = true;
  $('loginTitle').textContent = 'Entrar a caja';
  $('loginSub').textContent = 'Escribe tu PIN y presiona Entrar.';
  $('loginError').textContent = message;
  setPin('');
}

function showBootstrap() {
  $('posMain').hidden = true;
  hideOverlays();
  $('loginScreen').hidden = false;
  $('loginPanel').hidden = true;
  $('bootstrapPanel').hidden = false;
  $('bootstrapError').textContent = '';
  setTimeout(() => $('managerName').focus(), 50);
}

function setPin(value) {
  S.pin = String(value || '').slice(0, 8);
  $('pinDots').innerHTML = Array.from({ length: 8 }, (_, i) =>
    `<i class="${i < S.pin.length ? 'on' : ''}"></i>`).join('');
}

async function submitPin() {
  if (S.busy) return;
  const pin = S.pin;
  if (!pin) { shake($('loginScreen').querySelector('.login')); return; }
  S.busy = true;
  $('loginError').textContent = '';
  try {
    const data = await api('/api/pos/login', { pin });
    S.token = data.token;
    localStorage.setItem(TOKEN_KEY, data.token);
    setPin('');
    await refresh();
  } catch (err) {
    setPin('');
    $('loginError').textContent = err.message;
    shake($('loginScreen').querySelector('.login'));
    beep(false);
  } finally {
    S.busy = false;
  }
}

function shake(panel) {
  if (!panel) return;
  panel.classList.remove('shake');
  void panel.offsetWidth;
  panel.classList.add('shake');
}

async function bootstrap() {
  if (S.busy) return;
  const payload = {
    manager_name: $('managerName').value.trim(),
    manager_pin: $('managerPin').value.trim(),
    cashier_name: $('cashierName').value.trim(),
    cashier_pin: $('cashierPin').value.trim(),
  };
  S.busy = true;
  $('bootstrapError').textContent = '';
  try {
    const data = await api('/api/pos/bootstrap', payload);
    S.token = data.token;
    localStorage.setItem(TOKEN_KEY, data.token);
    await refresh();
    toast('Caja configurada. Abre el turno para empezar.', 'ok');
  } catch (err) {
    $('bootstrapError').textContent = err.message;
    shake($('bootstrapPanel'));
  } finally {
    S.busy = false;
  }
}

/* ------------------------------------------------------------ estado */
async function refresh() {
  try {
    const data = await api('/api/pos/state');
    S.user = data.user;
    S.shift = data.shift;
    S.today = data.today;
    S.store = data.store || {};
    S.printer = data.printer || {};
    S.rules = data.rules || S.rules;
    S.security = data.security || S.security;
    S.catalog = data.catalog;
    S.inventory = data.inventory || null;
    S.pending = data.pending || 0;
    S.pendingSales = data.pending_sales || [];
    S.version = data.version || '';
    S.notifications = data.notifications || 0;

    $('loginScreen').hidden = true;
    renderHeader();
    if (!S.shift && S.cart.length) {
      // El carrito pertenece a un turno: si ya no hay turno, no se arrastra.
      S.cart = [];
      saveCart();
    }
    renderCart();
    if (!S.shift) {
      showShiftScreen();
    } else {
      $('shiftScreen').hidden = true;
      $('posMain').hidden = false;
      renderResultsIdle();
      focusScan();
    }
  } catch (err) {
    if (err.message) toast(err.message, 'bad');
  }
}

async function refreshQuiet() {
  if (!S.token || !S.user) return;
  try {
    const data = await api('/api/pos/state');
    S.today = data.today;
    S.shift = data.shift;
    S.pending = data.pending || 0;
    S.pendingSales = data.pending_sales || [];
    S.notifications = data.notifications || 0;
    S.user = data.user;
    renderHeader();
    if (S.shift) renderCart();
    if (!$('pendingModal').hidden) renderPendingModal();
    if (!$('cashModal').hidden) {
      const cash = await api('/api/pos/cash/movements');
      renderCashMovements(cash.shift || S.shift, cash.movements || []);
    }
  } catch (_) { /* silencioso */ }
}

/** Muestra en la cabecera si esta PC tiene impresora local conectada. */
function renderChipImpresora() {
  const chip = $('printerChip');
  if (!chip) return;
  const p = S.localPrinter || {};
  if (p.ok) {
    chip.textContent = `🖨 ${p.name || 'impresora'}`;
    chip.className = 'chip shift-open';
    chip.title = 'Los tickets se imprimen en la impresora de ESTA computadora.';
  } else {
    chip.textContent = '🖨 sin impresora local';
    chip.className = 'chip';
    chip.title = 'No se detectó impresora_local.py en esta computadora: '
      + 'los tickets se imprimen en la impresora de la caja central. '
      + 'Arranca impresora.bat en esta PC.';
  }
}

function renderHeader() {
  const u = S.user;
  const roleLabel = { gerente: 'Gerente', administrador: 'Administrador', cajero: 'Cajero' }[u && u.role] || 'Cajero';
  $('userChip').textContent = u ? `${u.name} · ${roleLabel}` : 'Sin sesión';
  if (S.shift) {
    const opened = (S.shift.opened_at || '').slice(11, 16);
    $('shiftChip').textContent = `Turno #${S.shift.id} · desde ${opened}`;
    $('shiftChip').className = 'chip shift-open';
  } else {
    $('shiftChip').textContent = 'Sin turno abierto';
    $('shiftChip').className = 'chip';
  }
  const t = S.today || { net_total: 0, count: 0 };
  const baseSales = t.net_total != null ? Number(t.net_total) : Number(t.total || 0);
  const shiftCashIn = S.shift ? Number(S.shift.cash_in || 0) : 0;
  const shiftCashOut = S.shift ? Number(S.shift.cash_out || 0) : 0;
  const shiftMoves = shiftCashIn - shiftCashOut;
  const chipTotal = round2(baseSales + shiftMoves);

  $('todayChip').textContent = `Hoy ${fmtMoney(chipTotal)} · ${t.count} ticket${t.count === 1 ? '' : 's'}`;

  let tip = `Ventas netas: ${fmtMoney(baseSales)} (${t.count} tickets)`;
  if (shiftCashIn > 0) tip += ` · Entradas turno: +${fmtMoney(shiftCashIn)}`;
  if (shiftCashOut > 0) tip += ` · Salidas turno: −${fmtMoney(shiftCashOut)}`;
  if (shiftMoves !== 0) tip += ` · Total con entradas/salidas: ${fmtMoney(chipTotal)}`;
  $('todayChip').title = tip;

  renderChipImpresora();
  $('menuVersion').textContent = [
    `Versión ${S.version || '—'}`,
    S.catalog ? `${S.catalog.products.toLocaleString('es-MX')} productos` : '',
    S.inventory && S.inventory.source === 'propio'
      ? `inventario propio (${(S.inventory.products.active || 0).toLocaleString('es-MX')} activos)`
      : 'inventario: catálogo de 1box',
    S.printer && S.printer.name ? `Impresora: ${S.printer.name}` : 'Impresora sin configurar',
    S.pending ? `${S.pending} venta(s) registradas en esta caja` : '',
  ].filter(Boolean).join(' · ');

  const manager = canManage();
  const cashRegister = isCashRegister();
  $('invBtn').hidden = !manager;
  $('unitsBtn').hidden = !manager;
  $('usersBtn').hidden = !isAdmin();
  $('closeShiftBtn').hidden = !cashRegister;
  if ($('cashMoveBtn')) $('cashMoveBtn').hidden = !cashRegister;
  if ($('cashMoveMenuBtn')) $('cashMoveMenuBtn').hidden = !cashRegister;

  const pendingList = S.pendingSales || [];
  const pendingCount = pendingList.length;
  if ($('pendingBtn')) {
    $('pendingBtn').hidden = !cashRegister;
    $('pendingBtn').classList.toggle('has-alerts', pendingCount > 0);
  }
  if ($('pendingBadge')) {
    $('pendingBadge').hidden = pendingCount === 0;
    $('pendingBadge').textContent = pendingCount > 99 ? '99+' : String(pendingCount);
  }
  if ($('pendingCartBtn')) {
    $('pendingCartBtn').hidden = !cashRegister;
  }
  if ($('pendingCartBadge')) {
    $('pendingCartBadge').hidden = pendingCount === 0;
    $('pendingCartBadge').textContent = pendingCount > 99 ? '99+' : String(pendingCount);
  }

  if (cashRegister) {
    if (!S.cart.length && pendingCount > 0) {
      $('payBtn').innerHTML = `COBRAR PENDIENTES (${pendingCount}) <kbd>F4</kbd>`;
      $('payBtn').title = 'Ver y cobrar tickets despachados pendientes de pago (F4 / F6)';
    } else {
      $('payBtn').innerHTML = 'COBRAR <kbd>F4</kbd>';
      $('payBtn').title = 'Cobrar en caja (F4)';
    }
  } else {
    $('payBtn').innerHTML = 'DESPACHAR <kbd>F4</kbd>';
    $('payBtn').title = 'Despachar y generar ticket para pagar en caja (F4)';
  }

  const badge = $('notifBadge');
  badge.hidden = !S.notifications;
  badge.textContent = S.notifications > 99 ? '99+' : String(S.notifications);
  $('notifBtn').classList.toggle('has-alerts', S.notifications > 0);
}

/* ------------------------------------------------------------ turno */
function showShiftScreen() {
  $('posMain').hidden = true;
  hideOverlays();
  $('shiftScreen').hidden = false;
  $('shiftError').textContent = '';

  if (isCashRegister()) {
    if ($('shiftScreenTitle')) $('shiftScreenTitle').textContent = 'Abrir turno';
    if ($('shiftScreenSub')) $('shiftScreenSub').hidden = false;
    if ($('shiftOpenFields')) $('shiftOpenFields').hidden = false;
    if ($('shiftSellerWait')) $('shiftSellerWait').hidden = true;
    $('openingCash').value = '0';
    $('openShiftBtn').disabled = false;
    setTimeout(() => { $('openingCash').focus(); $('openingCash').select(); }, 60);
  } else {
    if ($('shiftScreenTitle')) $('shiftScreenTitle').textContent = 'Turno no iniciado';
    if ($('shiftScreenSub')) $('shiftScreenSub').hidden = true;
    if ($('shiftOpenFields')) $('shiftOpenFields').hidden = true;
    if ($('shiftSellerWait')) $('shiftSellerWait').hidden = false;
  }
}

async function openShift() {
  if (S.busy) return;
  const opening = num($('openingCash').value);
  if (opening < 0) { $('shiftError').textContent = 'El fondo no puede ser negativo.'; return; }
  S.busy = true;
  $('shiftError').textContent = '';
  $('openShiftBtn').disabled = true;
  try {
    await api('/api/pos/shift/open', { opening_cash: opening });
    await refresh();
    toast('Turno abierto. Ya puedes cobrar.', 'ok');
  } catch (err) {
    $('shiftError').textContent = err.message;
    $('openShiftBtn').disabled = false;
  } finally {
    S.busy = false;
  }
}

function updateShiftDiff() {
  const expected = num(S.shift && S.shift.expected_cash);
  const counted = num($('countedCash').value);
  const diff = round2(counted - expected);
  const el = $('shiftDiff');
  el.textContent = fmtMoney(diff);
  el.style.color = Math.abs(diff) < 0.005 ? '#14532d' : (diff < 0 ? '#b91c1c' : '#b45309');
}

/** Escribe el corte en una ventana del navegador (para imprimir o consultar). */
function openShiftReportWindow(win, text, store) {
  const w = win || window.open('', '_blank', 'width=520,height=680');
  if (!w) return null;
  w.document.write(`<!doctype html><html><head><title>Corte – ${store}</title>
    <style>body{font-family:monospace;font-size:12px;white-space:pre;padding:12px;background:#fff}
    button{font-family:inherit;font-size:14px;padding:8px 14px;margin-bottom:10px;cursor:pointer}
    @media print{@page{margin:5mm}button{display:none}}</style></head>
    <body><button onclick="window.print()">🖨 Imprimir corte</button>\n${text}</body></html>`);
  w.document.close();
  return w;
}

async function confirmShiftClose() {
  if (S.busy) return;
  const counted = num($('countedCash').value);
  let notes = (($('shiftCloseNotes') && $('shiftCloseNotes').value) || '').trim().slice(0, 300);
  const store = (S.store && S.store.name) || 'La Colonial';

  // Una diferencia grande se confirma y se explica: así el corte queda
  // documentado aunque falte o sobre dinero.
  const expectedNow = num(S.shift && S.shift.expected_cash);
  const diffNow = round2(counted - expectedNow);
  if (Math.abs(diffNow) >= 50) {
    const ok = confirm(
      `La diferencia del turno es de ${fmtMoney(diffNow)}.\n\n`
      + '¿Cerrar el turno de todos modos?'
    );
    if (!ok) return;
    if (!notes) {
      const motivo = prompt('Escribe una nota de cierre que explique la diferencia:');
      if (motivo === null) return;
      notes = String(motivo).trim().slice(0, 300);
      if ($('shiftCloseNotes')) $('shiftCloseNotes').value = notes;
    }
  }

  // Si el corte no se va a imprimir, se abre la ventana ANTES de la llamada
  // (después de un await el navegador puede bloquear la ventana emergente).
  // Si el corte no se va a imprimir en esta PC, se abre la ventana ANTES de
  // cualquier llamada (después de un await el navegador puede bloquearla).
  const cachedLocal = S.localPrinter.ok;
  const autoPrint = !(S.printer && S.printer.print_shift === false) || cachedLocal;
  let win = null;
  if (!autoPrint) win = openShiftReportWindow(null, '', store);

  const local = await pingImpresoraLocal(true);
  S.busy = true;
  $('shiftCloseError').textContent = '';
  $('confirmCloseShift').disabled = true;
  try {
    const denominations = {};
    DENOMS.forEach((d, i) => {
      if (denomCounts[i] > 0) denominations[String(d.value)] = denomCounts[i];
    });
    const data = await api('/api/pos/shift/close', {
      counted_cash: counted,
      notes,
      denominations,
      impresion_local: local,
    });
    S.shift = null;
    S.cart = [];
    saveCart();
    renderCart();
    closeModal('shiftCloseModal');
    const diff = num(data.shift && data.shift.difference);
    const diffText = Math.abs(diff) < 0.005 ? 'sin diferencia' : `diferencia ${fmtMoney(diff)}`;
    const reportText = (data.print && data.print.ticket_text) || '';
    let printed = data.print && data.print.ok;
    let printer = data.print && data.print.printer;
    let printError = (data.print && data.print.error) || '';

    // Con impresora local, el corte se manda a la impresora de ESTA PC.
    if (local && reportText) {
      const res = await imprimirEnLocal(null, reportText);
      if (res && res.ok) {
        printed = true;
        printer = res.impresora || printer;
        printError = '';
      } else {
        printed = false;
        printError = (res && res.error) || printError;
      }
    }

    if (printed) {
      if (win) win.close();
      toast(`Turno cerrado, ${diffText}. Corte impreso${printer ? ' en ' + printer : ''}.`,
        Math.abs(diff) < 0.005 ? 'ok' : 'bad');
    } else if (reportText) {
      const w = win || openShiftReportWindow(null, reportText, store);
      toast(`Turno cerrado, ${diffText}. El corte se abrió en el navegador (${printError || 'sin impresora'}).`,
        Math.abs(diff) < 0.005 ? 'ok' : 'bad');
    } else {
      if (win) win.close();
      toast(`Turno cerrado, ${diffText}. El corte no se pudo imprimir (${printError || 'sin impresora'}).`,
        Math.abs(diff) < 0.005 ? 'ok' : 'bad');
    }
    await refresh();
  } catch (err) {
    if (win) win.close();
    $('shiftCloseError').textContent = err.message;
  } finally {
    S.busy = false;
    $('confirmCloseShift').disabled = false;
  }
}

/* ------------------------------------------------------------ contador de denominaciones */

const DENOMS = [
  { label: '50 centavos', value: 0.50, bill: false },
  { label: '$1',          value: 1,    bill: false },
  { label: '$2',          value: 2,    bill: false },
  { label: '$5',          value: 5,    bill: false },
  { label: '$10',         value: 10,   bill: false },
  { label: '$20',         value: 20,   bill: true  },
  { label: '$50',         value: 50,   bill: true  },
  { label: '$100',        value: 100,  bill: true  },
  { label: '$200',        value: 200,  bill: true  },
  { label: '$500',        value: 500,  bill: true  },
  { label: '$1,000',      value: 1000, bill: true  },
];

/** Pesos máximos a dejar en caja para el día siguiente (rules.fondo_maximo en pos_config.json) */
function fondoLimite() {
  const v = Number(S.rules && S.rules.fondo_maximo);
  return Number.isFinite(v) && v >= 0 ? v : 1400;
}

/** Estado de piezas contadas */
let denomCounts = DENOMS.map(() => 0);

/** Suma total del conteo actual */
function denomTotal() {
  return round2(denomCounts.reduce((s, qty, i) => s + qty * DENOMS[i].value, 0));
}

/** Calcula los billetes a retirar (greedy de mayor a menor, solo billetes) para
 *  dejar exactamente o un poco más del fondo límite en caja.
 *  Devuelve { retiro, detalle:[{label,value,qty,subtotal}], fondoFinal }
 */
function calcRetiro(total, counts, limit) {
  const toRetire = round2(total - limit);
  if (toRetire <= 0) return { retiro: 0, detalle: [], fondoFinal: total };

  const bills = DENOMS.map((d, i) => ({ ...d, available: counts[i] }))
    .filter(d => d.bill)
    .sort((a, b) => b.value - a.value);

  let remaining = toRetire;
  const detalle = [];

  for (const d of bills) {
    if (remaining <= 0) break;
    const maxQty = Math.min(d.available, Math.floor(remaining / d.value));
    if (maxQty > 0) {
      detalle.push({ label: d.label, value: d.value, qty: maxQty, subtotal: maxQty * d.value });
      remaining = round2(remaining - maxQty * d.value);
    }
  }

  const retirado = round2(toRetire - remaining);
  return { retiro: retirado, detalle, fondoFinal: round2(total - retirado) };
}

/** Renderiza la tabla de denominaciones */
function renderDenomGrid() {
  const grid = $('denomGrid');
  if (!grid) return;

  // separar monedas y billetes en dos columnas dentro del grid
  const coins  = DENOMS.filter(d => !d.bill);
  const bills  = DENOMS.filter(d =>  d.bill);

  function colHTML(items, startIdx) {
    return items.map((d, j) => {
      const idx = startIdx + j;
      const sub = round2(denomCounts[idx] * d.value);
      return `<div class="denomrow" data-denom="${idx}">
        <span class="denom-label">${d.label}</span>
        <input class="denom-qty" type="number" inputmode="numeric" min="0" step="1"
               value="${denomCounts[idx] || ''}" placeholder="0" data-idx="${idx}">
        <span class="denom-sub" id="denomSub${idx}">${sub > 0 ? fmtMoney(sub) : ''}</span>
      </div>`;
    }).join('');
  }

  // columna 1: monedas, columna 2: billetes
  grid.innerHTML = `
    <div>
      <div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Monedas</div>
      ${colHTML(coins, 0)}
    </div>
    <div>
      <div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">Billetes</div>
      ${colHTML(bills, coins.length)}
    </div>`;

  // Eventos en cada input
  grid.querySelectorAll('.denom-qty').forEach(input => {
    input.addEventListener('input', () => {
      const idx = parseInt(input.dataset.idx, 10);
      denomCounts[idx] = Math.max(0, parseInt(input.value, 10) || 0);
      updateDenomSummary();
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const all = Array.from(grid.querySelectorAll('.denom-qty'));
        const pos = all.indexOf(input);
        if (pos < all.length - 1) all[pos + 1].focus();
        else $('denomNextBtn') && $('denomNextBtn').focus();
      }
    });
  });
}

/** Actualiza subtotales por denominación, el total y el recuadro de retiro */
function updateDenomSummary() {
  const total = denomTotal();

  DENOMS.forEach((d, i) => {
    const el = $(`denomSub${i}`);
    if (!el) return;
    const sub = round2(denomCounts[i] * d.value);
    el.textContent = sub > 0 ? fmtMoney(sub) : '';
  });

  if ($('denomTotal')) $('denomTotal').textContent = fmtMoney(total);

  const box = $('denomRetirarBox');
  if (!box) return;

  if (total <= fondoLimite()) {
    box.hidden = true;
    return;
  }

  const { retiro, detalle, fondoFinal } = calcRetiro(total, denomCounts, fondoLimite());
  box.hidden = false;
  const titleEl = box.querySelector('.denom-retiro-title');
  if (titleEl) titleEl.textContent = `🏦 Retiro sugerido para mañana (fondo máx. ${fmtMoney(fondoLimite())})`;

  const detalleEl = $('denomRetirarDetalle');
  if (detalleEl) {
    if (detalle.length === 0) {
      detalleEl.innerHTML = '<span style="grid-column:1/-1;color:#92400e">No hay billetes suficientes para completar el retiro exacto.</span>';
    } else {
      detalleEl.innerHTML = detalle.map(r =>
        `<span class="dr-qty">${r.qty} × ${r.label}</span><span class="dr-amt">= ${fmtMoney(r.subtotal)}</span>`
      ).join('');
    }
  }

  const fondoEl = $('denomFondoFinal');
  if (fondoEl) {
    fondoEl.innerHTML = `Retirar: <b>${fmtMoney(retiro)}</b> → Fondo en caja: <b>${fmtMoney(fondoFinal)}</b>`;
  }
}

/** Abre el modal de corte con el contador de denominaciones en el paso 1 */
async function openShiftClose() {
  try {
    const data = await api('/api/pos/state');
    S.shift = data.shift;
    S.today = data.today;
    renderHeader();
  } catch (_) { /* usamos lo último conocido */ }
  if (!S.shift) { toast('No hay turno abierto.'); return; }

  // Reiniciar conteo
  denomCounts = DENOMS.map(() => 0);
  if ($('shiftCloseNotes')) $('shiftCloseNotes').value = '';
  if ($('shiftCloseError')) $('shiftCloseError').textContent = '';

  // Mostrar paso 1, ocultar paso 2
  if ($('denomPanel'))      $('denomPanel').hidden = false;
  if ($('shiftCloseStep2')) $('shiftCloseStep2').hidden = true;

  renderDenomGrid();
  updateDenomSummary();
  openModal('shiftCloseModal');

  // Foco en el primer input
  setTimeout(() => {
    const first = document.querySelector('#denomGrid .denom-qty');
    if (first) first.focus();
  }, 80);
}

/** Genera el texto del ticket de conteo para imprimir / mostrar */
function buildDenomText() {
  const total  = denomTotal();
  const { retiro, detalle, fondoFinal } = calcRetiro(total, denomCounts, fondoLimite());
  const store  = (S.store && S.store.name) || 'La Colonial';
  const width  = 42;
  const line   = '='.repeat(width);
  const thin   = '-'.repeat(width);
  const shift  = S.shift || {};
  const now    = new Date().toLocaleString('es-MX', { hour12: false }).slice(0, 16);

  const rows = [
    line,
    store.toUpperCase().padStart(Math.floor((width + store.length) / 2)).padEnd(width),
    'CONTEO DE DENOMINACIONES'.padStart(Math.floor((width + 25) / 2)).padEnd(width),
    line,
    `Fecha  : ${now}`,
    `Turno  : #${shift.id || '?'} · ${shift.user_name || ''}`,
    thin,
    'MONEDAS',
  ];

  DENOMS.filter(d => !d.bill).forEach((d, j) => {
    const qty = denomCounts[j];
    if (qty > 0) {
      const sub = fmtMoney(qty * d.value);
      rows.push(`  ${d.label.padEnd(14)}× ${String(qty).padStart(4)}  = ${sub.padStart(10)}`);
    }
  });

  rows.push('BILLETES');
  const coinCount = DENOMS.filter(d => !d.bill).length;
  DENOMS.filter(d => d.bill).forEach((d, j) => {
    const qty = denomCounts[coinCount + j];
    if (qty > 0) {
      const sub = fmtMoney(qty * d.value);
      rows.push(`  ${d.label.padEnd(14)}× ${String(qty).padStart(4)}  = ${sub.padStart(10)}`);
    }
  });

  rows.push(thin);
  rows.push(`TOTAL CONTADO  ${fmtMoney(total).padStart(width - 15)}`);
  rows.push(line);

  if (retiro > 0) {
    rows.push(`RETIRO SUGERIDO (fondo máx. ${fmtMoney(fondoLimite())})`);
    rows.push(`  Retirar: ${fmtMoney(retiro)}`);
    detalle.forEach(r => {
      rows.push(`  ${r.qty} × ${r.label} = ${fmtMoney(r.subtotal)}`);
    });
    rows.push(`  → Fondo final en caja: ${fmtMoney(fondoFinal)}`);
    rows.push(line);
  } else {
    rows.push('No es necesario retirar efectivo.');
    rows.push(line);
  }

  return rows.join('\n');
}

/** Imprime el conteo de denominaciones en la impresora térmica local o en el navegador */
async function denomPrint() {
  const text   = buildDenomText();
  const store  = (S.store && S.store.name) || 'La Colonial';

  if (await pingImpresoraLocal()) {
    const res = await imprimirEnLocal(null, text, false);
    if (res && res.ok) {
      toast(`Conteo impreso en ${res.impresora || 'impresora local'}.`, 'ok');
      return;
    }
  }

  const win = window.open('', '_blank', 'width=400,height=600');
  if (!win) { toast('Permitir ventanas emergentes para imprimir.', 'bad'); return; }
  win.document.write(`<!doctype html><html><head><title>Conteo – ${store}</title>
    <style>body{font-family:monospace;font-size:12px;white-space:pre;padding:10px}
    @media print{@page{margin:4mm}button{display:none}}</style></head>
    <body><button onclick="window.print()">🖨 Imprimir</button>\n${text}</body></html>`);
  win.document.close();
  setTimeout(() => win.print(), 400);
}

/** Avanza al Paso 2 (cierre de turno) volcando el total contado */
function denomNext() {
  const total = denomTotal();
  if (total <= 0) toast('Caja sin efectivo: el turno se cerrará con $0.00 contado.', 'bad');

  // Rellenar el campo de efectivo contado con el total del contador
  if ($('countedCash')) {
    $('countedCash').value = total.toFixed(2);
  }

  // Actualizar resumen del turno
  const s = S.shift;
  const expected = num(s && s.expected_cash);
  const pendingCount = Number((s && s.pending_count) || 0);
  const pendingTotal = num(s && s.pending_total);
  if ($('shiftCloseSummary')) {
    $('shiftCloseSummary').innerHTML = `
      <div class="srow"><span>Turno</span><b>#${esc(s.id)} · ${esc(s.user_name || '')}</b></div>
      <div class="srow"><span>Fondo inicial</span><b>${fmtMoney(s.opening_cash)}</b></div>
      <div class="srow"><span>Ventas</span><b>${fmtMoney(s.sales_total)} (${s.sales_count})</b></div>
      <div class="srow"><span>Efectivo (ventas)</span><b>${fmtMoney((s.by_method || {}).efectivo || 0)}</b></div>
      <div class="srow"><span>Tarjeta</span><b>${fmtMoney((s.by_method || {}).tarjeta || 0)}</b></div>
      <div class="srow"><span>Transferencia</span><b>${fmtMoney((s.by_method || {}).transferencia || 0)}</b></div>
      <div class="srow"><span>Devoluciones</span><b>−${fmtMoney(s.returns_total || 0)} (${s.returns_count || 0})</b></div>
      <div class="srow"><span>Entradas de efectivo</span><b class="cash-up">+${fmtMoney(s.cash_in || 0)} (${s.cash_in_count || 0})</b></div>
      <div class="srow"><span>Salidas / gastos</span><b class="cash-down">−${fmtMoney(s.cash_out || 0)} (${s.cash_out_count || 0})</b></div>
      <div class="srow"><span><b>Efectivo esperado</b></span><b>${fmtMoney(expected)}</b></div>
      <div class="srow"><span><b>Contado (por denominaciones)</b></span><b style="color:#1e40af">${fmtMoney(total)}</b></div>
      ${pendingCount > 0
        ? `<div class="srow"><span><b>Pendientes de cobro</b></span><b style="color:#b45309">${fmtMoney(pendingTotal)} (${pendingCount})</b></div>`
        : ''}`;
  }
  if (pendingCount > 0) {
    toast(`${pendingCount} ticket(s) por cobrar (${fmtMoney(pendingTotal)}): `
      + 'no son efectivo de este turno, se cobran en el siguiente.', 'bad');
  }

  // Mostrar paso 2
  if ($('denomPanel'))      $('denomPanel').hidden = true;
  if ($('shiftCloseStep2')) $('shiftCloseStep2').hidden = false;

  if ($('shiftCloseError')) $('shiftCloseError').textContent = '';
  updateShiftDiff();
  setTimeout(() => { $('countedCash').focus(); $('countedCash').select(); }, 60);
}

/** Regresa al paso 1 del contador */
function denomBack() {
  if ($('denomPanel'))      $('denomPanel').hidden = false;
  if ($('shiftCloseStep2')) $('shiftCloseStep2').hidden = true;
}

/* ------------------------------------------------------------ entradas y salidas de efectivo */
function setCashMoveKind(kind) {
  S.cashMoveKind = kind === 'retiro' ? 'retiro' : 'ingreso';
  document.querySelectorAll('#cashModal [data-cashkind]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.cashkind === S.cashMoveKind);
  });
  const saveBtn = $('cashMoveSave');
  if (saveBtn) {
    if (S.editingCashMoveId) {
      saveBtn.textContent = S.cashMoveKind === 'ingreso' ? 'Guardar cambios (entrada)' : 'Guardar cambios (salida)';
    } else {
      saveBtn.textContent = S.cashMoveKind === 'ingreso' ? 'Registrar entrada' : 'Registrar salida';
    }
  }
}

function resetCashMoveForm() {
  S.editingCashMoveId = null;
  if ($('cashFormTitle')) $('cashFormTitle').textContent = 'Registrar movimiento';
  if ($('cashMoveCancelEdit')) $('cashMoveCancelEdit').hidden = true;
  $('cashMoveAmount').value = '';
  $('cashMoveReason').value = '';
  $('cashMoveError').textContent = '';
  setCashMoveKind(S.cashMoveKind || 'ingreso');
}

function startEditCashMovement(id) {
  const m = (S.cashMovements || []).find((r) => Number(r.id) === Number(id));
  if (!m) return;
  S.editingCashMoveId = Number(m.id);
  if ($('cashFormTitle')) $('cashFormTitle').textContent = `Editar movimiento (#${m.id})`;
  if ($('cashMoveCancelEdit')) $('cashMoveCancelEdit').hidden = false;
  setCashMoveKind(m.kind);
  $('cashMoveAmount').value = Math.abs(Number(m.amount) || 0).toFixed(2);
  $('cashMoveReason').value = m.reason || '';
  $('cashMoveError').textContent = '';
  $('cashMoveAmount').focus();
  $('cashMoveAmount').select();
}

async function removeCashMovement(id) {
  const m = (S.cashMovements || []).find((r) => Number(r.id) === Number(id));
  if (!m) return;
  const label = m.kind === 'ingreso' ? 'Entrada' : 'Salida / Gasto';
  const amt = fmtMoney(Math.abs(Number(m.amount) || 0));
  if (!confirm(`¿Eliminar ${label} por ${amt} (${m.reason || 'sin motivo'})?`)) return;
  try {
    const data = await api('/api/pos/cash/delete', { id: Number(m.id) });
    S.shift = data.shift;
    if (data.today) S.today = data.today;
    if (S.editingCashMoveId === Number(m.id)) resetCashMoveForm();
    renderHeader();
    renderCashMovements(data.shift, data.movements);
    toast(`${label} eliminada. Efectivo esperado: ${fmtMoney(data.shift.expected_cash)}.`, 'ok');
  } catch (err) {
    $('cashMoveError').textContent = err.message;
    beep(false);
  }
}

function renderCashMovements(shift, movements) {
  const s = shift || S.shift || {};
  S.cashMovements = movements || [];
  $('cashShiftSummary').innerHTML = `
    <span class="chip">Fondo inicial <b>${fmtMoney(s.opening_cash || 0)}</b></span>
    <span class="chip">Ventas efectivo <b>${fmtMoney((s.by_method || {}).efectivo || 0)}</b></span>
    <span class="chip">Entradas <b class="cash-up">+${fmtMoney(s.cash_in || 0)}</b> (${s.cash_in_count || 0})</span>
    <span class="chip">Salidas / gastos <b class="cash-down">−${fmtMoney(s.cash_out || 0)}</b> (${s.cash_out_count || 0})</span>
    <span class="chip"><b>Efectivo esperado en caja ${fmtMoney(s.expected_cash || 0)}</b></span>`;
  const rows = S.cashMovements;
  const canEdit = canManage();
  $('cashMovesBody').innerHTML = rows.map((m) => {
    const isIn = m.kind === 'ingreso';
    const amt = Math.abs(Number(m.amount) || 0);
    return `
      <tr>
        <td class="code">${esc((m.created_at || '').slice(11, 19))}</td>
        <td>${isIn
          ? '<span class="badge ok">Entrada</span>'
          : '<span class="badge warn">Salida / Gasto</span>'}</td>
        <td>${esc(m.reason || '—')}</td>
        <td>${esc(m.user_name || '')}</td>
        <td class="num ${isIn ? 'cash-up' : 'cash-down'}">${isIn ? '+' : '−'}${fmtMoney(amt)}</td>
        <td class="actions">
          ${canEdit
            ? `<button type="button" class="link" data-cashedit="${m.id}">Editar</button>
               <button type="button" class="link" style="color:var(--red)" data-cashdel="${m.id}">Eliminar</button>`
            : ''}
        </td>
      </tr>`;
  }).join('') || '<tr><td colspan="6" class="muted">Aún no hay entradas ni salidas registradas en este turno.</td></tr>';
}

async function openCashMovements() {
  if (!isCashRegister()) {
    toast('Sólo el gerente o administrador puede registrar entradas y salidas de caja.', 'bad');
    return;
  }
  if (!S.shift) {
    toast('Abre el turno antes de registrar entradas o salidas.', 'bad');
    return;
  }
  S.cashMoveKind = S.cashMoveKind || 'ingreso';
  resetCashMoveForm();
  renderCashMovements(S.shift, []);
  openModal('cashModal');
  setTimeout(() => $('cashMoveAmount').focus(), 60);
  try {
    const data = await api('/api/pos/cash/movements');
    if (data.shift) {
      S.shift = data.shift;
      renderHeader();
    }
    renderCashMovements(data.shift, data.movements);
  } catch (err) {
    $('cashMoveError').textContent = err.message;
  }
}

async function saveCashMovement() {
  if (S.busy) return;
  const kind = S.cashMoveKind === 'retiro' ? 'retiro' : 'ingreso';
  const amount = num($('cashMoveAmount').value);
  const reason = $('cashMoveReason').value.trim();
  $('cashMoveError').textContent = '';
  if (amount <= 0) {
    $('cashMoveError').textContent = 'Escribe un monto mayor a cero.';
    $('cashMoveAmount').focus();
    return;
  }
  if (!reason) {
    $('cashMoveError').textContent = 'Escribe el concepto o motivo del movimiento.';
    $('cashMoveReason').focus();
    return;
  }
  const editingId = S.editingCashMoveId;
  S.busy = true;
  $('cashMoveSave').disabled = true;
  try {
    const endpoint = editingId ? '/api/pos/cash/edit' : '/api/pos/cash';
    const payload = editingId ? { id: editingId, kind, amount, reason } : { kind, amount, reason };
    const data = await api(endpoint, payload);
    S.shift = data.shift;
    if (data.today) S.today = data.today;
    resetCashMoveForm();
    renderHeader();
    renderCashMovements(data.shift, data.movements);
    toast(
      editingId
        ? `Movimiento actualizado. Efectivo esperado: ${fmtMoney(data.shift.expected_cash)}.`
        : `${kind === 'ingreso' ? 'Entrada' : 'Salida'} registrada por ${fmtMoney(amount)}. Efectivo esperado: ${fmtMoney(data.shift.expected_cash)}.`,
      'ok'
    );
    $('cashMoveAmount').focus();
  } catch (err) {
    $('cashMoveError').textContent = err.message;
    beep(false);
  } finally {
    S.busy = false;
    $('cashMoveSave').disabled = false;
  }
}

/* ------------------------------------------------------------ búsqueda */
let searchTimer = null;
let customerSearchTimer = null;

function scheduleSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 110);
}

async function runSearch() {
  const input = $('scan');
  const q = input.value.trim();
  if (!q) {
    S.results = [];
    S.search = null;
    S.selected = 0;
    renderResultsIdle();
    return;
  }
  try {
    const data = await api('/api/pos/search?q=' + encodeURIComponent(q));
    if (input.value.trim() !== q) return; // llegó otra tecla
    S.search = data;
    S.results = data.results || [];
    S.selected = 0;
    renderResults(q, data);
  } catch (err) {
    S.results = [];
    renderResultsMessage('No se pudo buscar', err.message);
  }
}

function renderResultsIdle() {
  S.results = [];
  S.selected = 0;
  $('results').innerHTML = `
    <div class="results-empty">
      <strong>Listo para el siguiente producto</strong>
      Escanea el código de barras o escribe parte del nombre.<br>
      Si el producto no tiene código, búscalo por nombre y elígelo de la lista.
    </div>`;
}

function renderResultsMessage(title, sub) {
  $('results').innerHTML = `<div class="results-empty"><strong>${esc(title)}</strong>${esc(sub || '')}</div>`;
}

function renderResults(q, data) {
  const rows = data.results || [];
  if (!rows.length) {
    renderResultsMessage(`Sin resultados para “${q}”`,
      'Revisa el código escaneado o busca por una palabra del nombre.');
    return;
  }
  const parts = [];
  const isManager = canManage();
  rows.forEach((p, index) => {
    const bits = [];
    if (p.sku) bits.push(`<span class="badge code">${esc(p.sku)}</span>`);
    if (p.category) bits.push(`<span class="badge">${esc(p.category)}</span>`);
    if (p.unit && p.unit !== 'Pieza') {
      bits.push(`<span class="badge">${esc(p.unit_symbol || p.unit)}</span>`);
    }
    if (p.base_id) {
      bits.push(`<span class="badge">📦 ${fmtQty(p.base_qty || 1)}× ${esc(p.base_name || '')}</span>`);
    } else if (p.has_packs) {
      bits.push('<span class="badge">📦 Tiene empaque</span>');
    }
    if (p.location) bits.push(`<span class="badge">📍 ${esc(p.location)}</span>`);
    if (p.duplicate) bits.push('<span class="badge warn">Código compartido</span>');
    if (p.price_override) bits.push('<span class="badge warn">Precio emergencia</span>');
    if (p.blocked_reason) bits.push(`<span class="badge warn">${esc(p.blocked_reason)}</span>`);
    else if (Number(p.stock) < 0) bits.push('<span class="badge danger">Stock negativo</span>');
    else if (Number(p.stock) <= 0) bits.push('<span class="badge warn">Sin existencias</span>');
    const priceHtml = p.price_override
      ? `<span class="price-old">${fmtMoney(p.original_price)}</span>${fmtMoney(p.price)}`
      : fmtMoney(p.price);
    const priceBtn = (isManager && S.rules.allow_emergency_prices)
      ? `<button class="pricebtn" data-price="${esc(String(p.key))}" title="Cambiar precio (emergencia)">precio</button>`
      : '';
    parts.push(`
      <div class="resultrow${index === S.selected ? ' selected' : ''}${p.sellable ? '' : ' blocked'}"
           data-index="${index}" role="button" tabindex="0">
        <span class="r-main">
          <span class="r-name">${esc(p.name)}</span>
          <span class="r-meta">${bits.join(' ')}</span>
        </span>
        <span class="r-price">${priceHtml} ${priceBtn}</span>
        <span class="r-stock${Number(p.stock) < 0 ? ' negative' : (Number(p.stock) <= 0 ? ' zero' : '')}">
          ${fmtQty(p.stock)} ${esc(p.unit_symbol || 'pza')}
        </span>
      </div>`);
  });
  const shown = data.total > rows.length
    ? `<div class="results-empty small">${data.total} coincidencias · se muestran ${rows.length}. Escribe más para afinar.</div>`
    : '';
  $('results').innerHTML = parts.join('') + shown;

  if (data.exact && data.exact.duplicate && S.lastWarn !== q) {
    S.lastWarn = q;
    toast('Ese código está en más de un producto. Elige el correcto de la lista.', 'bad');
    beep(false);
  }
}

function selectResult(delta) {
  if (!S.results.length) return;
  S.selected = Math.max(0, Math.min(S.results.length - 1, S.selected + delta));
  const nodes = $('results').querySelectorAll('.resultrow');
  nodes.forEach((n, i) => n.classList.toggle('selected', i === S.selected));
  if (nodes[S.selected]) nodes[S.selected].scrollIntoView({ block: 'nearest' });
}

function addProduct(p) {
  if (!p) return;
  if (!p.sellable) {
    toast(p.blocked_reason || 'Ese producto no se puede vender.', 'bad');
    beep(false);
    return;
  }
  const existing = S.cart.find((i) => String(i.key) === String(p.key));
  const stock = Number(p.stock);
  if (existing) {
    if (existing.qty + 1 > maxQty()) {
      toast(`Cantidad máxima por producto: ${maxQty()}.`);
      return;
    }
    if (!S.rules.allow_zero_stock && stock - (existing.qty + 1) < 0) {
      toast(`Sólo hay ${fmtQty(stock)} en existencia.`, 'bad');
      return;
    }
    existing.qty = round2(existing.qty + 1);
  } else {
    if (!S.rules.allow_zero_stock && stock <= 0) {
      toast('Ese producto no tiene existencias.', 'bad');
      return;
    }
    S.cart.push({
      key: String(p.key),
      legacy_id: p.legacy_id != null ? p.legacy_id : null,
      name: p.name,
      sku: p.sku || '',
      barcode: p.barcode || '',
      price: num(p.price),
      qty: 1,
      stock: stock,
      unit: p.unit || 'Pieza',
      unit_symbol: p.unit_symbol || 'pza',
    });
  }
  saveCart();
  renderCart();
  beep(true);
  $('scan').value = '';
  S.search = null;
  renderResultsIdle();
  focusScan();
}

function handleScanEnter() {
  const q = $('scan').value.trim();
  if (!q) return;
  const process = (fromFreshScan) => {
    const exact = S.search && S.search.exact;
    if (exact && exact.duplicate) {
      // Código compartido: nunca cobrar a ciegas. El cajero elige de la lista.
      toast('Ese código está en más de un producto. Elige el correcto.', 'bad');
      beep(false);
      return;
    }
    if (exact && exact.has_presentations && fromFreshScan) {
      // Producto con presentaciones (pieza y paquete): en el escaneo rápido
      // mostramos ambas para que el cajero elija, o pulse Enter otra vez.
      toast('Elige presentación (pieza o paquete) con ↑ ↓ y pulsa Enter.');
      return;
    }
    if (S.results.length && exact && exact.has_presentations) {
      addProduct(S.results[Math.min(S.selected, S.results.length - 1)]);
      return;
    }
    if (exact && exact.sellable) {
      addProduct(exact);
      return;
    }
    if (S.results.length) {
      addProduct(S.results[Math.min(S.selected, S.results.length - 1)]);
      return;
    }
    if (exact) { addProduct(exact); return; }
    toast(`No encontré “${q}”. Busca por nombre o revisa el código.`, 'bad');
    beep(false);
  };
  if (!S.search || S.search.query !== q) {
    // El escáner escribe y da Enter en milisegundos: esperamos la búsqueda.
    runSearch().then(() => {
      if ($('scan').value.trim() === q) process(true);
    });
    return;
  }
  process(false);
}

/* ------------------------------------------------------------ carrito UI */
function renderCart() {
  const box = $('cartLines');
  if (!S.cart.length) {
    box.innerHTML = `<div class="cart-empty">
      <strong>Carrito vacío</strong><br>Escanea o busca un producto para empezar.</div>`;
  } else {
    box.innerHTML = S.cart.map((i, index) => `
      <div class="cartline${Number(i.stock) < 0 ? ' negative' : ''}" data-index="${index}">
        <div class="cl-top">
          <span class="cl-name">${esc(i.name)}</span>
          ${Number(i.stock) < 0 ? `<span class="badge danger">Stock ${fmtQty(i.stock)}</span>` : ''}
          <button class="cl-remove" data-remove="${index}" title="Quitar">✕</button>
        </div>
        <div class="cl-bottom">
          <span class="qty">
            <button data-dec="${index}" title="Quitar uno">−</button>
            <input class="qty-input" data-qty="${index}" type="text" inputmode="decimal"
                   value="${fmtQty(i.qty)}" title="${esc(i.unit || 'Pieza')}"
                   aria-label="Cantidad de ${esc(i.name)}">
            <span class="unit">${esc(i.unit_symbol || 'pza')}</span>
            <button data-inc="${index}" title="Agregar uno">+</button>
          </span>
          <span class="cl-price">${fmtMoney(i.price)} c/u</span>
          <span class="cl-total">${fmtMoney(lineTotal(i))}</span>
        </div>
      </div>`).join('');
  }
  $('tCount').textContent = countText();
  $('tTotal').textContent = fmtMoney(cartTotal());
  const customerLabel = $('cartCustomerLabel');
  customerLabel.textContent = S.customer
    ? `Cliente: ${S.customer.nombre}` : 'Cliente: PÚBLICO EN GENERAL (predeterminado)';
  customerLabel.classList.toggle('selected', !!S.customer);
  $('clearCustomerBtn').hidden = !S.customer;
  $('chooseCustomerBtn').textContent = S.customer ? 'Cambiar' : 'Elegir otro';
  const cashRegister = isCashRegister();
  const pendingCount = (S.pendingSales || []).length;
  $('payBtn').disabled = !S.shift || (!S.cart.length && (!cashRegister || pendingCount === 0));
  if (cashRegister) {
    if (!S.cart.length && pendingCount > 0) {
      $('payBtn').innerHTML = `COBRAR PENDIENTES (${pendingCount}) <kbd>F4</kbd>`;
      $('payBtn').title = 'Ver y cobrar tickets despachados pendientes de pago (F4 / F6)';
    } else {
      $('payBtn').innerHTML = 'COBRAR <kbd>F4</kbd>';
      $('payBtn').title = 'Cobrar en caja (F4)';
    }
  } else {
    $('payBtn').innerHTML = 'DESPACHAR <kbd>F4</kbd>';
    $('payBtn').title = 'Despachar y generar ticket para pagar en caja (F4)';
  }
}

function changeQty(index, delta) {
  const item = S.cart[index];
  if (!item) return;
  const next = round2(item.qty + delta);
  if (next < 0.01) {
    S.cart.splice(index, 1);
  } else {
    if (next > maxQty()) { toast(`Cantidad máxima por producto: ${maxQty()}.`); return; }
    if (!S.rules.allow_zero_stock && next > item.stock) {
      toast(`Sólo hay ${fmtQty(item.stock)} ${item.unit_symbol || ''} en existencia.`, 'bad');
      return;
    }
    item.qty = next;
  }
  saveCart();
  renderCart();
}

function commitQty(index, raw) {
  const item = S.cart[index];
  if (!item) return;
  const text = String(raw == null ? '' : raw).trim().replace(',', '.');
  if (!text) { renderCart(); return; }
  const value = Number(text);
  if (!Number.isFinite(value) || value < 0) {
    toast('Cantidad inválida.', 'bad');
    renderCart();
    return;
  }
  const qty = round2(value);
  if (qty === 0) {
    if (value > 0) {
      toast('La cantidad mínima es 0.01.', 'bad');
      renderCart();
      return;
    }
    S.cart.splice(index, 1);
    saveCart();
    renderCart();
    return;
  }
  if (qty < 0.01) {
    toast('La cantidad mínima es 0.01.', 'bad');
    renderCart();
    return;
  }
  if (qty > maxQty()) {
    toast(`Cantidad máxima por producto: ${maxQty()}.`, 'bad');
    renderCart();
    return;
  }
  if (!S.rules.allow_zero_stock && qty > item.stock) {
    toast(`Sólo hay ${fmtQty(item.stock)} ${item.unit_symbol || ''} en existencia.`, 'bad');
    renderCart();
    return;
  }
  item.qty = qty;
  saveCart();
  renderCart();
}

function removeItem(index) {
  S.cart.splice(index, 1);
  saveCart();
  renderCart();
}

function clearCart(silent = false) {
  if (!S.cart.length && !S.customer) return;
  if (!silent && !confirm('¿Vaciar el carrito completo?')) return;
  S.cart = [];
  S.customer = null;
  saveCart();
  renderCart();
  focusScan();
}

/* ------------------------------------------------------------ cobro */
function activePaymentTotal() {
  return S.payingPendingSale ? round2(Number(S.payingPendingSale.total) || 0) : cartTotal();
}

function openPayment() {
  if (!S.shift) {
    toast(isCashRegister() ? 'Abre el turno antes de cobrar.' : 'Esperando apertura de turno por Caja central.', 'bad');
    return;
  }
  if (!S.cart.length) {
    if (isCashRegister() && (S.pendingSales || []).length > 0) {
      openPendingSales();
      return;
    }
    toast('El carrito está vacío.', 'bad');
    return;
  }

  if (!isCashRegister()) {
    dispatchSale();
    return;
  }

  S.payingPendingSale = null;
  if ($('payTicketMeta')) {
    $('payTicketMeta').hidden = true;
    $('payTicketMeta').innerHTML = '';
  }
  if ($('payHeadLabel')) $('payHeadLabel').textContent = 'Total a cobrar';
  S.method = 'efectivo';
  const total = activePaymentTotal();
  $('payTotal').textContent = fmtMoney(total);
  $('payError').textContent = '';
  $('refInput').value = '';
  $('cashInput').value = total.toFixed(2);
  setMethod('efectivo', false);
  updateChange();
  openModal('payModal');
  setTimeout(() => { $('cashInput').focus(); $('cashInput').select(); }, 60);
}

function openPendingSalePayment(sale) {
  if (!sale) return;
  if (!isCashRegister()) {
    toast('Sólo el gerente o administrador puede cobrar en Caja.', 'bad');
    return;
  }
  if (!S.shift) {
    toast('Abre el turno antes de cobrar.', 'bad');
    return;
  }
  S.payingPendingSale = sale;
  if ($('payTicketMeta')) {
    $('payTicketMeta').hidden = false;
    $('payTicketMeta').innerHTML = `
      <div class="srow"><span>Ticket despachado</span><b>${esc(sale.folio)}</b></div>
      <div class="srow"><span>Vendedor</span><b>${esc(sale.user_name || '—')} · ${esc((sale.created_at || '').slice(11, 16))}</b></div>
      ${sale.items_summary ? `<div class="srow"><span class="muted small" style="white-space:normal">${esc(sale.items_summary)}</span></div>` : ''}`;
  }
  if ($('payHeadLabel')) $('payHeadLabel').textContent = `Cobrar ticket ${sale.folio}`;
  S.method = 'efectivo';
  const total = activePaymentTotal();
  $('payTotal').textContent = fmtMoney(total);
  $('payError').textContent = '';
  $('refInput').value = '';
  $('cashInput').value = total.toFixed(2);
  setMethod('efectivo', false);
  updateChange();
  closeModal('pendingModal');
  closeModal('salesModal');
  openModal('payModal');
  setTimeout(() => { $('cashInput').focus(); $('cashInput').select(); }, 60);
}

async function dispatchSale() {
  if (S.busy) return;
  const local = await pingImpresoraLocal();
  const payload = {
    items: S.cart.map((i) => ({ product_key: i.key, legacy_id: i.legacy_id, qty: i.qty })),
    customer_id: S.customer ? S.customer.id : null,
    customer_name: customerTicketLabel(),
    impresion_local: local,
  };
  S.busy = true;
  try {
    const data = await api('/api/pos/dispatch', payload);
    S.lastSale = data.sale;
    if (data.pending_sales) S.pendingSales = data.pending_sales;
    S.cart = [];
    S.customer = null;
    saveCart();
    renderCart();
    renderHeader();
    showDone(data);
    refreshQuiet();
    toast(`Ticket ${data.sale.folio} despachado (pendiente de cobro en Caja).`, 'ok');
  } catch (err) {
    toast(err.message || 'Error al despachar venta.', 'bad');
    beep(false);
  } finally {
    S.busy = false;
  }
}

function setMethod(method, focus = true) {
  S.method = method;
  document.querySelectorAll('#payModal .method').forEach((b) => {
    b.classList.toggle('active', b.dataset.method === method);
  });
  $('cashFields').hidden = method !== 'efectivo';
  $('refFields').hidden = method === 'efectivo';
  if (method === 'efectivo') {
    if (!num($('cashInput').value)) $('cashInput').value = activePaymentTotal().toFixed(2);
    if (focus) setTimeout(() => { $('cashInput').focus(); $('cashInput').select(); }, 30);
    updateChange();
  } else {
    if (focus) setTimeout(() => $('refInput').focus(), 30);
  }
}

function updateChange() {
  const total = activePaymentTotal();
  const cash = num($('cashInput').value);
  const change = round2(cash - total);
  const el = $('changeValue');
  el.textContent = fmtMoney(Math.max(0, change));
  el.style.color = change < -0.005 ? '#b91c1c' : '#14532d';
}

async function confirmPayment() {
  if (S.busy) return;
  const total = activePaymentTotal();
  const method = S.method;
  const payments = [];
  if (method === 'efectivo') {
    const cash = num($('cashInput').value);
    if (cash <= 0) { $('payError').textContent = 'Escribe el efectivo recibido.'; return; }
    if (cash + 0.005 < total) {
      $('payError').textContent = `Faltan ${fmtMoney(total - cash)} para cubrir el total.`;
      return;
    }
    payments.push({ method: 'efectivo', amount: cash });
  } else {
    payments.push({ method, amount: total, reference: $('refInput').value.trim() });
  }
  const payingPending = S.payingPendingSale;
  const local = await pingImpresoraLocal();
  S.busy = true;
  $('payError').textContent = '';
  $('payConfirm').disabled = true;
  try {
    let data;
    if (payingPending) {
      data = await api('/api/pos/sale/pay', {
        sale_id: payingPending.id,
        payments,
        impresion_local: local,
      });
      S.payingPendingSale = null;
    } else {
      const payload = {
        items: S.cart.map((i) => ({ product_key: i.key, legacy_id: i.legacy_id, qty: i.qty })),
        customer_id: S.customer ? S.customer.id : null,
        customer_name: customerTicketLabel(),
        payments,
        impresion_local: local,
      };
      data = await api('/api/pos/checkout', payload);
      S.cart = [];
      S.customer = null;
      saveCart();
    }
    S.lastSale = data.sale;
    if (data.pending_sales) S.pendingSales = data.pending_sales;
    renderHeader();
    renderCart();
    closeModal('payModal');
    showDone(data);
    refreshQuiet();
  } catch (err) {
    $('payError').textContent = err.message;
    beep(false);
  } finally {
    S.busy = false;
    $('payConfirm').disabled = false;
  }
}

async function showDone(data) {
  const sale = data.sale;
  const isCash = isCashRegister();
  if ($('doneTitle')) $('doneTitle').textContent = isCash ? 'Venta cobrada' : 'Venta despachada';
  $('doneFolio').textContent = `Ticket ${sale.folio} · ${fmtMoney(sale.total)}`;
  $('doneChange').textContent = fmtMoney(data.change || 0);
  if ($('doneChangeWrap')) $('doneChangeWrap').hidden = !isCash;
  const link = $('ticketLink');
  const status = $('printStatus');
  link.href = ticketUrl(sale);

  // Si el ticket viene con líneas, se imprime en la impresora de ESTA PC.
  let local = null;
  if (data.ticket_lines && data.ticket_lines.length) {
    local = await imprimirTicketLocal(data);
  }

  if (local && local.ok) {
    status.className = 'printstatus ok';
    status.textContent = isCash
      ? `Ticket impreso${local.printer ? ' en ' + local.printer : ''}.`
      : 'Ticket impreso. Entregar al cliente para pagar en Caja.';
    link.href = ticketUrl(sale);
    link.textContent = 'Ver ticket';
  } else if (data.printed) {
    status.className = 'printstatus ok';
    status.textContent = isCash
      ? `Ticket impreso${data.printer ? ' en ' + data.printer : ''}.`
      : 'Ticket impreso. Entregar al cliente para pagar en Caja.';
    link.href = ticketUrl(sale);
    link.textContent = 'Ver ticket';
  } else {
    status.className = 'printstatus bad';
    const motivo = (local && local.error) || data.print_error;
    status.textContent = isCash
      ? (motivo ? `No se pudo imprimir: ${motivo}` : 'El ticket no se imprimió.')
      : 'Ticket generado. Entregar comprobante al cliente para pagar en Caja.';
    link.href = ticketUrl(sale, true);
    link.textContent = 'Abrir ticket para imprimir';
  }
  openModal('doneModal');
  beep(true);
  setTimeout(() => $('newSaleBtn').focus(), 80);
}

/* ------------------------------------------------------------ tickets pendientes de cobro */
function renderPendingModal() {
  const list = S.pendingSales || [];
  const total = list.reduce((s, r) => s + Number(r.total || 0), 0);
  if ($('pendingSummary')) {
    $('pendingSummary').innerHTML = `
      <span class="chip"><b>${list.length}</b> ticket${list.length === 1 ? '' : 's'} por cobrar</span>
      <span class="chip">Total pendiente <b>${fmtMoney(total)}</b></span>`;
  }
  if ($('pendingBody')) {
    $('pendingBody').innerHTML = list.map((s) => `
      <tr>
        <td class="code">${esc(s.folio)}</td>
        <td>${esc((s.created_at || '').slice(11, 16))}</td>
        <td>${esc(s.user_name || '')}</td>
        <td style="white-space:normal;max-width:340px" class="small">${esc(s.items_summary || `${fmtQty(s.item_count)} art.`)}</td>
        <td class="num"><b>${fmtMoney(s.total)}</b></td>
        <td class="actions">
          ${isCashRegister()
            ? `<button class="primary" style="padding:6px 12px;font-size:12.5px" data-paypending="${s.id}">Cobrar</button>`
            : ''}
          <button class="link" data-print="${s.id}" data-token="${esc(s.ticket_token || '')}">Ticket</button>
          ${isCashRegister()
            ? `<button class="link" data-cancel="${s.id}" data-folio="${esc(s.folio)}">Cancelar</button>`
            : ''}
        </td>
      </tr>`).join('') || '<tr><td colspan="6" class="muted">No hay tickets pendientes de cobro en este momento.</td></tr>';
  }
}

async function openPendingSales() {
  renderPendingModal();
  openModal('pendingModal');
  try {
    const data = await api('/api/pos/sales/pending');
    S.pendingSales = data.pending_sales || [];
    renderHeader();
    renderCart();
    renderPendingModal();
  } catch (err) {
    toast(err.message, 'bad');
  }
}

/* ------------------------------------------------------------ ventas del día */
async function openSales() {
  try {
    const data = await api('/api/pos/today');
    const by = data.by_method || {};
    const pendingChip = Number(data.pending_count || 0) > 0
      ? `<span class="chip" style="background:#fef3c7;border-color:#fcd34d;color:#92400e">Por cobrar <b>${fmtMoney(data.pending_total || 0)}</b> (${data.pending_count})</span>`
      : '';
    $('salesSummary').innerHTML = `
      <span class="chip"><b>${data.count}</b> tickets cobrados</span>
      <span class="chip">Total cobrado <b>${fmtMoney(data.total)}</b></span>
      <span class="chip">Efectivo <b>${fmtMoney(by.efectivo || 0)}</b></span>
      <span class="chip">Tarjeta <b>${fmtMoney(by.tarjeta || 0)}</b></span>
      <span class="chip">Transferencia <b>${fmtMoney(by.transferencia || 0)}</b></span>
      <span class="chip">Devoluciones <b>${fmtMoney(data.returns_total || 0)}</b> (${data.returns_count || 0})</span>
      <span class="chip">Neto <b>${fmtMoney(data.net_total || 0)}</b></span>
      ${pendingChip}`;
    $('salesBody').innerHTML = (data.sales || []).map((s) => `
      <tr class="${s.status === 'cancelled' ? 'cancelled' : ''}">
        <td class="code">${esc(s.folio)}</td>
        <td>${esc((s.created_at || '').slice(11, 16))}</td>
        <td>${esc(s.user_name || '')}</td>
        <td>${s.status === 'pending'
          ? '<span class="muted small">Sin cobrar</span>'
          : esc((s.methods || '').split(',').map((m) => ({
            efectivo: 'Efectivo', tarjeta: 'Tarjeta', transferencia: 'Transferencia',
          }[m] || m)).join(' + '))}</td>
        <td class="num">${fmtMoney(s.total)}</td>
        <td>${s.status === 'cancelled'
          ? '<span class="badge cancel">Cancelada</span>'
          : s.status === 'pending'
            ? '<span class="badge warn">Pendiente de cobro</span>'
            : '<span class="badge ok">Pagada</span>'}
          ${Number(s.returns_total || 0) > 0
            ? `<span class="badge warn">Devuelto ${fmtMoney(s.returns_total)}</span>` : ''}</td>
        <td class="actions">
          ${s.status === 'pending' && isCashRegister()
            ? `<button class="link" style="font-weight:700;color:var(--green)" data-paypending="${s.id}">Cobrar</button>`
            : ''}
          <button class="link" data-print="${s.id}" data-token="${esc(s.ticket_token || '')}">Reimprimir</button>
          ${s.status === 'completed'
            ? `<button class="link" data-return="${s.id}" data-folio="${esc(s.folio)}">Devolver</button>
               <button class="link" data-cancel="${s.id}" data-folio="${esc(s.folio)}">Cancelar</button>`
            : s.status === 'pending'
              ? `<button class="link" data-cancel="${s.id}" data-folio="${esc(s.folio)}">Cancelar</button>`
              : ''}
        </td>
      </tr>`).join('') || '<tr><td colspan="7" class="muted">Aún no hay ventas hoy.</td></tr>';

    $('returnsBody').innerHTML = (data.returns || []).map((r) => `
      <tr class="${r.status === 'cancelled' ? 'cancelled' : ''}">
        <td class="code">${esc(r.folio)}</td>
        <td>${esc((r.created_at || '').slice(11, 16))}</td>
        <td class="code">${esc(r.sale_folio)}</td>
        <td>${esc(r.user_name || '')}</td>
        <td>${esc({ efectivo: 'Efectivo', tarjeta: 'Tarjeta', transferencia: 'Transferencia' }[r.method] || r.method)}</td>
        <td class="num">${fmtMoney(r.total)}</td>
        <td>${r.status === 'cancelled'
          ? '<span class="badge cancel">Anulada</span>'
          : r.sale_status === 'cancelled'
            ? '<span class="badge cancel">Venta cancelada</span>'
            : '<span class="badge ok">Devuelta</span>'}</td>
        <td class="actions">
          ${r.status === 'completed' && r.sale_status !== 'cancelled'
            ? `<button class="link" data-returncancel="${r.id}" data-folio="${esc(r.folio)}">Anular</button>`
            : r.status === 'completed'
              ? '<span class="muted small">Incluida en la cancelación</span>'
              : ''}
        </td>
      </tr>`).join('') || '<tr><td colspan="8" class="muted">Sin devoluciones hoy.</td></tr>';
    openModal('salesModal');
  } catch (err) {
    toast(err.message, 'bad');
  }
}

/* ------------------------------------------------------------ devoluciones */
async function openReturn(saleId, folio) {
  S.returnSale = null;
  $('returnFolio').value = folio || '';
  $('returnLines').innerHTML = '';
  $('returnInfo').innerHTML = '<div class="srow"><span>Busca el ticket que vas a devolver.</span></div>';
  $('returnError').textContent = '';
  $('returnReason').value = '';
  $('returnPin').value = '';
  $('returnMethod').value = 'efectivo';
  openModal('returnModal');
  if (saleId) await lookupReturn(saleId);
  else setTimeout(() => $('returnFolio').focus(), 60);
}

async function lookupReturn(saleId) {
  try {
    const payload = saleId ? { sale_id: saleId } : { folio: $('returnFolio').value.trim() };
    if (!payload.folio && !payload.sale_id) throw new Error('Escribe el folio del ticket.');
    const data = await api('/api/pos/return/lookup', payload);
    S.returnSale = data.sale;
    $('returnFolio').value = data.sale.folio;
    renderReturnLines();
  } catch (err) {
    $('returnError').textContent = err.message;
  }
}

function renderReturnLines() {
  const sale = S.returnSale;
  if (!sale) return;
  $('returnInfo').innerHTML = `
    <div class="srow"><span>Ticket</span><b>${esc(sale.folio)}</b></div>
    <div class="srow"><span>Fecha</span><b>${esc(sale.created_at || '')}</b></div>
    <div class="srow"><span>Cajero</span><b>${esc(sale.user_name || '')}</b></div>
    <div class="srow"><span>Total del ticket</span><b>${fmtMoney(sale.total)}</b></div>
    <div class="srow"><span>Ya devuelto</span><b>${fmtMoney(sale.returned_total || 0)}</b></div>
    <div class="srow"><span><b>Total a devolver</b></span><b id="returnTotal">${fmtMoney(0)}</b></div>`;
  $('returnLines').innerHTML = sale.items.map((it) => `
    <tr>
      <td>${esc(it.name)}</td>
      <td class="num">${fmtQty(it.qty)} ${esc(it.unit || '')}</td>
      <td class="num">${fmtQty(it.returned_qty)} ${esc(it.unit || '')}</td>
      <td class="num">${fmtQty(it.remaining_qty)} ${esc(it.unit || '')}</td>
      <td class="num"><input class="rqty" type="number" min="0" step="0.01"
            data-item="${it.id}" data-price="${it.unit_price}" value="${it.remaining_qty}"></td>
      <td class="num" data-amount="${it.id}">${fmtMoney(0)}</td>
    </tr>`).join('');
  updateReturnTotal();
}

function updateReturnTotal() {
  let total = 0;
  document.querySelectorAll('.rqty').forEach((input) => {
    const qty = Math.max(0, num(input.value));
    const price = num(input.dataset.price);
    const amount = round2(qty * price);
    total += amount;
    const cell = document.querySelector(`[data-amount="${input.dataset.item}"]`);
    if (cell) cell.textContent = fmtMoney(amount);
  });
  const label = $('returnTotal');
  if (label) label.textContent = fmtMoney(round2(total));
  return round2(total);
}

async function confirmReturn() {
  if (S.busy) return;
  if (!S.returnSale) { $('returnError').textContent = 'Busca primero el ticket.'; return; }
  const items = Array.from(document.querySelectorAll('.rqty'))
    .map((input) => ({ sale_item_id: Number(input.dataset.item), qty: num(input.value) }))
    .filter((i) => i.qty > 0);
  if (!items.length) { $('returnError').textContent = 'Elige al menos una línea a devolver.'; return; }
  const reason = $('returnReason').value.trim();
  if (!reason) { $('returnError').textContent = 'Escribe el motivo de la devolución.'; return; }
  const payload = {
    sale_id: S.returnSale.id,
    items,
    reason,
    method: $('returnMethod').value,
  };
  if (S.security.manager_required_for_return) payload.manager_pin = $('returnPin').value.trim();
  payload.impresion_local = await pingImpresoraLocal();
  S.busy = true;
  $('returnError').textContent = '';
  $('returnConfirm').disabled = true;
  try {
    const data = await api('/api/pos/return', payload);
    closeModal('returnModal');
    const local = data.ticket_lines && data.ticket_lines.length
      ? await imprimirTicketLocal(data) : null;
    const okPrint = (local && local.ok) || data.printed;
    const printed = okPrint
      ? 'Comprobante impreso.'
      : `No se pudo imprimir (${(local && local.error) || data.print_error || 'sin impresora'}).`;
    toast(`Devolución ${data.return.folio} por ${fmtMoney(data.return.total)}. ${printed}`,
      okPrint ? 'ok' : 'bad');
    await openSales();
    refreshQuiet();
    if ($('scan').value.trim()) runSearch();
  } catch (err) {
    $('returnError').textContent = err.message;
    beep(false);
  } finally {
    S.busy = false;
    $('returnConfirm').disabled = false;
  }
}

/* ------------------------------------------------------------ inventario */
let invTimer = null;

function canManage() {
  return !!(S.user && (S.user.role === 'gerente' || S.user.role === 'administrador'));
}

function isAdmin() {
  return !!(S.user && S.user.role === 'administrador');
}

function isCashRegister() {
  if (!S.user) return false;
  return S.user.role === 'gerente' || S.user.role === 'administrador';
}

/* ------------------------------------------------------------ unidades */
async function loadUnits(includeInactive) {
  const data = await api('/api/pos/units' + (includeInactive ? '?todas=1' : ''));
  const units = data.units || [];
  if (!includeInactive) S.units = units;
  return units;
}

function unitOptions(selected) {
  const units = (S.units && S.units.length)
    ? S.units
    : [{ name: 'Pieza', symbol: 'pza' }];
  const current = selected || 'Pieza';
  const has = units.some((u) => u.name === current);
  const extra = has ? '' : `<option value="${esc(current)}" selected>${esc(current)}</option>`;
  return extra + units.map((u) => `
    <option value="${esc(u.name)}"${u.name === current ? ' selected' : ''}>
      ${esc(u.name)} (${esc(u.symbol)})
    </option>`).join('');
}

async function refreshUnitSelect() {
  const sel = $('invUnit');
  if (!sel) return;
  const current = sel.value || (S.invProduct && S.invProduct.unit) || 'Pieza';
  try { await loadUnits(false); } catch (_) { /* se conserva lo que había */ }
  sel.innerHTML = unitOptions(current);
}

async function openUnitsManager() {
  if (!canManage()) { toast('Sólo el administrador o el gerente.', 'bad'); return; }
  $('unitName').value = '';
  $('unitSymbol').value = '';
  $('unitsError').textContent = '';
  // Se abre encima del inventario o del menú (no los cierra).
  $('unitsModal').hidden = false;
  await refreshUnitsList();
  setTimeout(() => $('unitName').focus(), 60);
}

async function refreshUnitsList() {
  try {
    const units = await loadUnits(true);
    $('unitsList').innerHTML = units.length ? units.map((u) => {
      const isDefault = u.name.toLowerCase() === 'pieza';
      let action = '';
      if (isDefault) {
        action = '<span class="muted small">Predeterminada</span>';
      } else if (u.active) {
        action = `<button class="ghost danger-soft" data-unitoff="${u.id}">Eliminar</button>`;
      } else {
        action = `<button class="ghost" data-uniton="${u.id}">Reactivar</button>`;
      }
      return `<div class="unitrow${u.active ? '' : ' off'}">
        <span>
          <b>${esc(u.name)}</b> <span class="muted">(${esc(u.symbol)})</span>
          <span class="muted small"> · ${Number(u.product_count || 0).toLocaleString('es-MX')} producto(s)</span>
        </span>
        ${action}
      </div>`;
    }).join('') : '<div class="muted">Sin unidades registradas.</div>';
  } catch (err) {
    $('unitsError').textContent = err.message;
  }
}

async function addUnit() {
  const name = $('unitName').value.trim();
  const symbol = $('unitSymbol').value.trim();
  if (!name) { $('unitsError').textContent = 'Escribe el nombre de la unidad.'; return; }
  if (!symbol) { $('unitsError').textContent = 'Escribe la abreviatura.'; return; }
  try {
    const data = await api('/api/pos/unit', { name, symbol });
    $('unitName').value = '';
    $('unitSymbol').value = '';
    $('unitsError').textContent = '';
    await refreshUnitsList();
    await refreshUnitSelect();
    toast(`Unidad agregada: ${data.unit.name} (${data.unit.symbol}).`, 'ok');
  } catch (err) {
    $('unitsError').textContent = err.message;
  }
}

async function toggleUnit(unitId, active) {
  try {
    const data = await api('/api/pos/unit/active', { unit_id: unitId, active });
    $('unitsError').textContent = '';
    await refreshUnitsList();
    await refreshUnitSelect();
    toast(active ? `Unidad reactivada: ${data.unit.name}.`
      : `Unidad eliminada: ${data.unit.name}.`, 'ok');
  } catch (err) {
    $('unitsError').textContent = err.message;
  }
}

/* ------------------------------------------------------------ categorías y ubicaciones */
const DEFAULT_LOCATIONS_FALLBACK = [
  'fila 1 sección 1', 'fila 1 sección 2',
  'fila 2 sección 1', 'fila 2 sección 2',
  'fila 3 sección 1', 'fila 3 sección 2',
  'fila 4 sección 1', 'fila 4 sección 2',
  'fila 5 sección 1', 'fila 5 sección 2',
];

async function loadCategories(includeInactive) {
  const data = await api('/api/pos/categories' + (includeInactive ? '?todas=1' : ''));
  const categories = data.categories || [];
  if (!includeInactive) S.categories = categories;
  return categories;
}

function categoryOptions(selected) {
  const categories = (S.categories && S.categories.length)
    ? S.categories
    : [{ name: 'Papelería' }];
  const current = (selected || '').trim() || 'Papelería';
  const has = categories.some((c) => c.name.toLowerCase() === current.toLowerCase());
  const extra = has ? '' : `<option value="${esc(current)}" selected>${esc(current)}</option>`;
  return extra + categories.map((c) => `
    <option value="${esc(c.name)}"${c.name.toLowerCase() === current.toLowerCase() ? ' selected' : ''}>
      ${esc(c.name)}
    </option>`).join('');
}

async function refreshCategorySelect() {
  const sel = $('invCategory');
  if (!sel) return;
  const current = sel.value || (S.invProduct && S.invProduct.category) || 'Papelería';
  try { await loadCategories(false); } catch (_) { /* conserva lo cargado */ }
  sel.innerHTML = categoryOptions(current);
}

async function openCategoriesManager() {
  if (!canManage()) { toast('Sólo el administrador o el gerente.', 'bad'); return; }
  $('categoryName').value = '';
  $('categoriesError').textContent = '';
  $('categoriesModal').hidden = false;
  await refreshCategoriesList();
  setTimeout(() => $('categoryName').focus(), 60);
}

async function refreshCategoriesList() {
  try {
    const categories = await loadCategories(true);
    $('categoriesList').innerHTML = categories.length ? categories.map((c) => {
      const isDefault = c.name.toLowerCase() === 'papelería' || c.name.toLowerCase() === 'papeleria';
      let action = '';
      if (isDefault) {
        action = '<span class="muted small">Predeterminada</span>';
      } else if (c.active) {
        action = `<button class="ghost danger-soft" data-catoff="${c.id}">Eliminar</button>`;
      } else {
        action = `<button class="ghost" data-caton="${c.id}">Reactivar</button>`;
      }
      return `<div class="unitrow${c.active ? '' : ' off'}">
        <span>
          <b>${esc(c.name)}</b>
          <span class="muted small"> · ${Number(c.product_count || 0).toLocaleString('es-MX')} producto(s)</span>
        </span>
        ${action}
      </div>`;
    }).join('') : '<div class="muted">Sin categorías registradas.</div>';
  } catch (err) {
    $('categoriesError').textContent = err.message;
  }
}

async function addCategory() {
  const name = $('categoryName').value.trim();
  if (!name) { $('categoriesError').textContent = 'Escribe el nombre de la categoría.'; return; }
  try {
    const data = await api('/api/pos/category', { name });
    $('categoryName').value = '';
    $('categoriesError').textContent = '';
    await refreshCategoriesList();
    await refreshCategorySelect();
    const sel = $('invCategory');
    if (sel) sel.value = data.category.name;
    toast(`Categoría agregada: ${data.category.name}.`, 'ok');
  } catch (err) {
    $('categoriesError').textContent = err.message;
  }
}

async function toggleCategory(categoryId, active) {
  try {
    const data = await api('/api/pos/category/active', { category_id: categoryId, active });
    $('categoriesError').textContent = '';
    await refreshCategoriesList();
    await refreshCategorySelect();
    toast(active ? `Categoría reactivada: ${data.category.name}.`
      : `Categoría eliminada: ${data.category.name}.`, 'ok');
  } catch (err) {
    $('categoriesError').textContent = err.message;
  }
}

async function loadLocations(includeInactive) {
  const data = await api('/api/pos/locations' + (includeInactive ? '?todas=1' : ''));
  const locations = data.locations || [];
  if (!includeInactive) S.locations = locations;
  return locations;
}

function locationOptions(selected) {
  const locations = (S.locations && S.locations.length)
    ? S.locations
    : DEFAULT_LOCATIONS_FALLBACK.map((name) => ({ name }));
  const current = (selected != null ? String(selected).trim() : '') || locations[0].name;
  const has = locations.some((l) => l.name.toLowerCase() === current.toLowerCase());
  const extra = has ? '' : `<option value="${esc(current)}" selected>${esc(current)}</option>`;
  return extra + locations.map((l) => `
    <option value="${esc(l.name)}"${l.name.toLowerCase() === current.toLowerCase() ? ' selected' : ''}>
      ${esc(l.name)}
    </option>`).join('');
}

async function refreshLocationSelect() {
  const sel = $('invLocation');
  if (!sel) return;
  const current = sel.value || (S.invProduct && S.invProduct.location) || '';
  try { await loadLocations(false); } catch (_) { /* conserva lo cargado */ }
  sel.innerHTML = locationOptions(current);
}

async function openLocationsManager() {
  if (!canManage()) { toast('Sólo el administrador o el gerente.', 'bad'); return; }
  $('locationName').value = '';
  $('locationsError').textContent = '';
  $('locationsModal').hidden = false;
  await refreshLocationsList();
  setTimeout(() => $('locationName').focus(), 60);
}

async function refreshLocationsList() {
  try {
    const locations = await loadLocations(true);
    $('locationsList').innerHTML = locations.length ? locations.map((l) => {
      const action = l.active
        ? `<button class="ghost danger-soft" data-locoff="${l.id}">Eliminar</button>`
        : `<button class="ghost" data-locon="${l.id}">Reactivar</button>`;
      return `<div class="unitrow${l.active ? '' : ' off'}">
        <span>
          <b>${esc(l.name)}</b>
          <span class="muted small"> · ${Number(l.product_count || 0).toLocaleString('es-MX')} producto(s)</span>
        </span>
        ${action}
      </div>`;
    }).join('') : '<div class="muted">Sin ubicaciones registradas.</div>';
  } catch (err) {
    $('locationsError').textContent = err.message;
  }
}

async function addLocation() {
  const name = $('locationName').value.trim();
  if (!name) { $('locationsError').textContent = 'Escribe el nombre de la ubicación.'; return; }
  try {
    const data = await api('/api/pos/location', { name });
    $('locationName').value = '';
    $('locationsError').textContent = '';
    await refreshLocationsList();
    await refreshLocationSelect();
    const sel = $('invLocation');
    if (sel) sel.value = data.location.name;
    toast(`Ubicación agregada: ${data.location.name}.`, 'ok');
  } catch (err) {
    $('locationsError').textContent = err.message;
  }
}

async function toggleLocation(locationId, active) {
  try {
    const data = await api('/api/pos/location/active', { location_id: locationId, active });
    $('locationsError').textContent = '';
    await refreshLocationsList();
    await refreshLocationSelect();
    toast(active ? `Ubicación reactivada: ${data.location.name}.`
      : `Ubicación eliminada: ${data.location.name}.`, 'ok');
  } catch (err) {
    $('locationsError').textContent = err.message;
  }
}

const DEFAULT_REASONS_FALLBACK = ['Error de Conteo', 'Compra', 'Caducidad'];

async function loadReasons(includeInactive) {
  const data = await api('/api/pos/reasons' + (includeInactive ? '?todas=1' : ''));
  const reasons = data.reasons || [];
  if (!includeInactive) S.reasons = reasons;
  return reasons;
}

function reasonOptions(selected) {
  const reasons = (S.reasons && S.reasons.length)
    ? S.reasons
    : DEFAULT_REASONS_FALLBACK.map((name) => ({ name }));
  const current = (selected || '').trim() || 'Error de Conteo';
  const has = reasons.some((r) => r.name.toLowerCase() === current.toLowerCase());
  const extra = has ? '' : `<option value="${esc(current)}" selected>${esc(current)}</option>`;
  return extra + reasons.map((r) => `
    <option value="${esc(r.name)}"${r.name.toLowerCase() === current.toLowerCase() ? ' selected' : ''}>
      ${esc(r.name)}
    </option>`).join('');
}

async function refreshReasonSelect() {
  const sel = $('invReason');
  if (!sel) return;
  const current = sel.value || 'Error de Conteo';
  try { await loadReasons(false); } catch (_) { /* conserva lo cargado */ }
  sel.innerHTML = reasonOptions(current);
}

async function openReasonsManager() {
  if (!canManage()) { toast('Sólo el administrador o el gerente.', 'bad'); return; }
  $('reasonName').value = '';
  $('reasonsError').textContent = '';
  $('reasonsModal').hidden = false;
  await refreshReasonsList();
  setTimeout(() => $('reasonName').focus(), 60);
}

async function refreshReasonsList() {
  try {
    const reasons = await loadReasons(true);
    $('reasonsList').innerHTML = reasons.length ? reasons.map((r) => {
      const isDefault = r.name.toLowerCase() === 'error de conteo';
      let action = '';
      if (isDefault) {
        action = '<span class="muted small">Predeterminado</span>';
      } else if (r.active) {
        action = `<button class="ghost danger-soft" data-reasonoff="${r.id}">Eliminar</button>`;
      } else {
        action = `<button class="ghost" data-reasonon="${r.id}">Reactivar</button>`;
      }
      return `<div class="unitrow${r.active ? '' : ' off'}">
        <span>
          <b>${esc(r.name)}</b>
          <span class="muted small"> · ${Number(r.movement_count || 0).toLocaleString('es-MX')} movimiento(s)</span>
        </span>
        ${action}
      </div>`;
    }).join('') : '<div class="muted">Sin motivos registrados.</div>';
  } catch (err) {
    $('reasonsError').textContent = err.message;
  }
}

async function addReason() {
  const name = $('reasonName').value.trim();
  if (!name) { $('reasonsError').textContent = 'Escribe el motivo.'; return; }
  try {
    const data = await api('/api/pos/reason', { name });
    $('reasonName').value = '';
    $('reasonsError').textContent = '';
    await refreshReasonsList();
    await refreshReasonSelect();
    const sel = $('invReason');
    if (sel) sel.value = data.reason.name;
    toast(`Motivo agregado: ${data.reason.name}.`, 'ok');
  } catch (err) {
    $('reasonsError').textContent = err.message;
  }
}

async function toggleReason(reasonId, active) {
  try {
    const data = await api('/api/pos/reason/active', { reason_id: reasonId, active });
    $('reasonsError').textContent = '';
    await refreshReasonsList();
    await refreshReasonSelect();
    toast(active ? `Motivo reactivado: ${data.reason.name}.`
      : `Motivo eliminado: ${data.reason.name}.`, 'ok');
  } catch (err) {
    $('reasonsError').textContent = err.message;
  }
}

async function preloadNextSku() {
  try {
    const data = await api('/api/pos/inventory/next-sku');
    if (data && data.sku) {
      S.nextSku = data.sku;
      const input = $('invSku');
      if (input && (!input.value.trim() || input.dataset.auto === '1')) {
        input.value = data.sku;
        input.placeholder = `Se genera solo (${data.sku})`;
        input.dataset.auto = '1';
      }
    }
  } catch (_) { /* si falla, el servidor lo genera al guardar */ }
}

async function openInventory() {
  if (!canManage()) { toast('Sólo el administrador o el gerente.', 'bad'); return; }
  await Promise.all([
    loadUnits(false).catch(() => {}),
    loadCategories(false).catch(() => {}),
    loadLocations(false).catch(() => {}),
    loadReasons(false).catch(() => {}),
    preloadNextSku().catch(() => {}),
  ]);
  $('invDetail').innerHTML = 'Elige un producto de la lista.';
  $('invMoves').innerHTML = '';
  $('invSearch').value = '';
  $('invResults').innerHTML = '';
  openModal('inventoryModal');
  setTimeout(() => $('invSearch').focus(), 60);
}

async function invSearch() {
  const q = $('invSearch').value.trim();
  if (!q) { $('invResults').innerHTML = ''; return; }
  try {
    const endpoint = isAdmin()
      ? '/api/pos/inventory/products?q=' + encodeURIComponent(q) + '&inactive=1'
      : '/api/pos/search?q=' + encodeURIComponent(q);
    const data = await api(endpoint);
    S.invResults = data.results || [];
    renderInvResults();
  } catch (err) {
    toast(err.message, 'bad');
  }
}

function renderInvResults() {
  const rows = S.invResults || [];
  if (!rows.length) {
    $('invResults').innerHTML = '<div class="results-empty">Sin resultados.</div>';
    return;
  }
  $('invResults').innerHTML = rows.map((p, i) => `
    <div class="invrow${i === S.invSelected ? ' active' : ''}" data-invidx="${i}">
      <span><span class="n">${esc(p.name)}</span><br>
        <span class="m">${esc(p.sku || '')}${p.location ? ' · ' + esc(p.location) : ''}${p.unit ? ' · ' + esc(p.unit) : ''}${p.base_id ? ' · 📦 ' + esc(p.base_name || '') : ''}</span></span>
      <span class="p">${fmtMoney(p.price)}</span>
      <span class="s${Number(p.stock) <= 0 ? ' zero' : ''}">${fmtQty(p.stock)}</span>
      ${canManage()
        ? `<button class="invrow-edit" type="button" data-invedit="${i}" title="Editar producto">Editar</button>`
        : '<span></span>'}
    </div>`).join('');
}

function startInvEdit(index) {
  if (!canManage()) {
    toast('Sólo el administrador o el gerente.', 'bad');
    return;
  }
  const product = (S.invResults || [])[index];
  if (!product) return;
  selectInvProduct(product);
  const detail = $('invDetail');
  if (detail && detail.scrollIntoView) {
    detail.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  setTimeout(() => {
    const name = $('invName');
    if (name) { name.focus(); name.select(); }
  }, 80);
}

function selectInvProduct(product) {
  S.invProduct = product;
  S.invSelected = (S.invResults || []).indexOf(product);
  renderInvResults();
  renderInvDetail(product);
  loadInvMovements(product);
}

function renderInvDetail(p) {
  const isNew = !p;
  const inactive = p && p.active === 0;
  const isPack = !!(p && p.base_id);
  $('invDetail').innerHTML = `
    <div class="invhead">
      <div>
        <b style="font-size:15px">${isNew ? 'Nuevo producto' : esc(p.name)}</b>
        <div class="muted small">${isNew ? 'Alta manual en el inventario del POS'
          : `SKU ${esc(p.sku || '—')}${p.barcode ? ' · código ' + esc(p.barcode) : ''}`
            + (p.unit ? ' · ' + esc(p.unit) : '')}</div>
        ${isPack ? `<div class="small" style="color:var(--amber);font-weight:600">
          📦 Empaque de ${esc(p.base_name || '')} · ${fmtQty(p.base_qty || 1)} pza por empaque</div>` : ''}
      </div>
      <div class="invstock">
        <span>Existencias</span>
        <b>${isNew ? '0' : fmtQty(p.stock)}</b>
        ${isPack ? `<div class="small muted">= ${fmtQty((Number(p.stock) || 0) * (Number(p.base_qty) || 1))} pza del base</div>` : ''}
      </div>
    </div>
    <label for="invName">Nombre</label>
    <input id="invName" maxlength="120" value="${isNew ? '' : esc(p.name)}" autocomplete="off">
    ${isNew ? `<label for="invSku">SKU (opcional)</label>
    <input id="invSku" maxlength="30" value="${esc(S.nextSku || '')}"
           data-auto="1" placeholder="Se genera solo (${esc(S.nextSku || 'M-00001')})" autocomplete="off">` : ''}
    <label for="invBarcode">Código de barras</label>
    <input id="invBarcode" maxlength="40" value="${isNew ? '' : esc(p.barcode || '')}" autocomplete="off">
    <div class="grid2">
      <div>
        <label for="invUnit">Unidad de venta</label>
        <select id="invUnit">${unitOptions(isNew ? 'Pieza' : (p.unit || 'Pieza'))}</select>
      </div>
      <div>
        <label>&nbsp;</label>
        <button type="button" id="invUnitsBtn" class="ghost" style="width:100%">Administrar unidades…</button>
      </div>
    </div>
    <div class="grid2">
      <div>
        <label for="invCategory">Categoría</label>
        <select id="invCategory">${categoryOptions(isNew ? 'Papelería' : (p.category || 'Papelería'))}</select>
      </div>
      <div>
        <label>&nbsp;</label>
        <button type="button" id="invCategoriesBtn" class="ghost" style="width:100%">Administrar categorías…</button>
      </div>
    </div>
    <div class="grid2">
      <div>
        <label for="invLocation">Ubicación</label>
        <select id="invLocation">${locationOptions(isNew ? '' : (p.location || ''))}</select>
      </div>
      <div>
        <label>&nbsp;</label>
        <button type="button" id="invLocationsBtn" class="ghost" style="width:100%">Administrar ubicación…</button>
      </div>
    </div>
    <div class="grid2">
      <div>
        <label for="invPrice">Precio de venta</label>
        <input id="invPrice" type="number" step="0.01" min="0.01" inputmode="decimal"
               value="${isNew ? '' : Number(p.price).toFixed(2)}">
      </div>
      <div>
        <label for="invCost">Costo</label>
        <input id="invCost" type="number" step="0.01" min="0" inputmode="decimal"
               value="${isNew ? '' : Number(p.cost).toFixed(2)}">
      </div>
    </div>
    ${isNew ? `<label for="invInitial">Existencias iniciales</label>
    <input id="invInitial" type="number" step="0.001" inputmode="decimal" value="0">` : ''}
    <hr>
    <h4>Empaque de otro producto (opcional)</h4>
    <p class="muted small" style="margin:0 0 4px">Si este artículo se vende en paquete, vincúlalo
      al producto suelto: al vender 1 empaque se descuentan las unidades indicadas del base.</p>
    <label for="invBase">Producto base (existencias de donde se descuenta)</label>
    <input id="invBase" maxlength="80" placeholder="Buscar por nombre o SKU…" autocomplete="off">
    <div id="invBaseList" class="invbaseresults" hidden></div>
    <div class="grid2">
      <div>
        <label for="invBaseQty">Unidades por empaque</label>
        <input id="invBaseQty" type="number" step="0.01" min="0.01" inputmode="decimal" value="1">
      </div>
      <div>
        <label>&nbsp;</label>
        <button type="button" id="invBaseClear" class="ghost" style="width:100%" hidden>
          Quitar vínculo</button>
      </div>
    </div>
    <div id="invError" class="error"></div>
    ${isNew
      ? `<button id="invCreateBtn" class="primary" style="width:100%;margin-top:10px">Crear producto</button>`
      : `<button id="invSaveBtn" class="primary" style="width:100%;margin-top:10px">Guardar cambios</button>
         <hr>
         <h4>Movimientos de existencias</h4>
         <div class="grid2">
           <div>
             <label for="invQty">Cantidad (+ alta / − baja)</label>
             <input id="invQty" type="number" step="0.001" inputmode="decimal" value="1">
           </div>
           <div>
             <label for="invReason">Motivo</label>
             <select id="invReason">${reasonOptions('Error de Conteo')}</select>
           </div>
         </div>
         <div style="margin-bottom:8px">
           <button type="button" id="invReasonsBtn" class="ghost" style="width:100%">Administrar motivos…</button>
         </div>
         <div class="row2">
           <button id="invAddBtn" class="ghost">+ Alta</button>
           <button id="invSubBtn" class="ghost">− Baja</button>
         </div>
          ${(isAdmin() || (S.user && S.user.role === 'gerente')) ? `<button id="invToggleBtn" class="ghost danger-soft" style="width:100%;margin-top:10px">
            ${inactive ? 'Reactivar producto' : 'Dar de baja el producto'}</button>` : ''}`}`;
  wireInvDetail(p, isNew);
}

function invProductPayload(product) {
  return {
    key: product.key, name: product.name, sku: product.sku,
    barcode: product.barcode, category: product.category,
    location: product.location, price: product.price,
    cost: product.cost, stock: product.stock, active: product.active,
    unit: product.unit || 'Pieza', unit_symbol: product.unit_symbol || 'pza',
    base_id: product.base_product_id != null ? product.base_product_id : (product.base_id || null),
    base_qty: product.base_qty || 1,
    base_name: product.base_name || null,
  };
}

function apiProductPayload(product) {
  return invProductPayload({
    key: product.id, name: product.name, sku: product.sku,
    barcode: product.barcode, category: product.category,
    location: product.location, price: product.sale_price,
    cost: product.cost_price, stock: product.stock, active: product.active,
    unit: product.unit || 'Pieza', unit_symbol: product.unit_symbol || 'pza',
    base_product_id: product.base_product_id, base_qty: product.base_qty,
    base_name: product.base_name,
  });
}

function wireInvDetail(p, isNew) {
  const err = (msg) => { $('invError').textContent = msg; };
  if ($('invUnitsBtn')) $('invUnitsBtn').addEventListener('click', openUnitsManager);
  if ($('invCategoriesBtn')) $('invCategoriesBtn').addEventListener('click', openCategoriesManager);
  if ($('invLocationsBtn')) $('invLocationsBtn').addEventListener('click', openLocationsManager);
  if ($('invReasonsBtn')) $('invReasonsBtn').addEventListener('click', openReasonsManager);
  if (isNew && $('invSku')) {
    $('invSku').addEventListener('input', () => { $('invSku').dataset.auto = '0'; });
    preloadNextSku();
  }
  // vínculo empaque -> producto base
  S.invBaseSel = (p && p.base_id) ? { id: p.base_id, name: p.base_name || '' } : null;
  const baseInput = $('invBase');
  const baseList = $('invBaseList');
  const baseClear = $('invBaseClear');
  const baseQty = $('invBaseQty');
  baseInput.value = S.invBaseSel ? S.invBaseSel.name : '';
  baseQty.value = (p && p.base_id) ? (p.base_qty || 1) : 1;
  baseClear.hidden = !S.invBaseSel;
  let baseTimer = null;
  const hideBaseList = () => { baseList.hidden = true; baseList.innerHTML = ''; };
  baseInput.addEventListener('input', () => {
    clearTimeout(baseTimer);
    baseTimer = setTimeout(async () => {
      const q = baseInput.value.trim();
      if (!q || (S.invBaseSel && q === S.invBaseSel.name)) { hideBaseList(); return; }
      try {
        const data = await api('/api/pos/search?q=' + encodeURIComponent(q) + '&limit=8');
        const rows = (data.results || []).filter((r) => !r.base_id);
        if (!rows.length) { hideBaseList(); return; }
        baseList.innerHTML = rows.map((r) => `
          <button type="button" class="invbaseopt" data-baseid="${r.key}" data-basename="${esc(r.name)}">
            <span>${esc(r.name)}</span>
            <span class="muted small">${esc(r.sku || '')} · ${fmtQty(r.stock)} ${esc(r.unit_symbol || 'pza')}</span>
          </button>`).join('');
        baseList.hidden = false;
      } catch (_) { hideBaseList(); }
    }, 180);
  });
  baseList.addEventListener('click', (e) => {
    const opt = e.target.closest('[data-baseid]');
    if (!opt) return;
    S.invBaseSel = { id: Number(opt.dataset.baseid), name: opt.dataset.basename };
    baseInput.value = S.invBaseSel.name;
    baseClear.hidden = false;
    hideBaseList();
    baseQty.focus();
  });
  baseClear.addEventListener('click', () => {
    S.invBaseSel = null;
    baseInput.value = '';
    baseClear.hidden = true;
    hideBaseList();
    baseInput.focus();
  });
  const baseLink = () => {
    const text = baseInput.value.trim();
    if (S.invBaseSel && text === S.invBaseSel.name) {
      return { base_product_id: S.invBaseSel.id, base_qty: num(baseQty.value) || 1 };
    }
    if (!text) return { base_product_id: null, base_qty: 1 };
    return null;
  };
  if (isNew) {
    $('invCreateBtn').addEventListener('click', async () => {
      const link = baseLink();
      if (link === null) { err('Elige un producto de la lista para el vínculo, o déjalo vacío.'); return; }
      const payload = {
        name: $('invName').value.trim(),
        sku: $('invSku').value.trim(),
        barcode: $('invBarcode').value.trim(),
        category: $('invCategory').value.trim(),
        location: $('invLocation').value.trim(),
        unit: $('invUnit').value,
        sale_price: num($('invPrice').value),
        cost_price: num($('invCost').value),
        stock: num($('invInitial').value),
        ...link,
        reason: 'Alta manual',
      };
      try {
        const data = await api('/api/pos/inventory/product', payload);
        toast(`Producto creado: ${data.product.name}`, 'ok');
        S.nextSku = null;
        preloadNextSku();
        $('invSearch').value = '';
        $('invResults').innerHTML = '';
        selectInvProduct(apiProductPayload(data.product));
      } catch (e) { err(e.message); }
    });
    return;
  }

  $('invSaveBtn').addEventListener('click', async () => {
    try {
      const link = baseLink();
      if (link === null) { err('Elige un producto de la lista para el vínculo, o déjalo vacío.'); return; }
      const data = await api('/api/pos/inventory/edit', {
        product_key: p.key,
        changes: {
          name: $('invName').value.trim(),
          barcode: $('invBarcode').value.trim(),
          category: $('invCategory').value.trim(),
          location: $('invLocation').value.trim(),
          unit: $('invUnit').value,
          sale_price: num($('invPrice').value),
          cost_price: num($('invCost').value),
          ...link,
        },
        reason: 'Edición manual',
      });
      toast('Producto actualizado.', 'ok');
      selectInvProduct(apiProductPayload(data.product));
    } catch (e) { err(e.message); }
  });

  const move = async (sign) => {
    const qty = num($('invQty').value);
    const reason = $('invReason').value.trim();
    if (!qty) { err('Escribe la cantidad.'); return; }
    if (!reason) { err('Escribe el motivo del movimiento.'); return; }
    try {
      const data = await api('/api/pos/inventory/stock', {
        product_key: p.key, qty: Math.abs(qty) * sign, reason,
      });
      toast(`Existencias: ${fmtQty(data.product.stock)}`, 'ok');
      selectInvProduct(apiProductPayload(data.product));
    } catch (e) { err(e.message); }
  };
  $('invAddBtn').addEventListener('click', () => move(1));
  $('invSubBtn').addEventListener('click', () => move(-1));

  const toggleActive = $('invToggleBtn');
  if (toggleActive && (isAdmin() || (S.user && S.user.role === 'gerente'))) {
    toggleActive.addEventListener('click', async () => {
      const active = p.active === 0 ? 1 : 0;
      try {
        const data = await api('/api/pos/inventory/active', {
          product_key: p.key, active, reason: active ? 'Reactivación' : 'Baja de producto',
        });
        toast(active ? 'Producto reactivado.' : 'Producto dado de baja (ya no se vende).', 'ok');
        selectInvProduct(apiProductPayload(data.product));
      } catch (e) { err(e.message); }
    });
  }
}

async function loadInvMovements(product) {
  try {
    const data = await api('/api/pos/inventory/movements?product_key=' + encodeURIComponent(product.key));
    const rows = data.movements || [];
    let note = '';
    if (data.presentation) {
      note = `<div class="warnbox small">📦 Este empaque descuenta existencias del producto
        <b>${esc(data.presentation.base_name || '')}</b>
        (${fmtQty(data.presentation.base_qty || 1)} por empaque); los movimientos
        de abajo son los del producto base.</div>`;
    }
    if (!rows.length) {
      $('invMoves').innerHTML = note + '<div class="muted small">Sin movimientos registrados.</div>';
      return;
    }
    $('invMoves').innerHTML = note +
      '<div class="muted small" style="margin-bottom:6px">Movimientos (usuario · fecha · hora)</div>' +
      rows.map((m) => `
        <div class="mrow">
          <span><b class="${Number(m.qty) >= 0 ? 'qty-up' : 'qty-down'}">${Number(m.qty) >= 0 ? '+' : ''}${fmtQty(m.qty)}</b>
            ${esc(m.kind)} · ${esc(m.user_name || 'sistema')} · ${esc(m.reason || '')}</span>
          <span class="when">${esc((m.created_at || '').replace('T', ' '))} · stock ${fmtQty(m.stock_after)}</span>
        </div>`).join('');
  } catch (err) {
    $('invMoves').innerHTML = `<div class="muted small">${esc(err.message)}</div>`;
  }
}

/* ------------------------------------------------------------ usuarios */
async function openUsers() {
  if (!isAdmin()) { toast('Sólo el administrador puede administrar usuarios.', 'bad'); return; }
  $('usersError').textContent = '';
  await loadUsers();
  openModal('usersModal');
}

async function loadUsers() {
  try {
    const data = await api('/api/pos/users');
    const rows = data.users || [];
    $('usersList').innerHTML = `
      <table>
        <thead><tr><th>Nombre</th><th>Rol</th><th>Estado</th><th>Creado</th><th></th></tr></thead>
        <tbody>${rows.map((u) => `
          <tr>
            <td>${esc(u.name)}</td>
            <td>${esc({ gerente: 'Gerente', administrador: 'Administrador', cajero: 'Cajero' }[u.role] || u.role)}</td>
            <td>${u.active ? '<span class="badge ok">Activo</span>' : '<span class="badge">Inactivo</span>'}</td>
            <td class="small muted">${esc((u.created_at || '').slice(0, 10))}</td>
            <td class="actions">
              <button class="link" data-userpin="${u.id}" data-username="${esc(u.name)}">PIN</button>
              <button class="link" data-usertoggle="${u.id}" data-active="${u.active ? '1' : '0'}"
                      data-username="${esc(u.name)}">${u.active ? 'Desactivar' : 'Activar'}</button>
            </td>
          </tr>`).join('')}</tbody>
      </table>`;
  } catch (err) {
    $('usersError').textContent = err.message;
  }
}

async function createUser() {
  const name = $('newUserName').value.trim();
  const role = $('newUserRole').value;
  const pin = $('newUserPin').value.trim();
  $('usersError').textContent = '';
  if (!name || !pin) { $('usersError').textContent = 'Escribe nombre y PIN.'; return; }
  try {
    await api('/api/pos/user', { name, role, pin });
    $('newUserName').value = '';
    $('newUserPin').value = '';
    toast(`Usuario ${name} creado.`, 'ok');
    await loadUsers();
  } catch (err) {
    $('usersError').textContent = err.message;
  }
}

async function toggleUser(userId, active, name) {
  const what = active ? 'desactivar' : 'activar';
  if (!confirm(`¿${what} a ${name}?`)) return;
  try {
    await api('/api/pos/user/active', { user_id: userId, active: !active });
    toast(`Usuario ${active ? 'desactivado' : 'activado'}.`, 'ok');
    await loadUsers();
  } catch (err) {
    toast(err.message, 'bad');
  }
}

function openPinModal(userId, name) {
  S.pinUser = { id: userId, name };
  $('pinModalWho').textContent = `Nuevo PIN para ${name}`;
  $('pinModalNew').value = '';
  $('pinModalError').textContent = '';
  openModal('pinModal');
  setTimeout(() => $('pinModalNew').focus(), 60);
}

async function savePinModal() {
  const pin = $('pinModalNew').value.trim();
  $('pinModalError').textContent = '';
  if (!pin) { $('pinModalError').textContent = 'Escribe el PIN.'; return; }
  try {
    await api('/api/pos/user/pin', { user_id: S.pinUser.id, pin });
    closeModal('pinModal');
    toast(`PIN de ${S.pinUser.name} actualizado.`, 'ok');
  } catch (err) {
    $('pinModalError').textContent = err.message;
  }
}

/* ------------------------------------------------------------ precio de emergencia */
function hideField(inputEl, hidden) {
  inputEl.hidden = hidden;
  const label = inputEl.previousElementSibling;
  if (label && label.tagName === 'LABEL') label.hidden = hidden;
}

function openPriceModal(productKey, mode = 'set') {
  const overrideRow = (S.priceOverrides || []).find((r) => String(r.product_key) === String(productKey));
  const product = overrideRow || S.results.find((r) => String(r.key) === String(productKey));
  if (!product) { toast('El producto ya no está en la lista.', 'bad'); return; }
  S.priceTarget = product;
  S.priceMode = mode;
  const isRevert = mode === 'revert';
  const catalogPrice = product.catalog_price != null
    ? product.catalog_price
    : (product.price_override && product.original_price != null ? product.original_price : product.price);
  $('priceInfo').innerHTML = `
    <div class="srow"><span>Producto</span><b>${esc(product.name)}</b></div>
    <div class="srow"><span>SKU</span><b>${esc(product.sku || '—')}</b></div>
    <div class="srow"><span>Precio en catálogo</span><b>${fmtMoney(catalogPrice)}</b></div>
    ${product.price_override || overrideRow ? `<div class="srow"><span>Precio vigente</span><b>${fmtMoney(product.price)}</b></div>` : ''}`;
  hideField($('priceNew'), isRevert);
  hideField($('priceReason'), isRevert);
  $('priceNew').value = product.price;
  $('priceReason').value = '';
  $('pricePin').value = '';
  $('priceError').textContent = '';
  $('priceSave').textContent = isRevert ? 'Revertir al precio del catálogo' : 'Guardar precio';
  $('priceSave').className = isRevert ? 'danger' : 'primary';
  openModal('priceModal');
  setTimeout(() => (isRevert ? $('pricePin') : $('priceNew')).focus(), 60);
}

async function savePrice() {
  if (S.busy || !S.priceTarget) return;
  const pin = $('pricePin').value.trim();
  S.busy = true;
  $('priceError').textContent = '';
  $('priceSave').disabled = true;
  try {
    if (S.priceMode === 'revert') {
      await api('/api/pos/price/revert', { product_key: S.priceTarget.key, manager_pin: pin });
      closeModal('priceModal');
      toast('Se volvió al precio del catálogo.', 'ok');
    } else {
      const data = await api('/api/pos/price/override', {
        product_key: S.priceTarget.key,
        price: num($('priceNew').value),
        reason: $('priceReason').value.trim(),
        manager_pin: pin,
      });
      closeModal('priceModal');
      toast(`Precio de ${data.name}: ${fmtMoney(data.original_price)} → ${fmtMoney(data.price)}.`, 'ok');
    }
    await refreshQuiet();
    if ($('scan').value.trim()) runSearch();
    loadPriceList();
  } catch (err) {
    $('priceError').textContent = err.message;
    beep(false);
  } finally {
    S.busy = false;
    $('priceSave').disabled = false;
  }
}

async function loadPriceList() {
  const box = $('priceList');
  try {
    const data = await api('/api/pos/prices');
    const rows = data.overrides || [];
    S.priceOverrides = rows;
    if (!rows.length) {
      box.innerHTML = '<p class="muted small">No hay precios de emergencia activos.</p>';
      return;
    }
    box.innerHTML = rows.map((r) => `
      <div class="prow">
        <span>${esc(r.name)}<br><span class="muted small">${esc(r.reason || 'sin motivo')} · ${esc(r.created_at || '')}</span></span>
        <span><b>${fmtMoney(r.price)}</b> <span class="price-old">${fmtMoney(r.original_price)}</span></span>
        <button class="link" data-revert="${r.legacy_item_id}">Revertir</button>
      </div>`).join('');
  } catch (err) {
    box.innerHTML = `<p class="muted small">${esc(err.message)}</p>`;
  }
}

async function exportSales() {
  try {
    const data = await api('/api/pos/export/sales');
    toast(`CSV creado: ${data.path.split('\\').pop()} (${data.sales} ventas, ${data.returns} devoluciones)`, 'ok');
    $('menuStatus').className = 'printstatus ok';
    $('menuStatus').textContent = `Exportado en ${data.path}`;
  } catch (err) {
    toast(err.message, 'bad');
  }
}

/* ------------------------------------------------------------ notificaciones */
async function openNotifications() {
  try {
    const data = await api('/api/pos/notifications');
    S.notifRows = data.notifications || [];
    S.notifications = data.count || 0;
    $('notifList').innerHTML = S.notifRows.length
      ? `<table>
           <thead><tr><th>Producto</th><th>SKU</th><th class="num">Stock</th>
                      <th>Detectado</th><th>Origen</th></tr></thead>
           <tbody>${S.notifRows.map((n) => `
             <tr class="notifrow">
               <td>${esc(n.name)}</td>
               <td class="code">${esc(n.sku || '')}</td>
               <td class="num negative">${fmtQty(n.stock)}</td>
               <td class="small muted">${esc((n.updated_at || '').slice(0, 16))}</td>
               <td>${esc(n.source || '')}</td>
             </tr>`).join('')}</tbody>
         </table>`
      : '<p class="muted">No hay avisos pendientes. El stock negativo aparece aquí hasta que se corrija.</p>';
    renderHeader();
    openModal('notificationsModal');
  } catch (err) {
    toast(err.message, 'bad');
  }
}

async function openCancel(saleId, folio) {
  S.cancelTarget = { type: 'sale', id: saleId, folio };
  $('cancelModal').querySelector('h2').innerHTML =
    `Cancelar venta <span id="cancelFolio" class="muted">${folio ? '· ' + esc(folio) : ''}</span>`;
  $('cancelNote').className = 'muted';
  $('cancelNote').textContent =
    'Esta acción queda registrada con el nombre de quien la autoriza.';
  $('cancelReason').value = '';
  $('cancelPin').value = '';
  $('cancelError').textContent = '';
  $('cancelConfirm').disabled = false;
  $('cancelConfirm').textContent = 'Cancelar venta';
  openModal('cancelModal');
  setTimeout(() => $('cancelReason').focus(), 60);
  try {
    const data = await api('/api/pos/return/lookup', { sale_id: saleId });
    const returned = Number((data.sale && data.sale.returned_total) || 0);
    if (returned > 0 && S.cancelTarget && S.cancelTarget.type === 'sale'
        && S.cancelTarget.id === saleId) {
      $('cancelNote').className = 'warnbox';
      $('cancelNote').innerHTML =
        `Esta venta ya tiene <b>${fmtMoney(returned)}</b> en devoluciones. ` +
        'Al cancelarla sólo se repondrán las existencias que faltan y se ' +
        'reintegrará el importe pendiente.';
    }
  } catch (_) { /* la nota genérica es suficiente */ }
}

function openCancelReturn(returnId, folio) {
  S.cancelTarget = { type: 'return', id: returnId, folio };
  $('cancelModal').querySelector('h2').innerHTML =
    `Anular devolución <span id="cancelFolio" class="muted">${folio ? '· ' + esc(folio) : ''}</span>`;
  $('cancelNote').className = 'muted';
  $('cancelNote').textContent =
    'Esta acción queda registrada con el nombre de quien la autoriza.';
  $('cancelReason').value = '';
  $('cancelPin').value = '';
  $('cancelError').textContent = '';
  $('cancelConfirm').disabled = false;
  $('cancelConfirm').textContent = 'Anular devolución';
  openModal('cancelModal');
  setTimeout(() => $('cancelReason').focus(), 60);
}

async function confirmCancel() {
  if (S.busy || !S.cancelTarget) return;
  const reason = $('cancelReason').value.trim();
  if (!reason) { $('cancelError').textContent = 'Escribe el motivo.'; return; }
  const payload = { reason };
  if (S.security.manager_required_for_cancel) payload.manager_pin = $('cancelPin').value.trim();
  S.busy = true;
  $('cancelError').textContent = '';
  $('cancelConfirm').disabled = true;
  try {
    if (S.cancelTarget.type === 'return') {
      payload.return_id = S.cancelTarget.id;
      await api('/api/pos/return/cancel', payload);
      closeModal('cancelModal');
      toast('Devolución anulada. El efectivo volvió a la caja.', 'ok');
    } else {
      payload.sale_id = S.cancelTarget.id;
      await api('/api/pos/sale/cancel', payload);
      closeModal('cancelModal');
      toast('Venta cancelada. Queda registrada en la bitácora.', 'ok');
    }
    if (!$('salesModal').hidden) await openSales();
    await refreshQuiet();
  } catch (err) {
    $('cancelError').textContent = err.message;
    beep(false);
  } finally {
    S.busy = false;
    $('cancelConfirm').disabled = false;
  }
}

async function reprintSale(saleId, token = '') {
  try {
    const local = await pingImpresoraLocal();
    const data = await api('/api/pos/sale/print', { sale_id: saleId, impresion_local: local });
    let ok = data.printed;
    let printer = data.printer;
    if (data.ticket_lines && data.ticket_lines.length) {
      const res = await imprimirTicketLocal(data);
      if (res && res.ok) { ok = true; printer = res.impresora || printer; }
    }
    if (ok) toast(`Ticket reimpreso${printer ? ' en ' + printer : ''}.`, 'ok');
    else {
      toast('No se pudo imprimir. Abre el ticket en el navegador.', 'bad');
      const sale = { id: saleId, ticket_token: data.ticket_token || token };
      window.open(ticketUrl(sale, true), '_blank');
    }
  } catch (err) {
    toast(err.message, 'bad');
  }
}

/* ------------------------------------------------------------ clientes */
function openCustomers(mode = 'browse') {
  S.customerMode = mode;
  S.customerRows = [];
  S.customerDetail = null;
  $('customersSearch').value = '';
  $('customersIncludeDeleted').checked = false;
  $('customersBody').innerHTML = '';
  $('customerDetail').innerHTML = '<p class="muted">Selecciona un cliente para consultar su ficha.</p>';
  $('customersModeHint').textContent = mode === 'select'
    ? 'Elige un cliente para asociarlo a esta venta.'
    : 'Consulta clientes importados del histórico de 1box.';
  $('customersCancelSelect').hidden = mode !== 'select';
  $('customersStatus').textContent = 'Cargando clientes…';
  openModal('customersModal');
  loadCustomers();
  setTimeout(() => $('customersSearch').focus(), 50);
}

async function loadCustomers() {
  const query = $('customersSearch').value.trim();
  const deleted = $('customersIncludeDeleted').checked ? '1' : '0';
  $('customersStatus').textContent = 'Buscando…';
  try {
    const data = await api(
      `/api/pos/clientes?q=${encodeURIComponent(query)}&eliminados=${deleted}&limit=100`
    );
    S.customerRows = data.customers || [];
    renderCustomers();
    $('customersStatus').textContent = `${data.total || 0} cliente(s) encontrado(s)` +
      ((data.total || 0) > S.customerRows.length ? ` · mostrando ${S.customerRows.length}` : '');
  } catch (err) {
    $('customersBody').innerHTML = '';
    $('customersStatus').textContent = (err.message === 'Error 404'
      || err.message.includes('Ruta no encontrada'))
      ? 'La sección de clientes se activará cuando se reinicie el servidor de caja.'
      : err.message;
  }
}

function renderCustomers() {
  const body = $('customersBody');
  if (!S.customerRows.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted">No se encontraron clientes.</td></tr>';
    return;
  }
  body.innerHTML = S.customerRows.map((c) => `
    <tr>
      <td><b>${esc(c.nombre || 'Sin nombre')}</b><br><span class="muted">${c.es_empresa ? 'Empresa' : 'Persona'}</span></td>
      <td>${esc(c.rfc || '—')}</td>
      <td>${esc(c.telefono || '—')}</td>
      <td>${esc(c.email || '—')}</td>
      <td class="customer-status${c.activo ? '' : ' inactive'}">${c.activo ? 'Activo' : 'Baja'}</td>
      <td><div class="customer-actions">
        <button class="ghost small" data-customer-detail="${Number(c.id)}">Ficha</button>
      </div></td>
    </tr>`).join('');
}

async function showCustomerDetail(customerId) {
  $('customerDetail').innerHTML = '<p class="muted">Cargando ficha…</p>';
  try {
    const data = await api(`/api/pos/cliente?id=${encodeURIComponent(customerId)}`);
    S.customerDetail = data.customer;
    renderCustomerDetail();
  } catch (err) {
    $('customerDetail').innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

function renderCustomerDetail() {
  const c = S.customerDetail;
  if (!c) return;
  let altEmails = [];
  try { altEmails = c.alt_emails ? JSON.parse(c.alt_emails) : []; } catch (_) { /* dato heredado */ }
  const emailText = [c.email, ...altEmails].filter(Boolean).map(esc).join('<br>') || '—';
  const location = [c.direccion, c.ciudad, c.estado, c.cp].filter(Boolean).map(esc).join(', ') || '—';
  const sales = c.sales || [];
  $('customerDetail').innerHTML = `
    <h3>${esc(c.nombre || 'Sin nombre')}</h3>
    <div class="srow"><span>Tipo</span><b>${c.es_empresa ? 'Empresa' : 'Persona'}</b></div>
    <div class="srow"><span>Estado</span><b class="customer-status${c.activo ? '' : ' inactive'}">${c.activo ? 'Activo' : 'Baja'}</b></div>
    <div class="srow"><span>RFC / cuenta histórica</span><b>${esc(c.rfc || '—')}</b></div>
    <div class="srow"><span>Teléfono</span><b>${esc(c.telefono || '—')}</b></div>
    <div class="srow"><span>Correo</span><b>${emailText}</b></div>
    <div class="srow"><span>Dirección</span><b>${location}</b></div>
    <div class="srow"><span>Saldo histórico 1box</span><b>${fmtMoney(c.saldo || 0)}</b></div>
    <div class="srow"><span>Límite histórico</span><b>${fmtMoney(c.limite_credito || 0)}</b></div>
    <div class="srow"><span>Compras en esta caja</span><b>${Number(c.sales_count || 0)} · ${fmtMoney(c.total_comprado || 0)}</b></div>
    <div class="srow"><span>Última compra en esta caja</span><b>${esc(c.last_sale || '—')}</b></div>
    <div class="customer-sales">
      <b>Últimas ventas de esta caja</b>
      ${sales.length ? `<div class="tablewrap"><table><thead><tr><th>Fecha</th><th>Folio</th><th class="num">Total</th></tr></thead><tbody>
        ${sales.map((sale) => `<tr><td>${esc(sale.created_at || '')}</td><td>${esc(sale.folio || '')}</td><td class="num">${fmtMoney(sale.total || 0)}</td></tr>`).join('')}
      </tbody></table></div>` : '<p class="muted small">Aún no hay ventas asociadas en esta caja.</p>'}
    </div>
    ${S.customerMode === 'select' && c.activo ? `<button class="primary customer-use" data-customer-use="${Number(c.id)}">Usar este cliente en la venta</button>` : ''}`;
}

function chooseCustomer(customerId) {
  const customer = S.customerDetail;
  if (!customer || Number(customer.id) !== Number(customerId) || !customer.activo) return;
  S.customer = {
    id: Number(customer.id),
    nombre: customer.nombre || '',
    rfc: customer.rfc || '',
    telefono: customer.telefono || '',
  };
  renderCart();
  closeModal('customersModal');
}

/* ------------------------------------------------------------ menú */
async function testPrint() {
  $('menuStatus').className = 'printstatus';
  $('menuStatus').textContent = 'Enviando prueba…';
  try {
    const local = await pingImpresoraLocal(true);
    const data = await api('/api/pos/print/test', { impresion_local: local });
    let ok = data.ok;
    let error = data.error;
    let printer = data.printer;
    if (data.ticket_lines && data.ticket_lines.length) {
      const res = await imprimirTicketLocal(data);
      if (res && res.ok) { ok = true; printer = res.impresora || printer; error = ''; }
      else { ok = false; error = (res && res.error) || 'sin impresora local'; }
    }
    $('menuStatus').className = 'printstatus ' + (ok ? 'ok' : 'bad');
    $('menuStatus').textContent = ok
      ? `Prueba enviada a ${printer || 'la impresora'}. Revisa el papel.`
      : `Falló: ${error}`;
  } catch (err) {
    $('menuStatus').className = 'printstatus bad';
    $('menuStatus').textContent = err.message;
  }
}

async function backupNow() {
  $('menuStatus').className = 'printstatus';
  $('menuStatus').textContent = 'Respaldando…';
  try {
    const data = await api('/api/pos/backup', {});
    $('menuStatus').className = 'printstatus ok';
    $('menuStatus').textContent = `Respaldo creado: ${data.path.split('\\').pop()}`;
  } catch (err) {
    $('menuStatus').className = 'printstatus bad';
    $('menuStatus').textContent = err.message;
  }
}

async function savePin() {
  const current = $('currentPin').value.trim();
  const next = $('newPin').value.trim();
  if (!current || !next) { toast('Escribe el PIN actual y el nuevo.', 'bad'); return; }
  try {
    const data = await api('/api/pos/pin', { current_pin: current, new_pin: next });
    S.token = data.token;
    localStorage.setItem(TOKEN_KEY, data.token);
    $('currentPin').value = '';
    $('newPin').value = '';
    $('pinChange').hidden = true;
    toast('PIN actualizado.', 'ok');
  } catch (err) {
    toast(err.message, 'bad');
  }
}

async function logout() {
  try { await api('/api/pos/logout', {}); } catch (_) { /* da igual */ }
  logoutLocal();
  S.cart = [];
  saveCart();
  renderCart();
  closeModal('menuModal');
  showLogin('Sesión cerrada.');
}

/* ------------------------------------------------------------ modales */
const OVERLAYS = ['loginScreen', 'shiftScreen', 'payModal', 'doneModal', 'pendingModal',
  'salesModal', 'cancelModal', 'shiftCloseModal', 'cashModal', 'menuModal', 'returnModal',
  'priceModal', 'inventoryModal', 'usersModal', 'unitsModal', 'categoriesModal',
  'locationsModal', 'reasonsModal', 'pinModal', 'notificationsModal', 'printerModal',
  'customersModal'];

function hideOverlays() {
  OVERLAYS.forEach((id) => {
    if (id === 'loginScreen') return; // la maneja showLogin/showBootstrap
    $(id).hidden = true;
  });
}

function openModal(id) {
  OVERLAYS.forEach((other) => {
    if (other !== id && other !== 'loginScreen') $(other).hidden = true;
  });
  $(id).hidden = false;
}

function closeModal(id) {
  if (id === 'payModal') S.payingPendingSale = null;
  $(id).hidden = true;
  focusScan();
}

function visibleModal() {
  const open = OVERLAYS.filter((id) => !$(id).hidden && id !== 'loginScreen' && id !== 'shiftScreen');
  return open.length ? open[open.length - 1] : null;
}

/* ------------------------------------------------------------ reloj y foco */
function tickClock() {
  const now = new Date();
  $('clock').textContent = now.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
}

function focusScan() {
  if ($('posMain').hidden) return;
  if (visibleModal()) return;
  const el = $('scan');
  if (el && document.activeElement !== el) el.focus();
}

/* ------------------------------------------------------------ eventos */
function wire() {
  // login
  $('keypad').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-key]');
    if (!btn) return;
    const key = btn.dataset.key;
    if (key === 'clear') setPin('');
    else if (key === 'back') setPin(S.pin.slice(0, -1));
    else setPin(S.pin + key);
  });
  $('loginSubmit').addEventListener('click', submitPin);
  $('bootstrapBtn').addEventListener('click', bootstrap);

  // turno
  $('openShiftBtn').addEventListener('click', openShift);
  $('openingQuick').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-open]');
    if (btn) $('openingCash').value = btn.dataset.open;
  });
  $('openingCash').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); openShift(); }
  });
  $('logoutFromShift').addEventListener('click', logout);
  if ($('shiftCheckBtn')) {
    $('shiftCheckBtn').addEventListener('click', async () => {
      await refresh();
      if (!S.shift) {
        toast('Aún no han abierto turno en Caja central.', 'bad');
      } else {
        toast('Turno detectado. Pantalla desbloqueada.', 'ok');
      }
    });
  }

  // búsqueda
  $('scan').addEventListener('input', () => {
    S.lastWarn = '';
    scheduleSearch();
  });
  $('scan').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(searchTimer); handleScanEnter(); }
    else if (e.key === 'Escape') {
      e.preventDefault();
      $('scan').value = '';
      S.search = null;
      renderResultsIdle();
    } else if (e.key === 'ArrowDown') { e.preventDefault(); selectResult(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); selectResult(-1); }
  });
  $('clearSearch').addEventListener('click', () => {
    $('scan').value = '';
    S.search = null;
    renderResultsIdle();
    focusScan();
  });
  $('results').addEventListener('click', (e) => {
    const priceBtn = e.target.closest('[data-price]');
    if (priceBtn) {
      e.stopPropagation();
      openPriceModal(Number(priceBtn.dataset.price));
      return;
    }
    const row = e.target.closest('.resultrow');
    if (!row) return;
    const product = S.results[Number(row.dataset.index)];
    addProduct(product);
  });
  $('results').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const row = e.target.closest('.resultrow');
    if (!row) return;
    e.preventDefault();
    addProduct(S.results[Number(row.dataset.index)]);
  });

  // carrito
  $('cartLines').addEventListener('click', (e) => {
    const inc = e.target.closest('[data-inc]');
    const dec = e.target.closest('[data-dec]');
    const rem = e.target.closest('[data-remove]');
    if (inc) changeQty(Number(inc.dataset.inc), 1);
    else if (dec) changeQty(Number(dec.dataset.dec), -1);
    else if (rem) removeItem(Number(rem.dataset.remove));
  });
  $('cartLines').addEventListener('focusin', (e) => {
    if (e.target.matches('.qty-input')) e.target.select();
  });
  $('cartLines').addEventListener('input', (e) => {
    if (!e.target.matches('.qty-input')) return;
    const index = Number(e.target.dataset.qty);
    const item = S.cart[index];
    if (!item) return;
    const v = num(e.target.value);
    const line = e.target.closest('.cartline');
    const totalEl = line && line.querySelector('.cl-total');
    if (totalEl) totalEl.textContent = fmtMoney(round2(item.price * v));
    const count = S.cart.map((it, i) => (i === index ? { ...it, qty: v } : it));
    const total = S.cart.reduce(
      (s, it, i) => s + round2(it.price * (i === index ? v : it.qty)), 0
    );
    $('tCount').textContent = countText(count);
    $('tTotal').textContent = fmtMoney(round2(total));
  });
  $('cartLines').addEventListener('keydown', (e) => {
    if (!e.target.matches('.qty-input')) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      commitQty(Number(e.target.dataset.qty), e.target.value);
      focusScan();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      renderCart();
      focusScan();
    }
  });
  $('cartLines').addEventListener('change', (e) => {
    if (e.target.matches('.qty-input')) {
      commitQty(Number(e.target.dataset.qty), e.target.value);
    }
  });
  $('clearCart').addEventListener('click', () => clearCart(false));
  $('chooseCustomerBtn').addEventListener('click', () => openCustomers('select'));
  $('clearCustomerBtn').addEventListener('click', () => {
    S.customer = null;
    renderCart();
    focusScan();
  });
  $('payBtn').addEventListener('click', openPayment);
  if ($('pendingBtn')) $('pendingBtn').addEventListener('click', openPendingSales);
  if ($('pendingCartBtn')) $('pendingCartBtn').addEventListener('click', openPendingSales);
  if ($('pendingRefreshBtn')) $('pendingRefreshBtn').addEventListener('click', openPendingSales);
  $('salesBtn').addEventListener('click', openSales);
  if ($('cashMoveBtn')) $('cashMoveBtn').addEventListener('click', openCashMovements);
  $('closeShiftBtn').addEventListener('click', openShiftClose);

  // pago
  document.querySelectorAll('#payModal .method').forEach((btn) => {
    btn.addEventListener('click', () => setMethod(btn.dataset.method));
  });
  $('cashQuick').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-cash]');
    if (!btn) return;
    $('cashInput').value = btn.dataset.cash === 'exact'
      ? activePaymentTotal().toFixed(2) : btn.dataset.cash;
    updateChange();
    $('cashInput').focus();
  });
  $('cashInput').addEventListener('input', updateChange);
  $('cashInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmPayment(); }
  });
  $('refInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmPayment(); }
  });
  $('payConfirm').addEventListener('click', confirmPayment);
  $('payCancel').addEventListener('click', () => {
    S.payingPendingSale = null;
    closeModal('payModal');
  });
  $('newSaleBtn').addEventListener('click', () => {
    closeModal('doneModal');
    refreshQuiet();
    focusScan();
  });

  // tickets pendientes de cobro
  if ($('pendingBody')) {
    $('pendingBody').addEventListener('click', (e) => {
      const payBtn = e.target.closest('[data-paypending]');
      const cancelBtn = e.target.closest('[data-cancel]');
      const printBtn = e.target.closest('[data-print]');
      if (payBtn) {
        const id = Number(payBtn.dataset.paypending);
        const sale = (S.pendingSales || []).find((s) => Number(s.id) === id);
        if (sale) openPendingSalePayment(sale);
      } else if (cancelBtn) {
        openCancel(Number(cancelBtn.dataset.cancel), cancelBtn.dataset.folio);
      } else if (printBtn) {
        reprintSale(Number(printBtn.dataset.print), printBtn.dataset.token);
      }
    });
  }

  // ventas del día
  $('salesBody').addEventListener('click', (e) => {
    const payBtn = e.target.closest('[data-paypending]');
    const cancelBtn = e.target.closest('[data-cancel]');
    const printBtn = e.target.closest('[data-print]');
    const returnBtn = e.target.closest('[data-return]');
    if (payBtn) {
      const id = Number(payBtn.dataset.paypending);
      const sale = (S.pendingSales || []).find((s) => Number(s.id) === id);
      if (sale) openPendingSalePayment(sale);
      else {
        api('/api/pos/sales/pending').then((d) => {
          S.pendingSales = d.pending_sales || [];
          const found = S.pendingSales.find((s) => Number(s.id) === id);
          if (found) openPendingSalePayment(found);
        }).catch((err) => toast(err.message, 'bad'));
      }
    }
    else if (cancelBtn) openCancel(Number(cancelBtn.dataset.cancel), cancelBtn.dataset.folio);
    else if (printBtn) reprintSale(Number(printBtn.dataset.print), printBtn.dataset.token);
    else if (returnBtn) openReturn(Number(returnBtn.dataset.return), returnBtn.dataset.folio);
  });
  $('returnsBody').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-returncancel]');
    if (btn) openCancelReturn(Number(btn.dataset.returncancel), btn.dataset.folio);
  });
  $('exportBtn').addEventListener('click', exportSales);
  $('exportBtn2').addEventListener('click', exportSales);

  // clientes
  $('customersSearch').addEventListener('input', () => {
    clearTimeout(customerSearchTimer);
    customerSearchTimer = setTimeout(loadCustomers, 180);
  });
  $('customersSearch').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      clearTimeout(customerSearchTimer);
      loadCustomers();
    }
  });
  $('customersIncludeDeleted').addEventListener('change', loadCustomers);
  $('customersBody').addEventListener('click', (e) => {
    const detail = e.target.closest('[data-customer-detail]');
    if (detail) showCustomerDetail(Number(detail.dataset.customerDetail));
  });
  $('customerDetail').addEventListener('click', (e) => {
    const use = e.target.closest('[data-customer-use]');
    if (use) chooseCustomer(Number(use.dataset.customerUse));
  });
  $('customersCancelSelect').addEventListener('click', () => closeModal('customersModal'));

  // devoluciones
  $('returnSearch').addEventListener('click', () => lookupReturn(null));
  $('returnFolio').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); lookupReturn(null); }
  });
  $('returnLines').addEventListener('input', updateReturnTotal);
  $('returnConfirm').addEventListener('click', confirmReturn);

  // precio de emergencia
  $('priceSave').addEventListener('click', savePrice);
  $('pricesBtn').addEventListener('click', () => {
    $('priceList').hidden = !$('priceList').hidden;
    if (!$('priceList').hidden) loadPriceList();
  });
  $('priceList').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-revert]');
    if (btn) openPriceModal(btn.dataset.revert, 'revert');
  });

  // inventario
  $('notifBtn').addEventListener('click', openNotifications);
  $('invBtn').addEventListener('click', openInventory);
  $('invSearch').addEventListener('input', () => {
    clearTimeout(invTimer);
    invTimer = setTimeout(invSearch, 160);
  });
  $('invSearch').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(invTimer); invSearch(); }
  });
  $('invResults').addEventListener('click', (e) => {
    const edit = e.target.closest('[data-invedit]');
    if (edit) {
      startInvEdit(Number(edit.dataset.invedit));
      return;
    }
    const row = e.target.closest('[data-invidx]');
    if (!row) return;
    selectInvProduct(S.invResults[Number(row.dataset.invidx)]);
  });
  $('invNewBtn').addEventListener('click', () => {
    S.invResults = [];
    S.invSelected = -1;
    $('invResults').innerHTML = '';
    renderInvDetail(null);
    $('invMoves').innerHTML = '';
  });

  // unidades
  $('unitsBtn').addEventListener('click', openUnitsManager);
  $('unitAddBtn').addEventListener('click', addUnit);
  $('unitsList').addEventListener('click', (e) => {
    const off = e.target.closest('[data-unitoff]');
    const on = e.target.closest('[data-uniton]');
    if (off) toggleUnit(Number(off.dataset.unitoff), false);
    else if (on) toggleUnit(Number(on.dataset.uniton), true);
  });
  ['unitName', 'unitSymbol'].forEach((id) => {
    $(id).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); addUnit(); }
    });
  });

  // categorías y ubicaciones
  $('categoryAddBtn').addEventListener('click', addCategory);
  $('categoriesList').addEventListener('click', (e) => {
    const off = e.target.closest('[data-catoff]');
    const on = e.target.closest('[data-caton]');
    if (off) toggleCategory(Number(off.dataset.catoff), false);
    else if (on) toggleCategory(Number(on.dataset.caton), true);
  });
  $('categoryName').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addCategory(); }
  });
  $('locationAddBtn').addEventListener('click', addLocation);
  $('locationsList').addEventListener('click', (e) => {
    const off = e.target.closest('[data-locoff]');
    const on = e.target.closest('[data-locon]');
    if (off) toggleLocation(Number(off.dataset.locoff), false);
    else if (on) toggleLocation(Number(on.dataset.locon), true);
  });
  $('locationName').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addLocation(); }
  });
  $('reasonAddBtn').addEventListener('click', addReason);
  $('reasonsList').addEventListener('click', (e) => {
    const off = e.target.closest('[data-reasonoff]');
    const on = e.target.closest('[data-reasonon]');
    if (off) toggleReason(Number(off.dataset.reasonoff), false);
    else if (on) toggleReason(Number(on.dataset.reasonon), true);
  });
  $('reasonName').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addReason(); }
  });

  // usuarios
  $('usersBtn').addEventListener('click', openUsers);  $('createUserBtn').addEventListener('click', createUser);
  $('usersList').addEventListener('click', (e) => {
    const pinBtn = e.target.closest('[data-userpin]');
    const toggleBtn = e.target.closest('[data-usertoggle]');
    if (pinBtn) openPinModal(Number(pinBtn.dataset.userpin), pinBtn.dataset.username);
    else if (toggleBtn) toggleUser(Number(toggleBtn.dataset.usertoggle),
      toggleBtn.dataset.active === '1', toggleBtn.dataset.username);
  });
  $('pinModalSave').addEventListener('click', savePinModal);
  document.querySelectorAll('[data-close]').forEach((btn) => {
    btn.addEventListener('click', () => closeModal(btn.dataset.close));
  });

  // cancelación
  $('cancelConfirm').addEventListener('click', confirmCancel);
  $('cancelReason').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (S.security.manager_required_for_cancel) $('cancelPin').focus();
      else confirmCancel();
    }
  });
  $('cancelPin').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmCancel(); }
  });

  // corte
  $('countedCash').addEventListener('input', updateShiftDiff);
  $('confirmCloseShift').addEventListener('click', confirmShiftClose);
  $('countedCash').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmShiftClose(); }
  });
  // contador de denominaciones
  if ($('denomNextBtn')) $('denomNextBtn').addEventListener('click', denomNext);
  if ($('denomBackBtn')) $('denomBackBtn').addEventListener('click', denomBack);
  if ($('denomPrintBtn')) $('denomPrintBtn').addEventListener('click', denomPrint);

  // entradas y salidas de efectivo
  document.querySelectorAll('#cashModal [data-cashkind]').forEach((btn) => {
    btn.addEventListener('click', () => {
      setCashMoveKind(btn.dataset.cashkind);
      $('cashMoveAmount').focus();
    });
  });
  if ($('cashReasonQuick')) {
    $('cashReasonQuick').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-reason]');
      if (!btn) return;
      $('cashMoveReason').value = btn.dataset.reason;
      $('cashMoveReason').focus();
    });
  }
  if ($('cashMoveAmount')) {
    $('cashMoveAmount').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); $('cashMoveReason').focus(); }
    });
  }
  if ($('cashMoveReason')) {
    $('cashMoveReason').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); saveCashMovement(); }
    });
  }
  if ($('cashMoveSave')) {
    $('cashMoveSave').addEventListener('click', saveCashMovement);
  }
  if ($('cashMoveCancelEdit')) {
    $('cashMoveCancelEdit').addEventListener('click', () => {
      resetCashMoveForm();
      $('cashMoveAmount').focus();
    });
  }
  if ($('cashMovesBody')) {
    $('cashMovesBody').addEventListener('click', (e) => {
      const editBtn = e.target.closest('[data-cashedit]');
      const delBtn = e.target.closest('[data-cashdel]');
      if (editBtn) startEditCashMovement(Number(editBtn.dataset.cashedit));
      else if (delBtn) removeCashMovement(Number(delBtn.dataset.cashdel));
    });
  }
  if ($('cashMoveMenuBtn')) {
    $('cashMoveMenuBtn').addEventListener('click', () => {
      closeModal('menuModal');
      openCashMovements();
    });
  }

  // menú
  $('menuBtn').addEventListener('click', () => {
    $('menuStatus').textContent = '';
    $('pinChange').hidden = true;
    $('reprintBtn').hidden = !S.lastSale;
    openModal('menuModal');
  });
  $('customersBtn').addEventListener('click', () => openCustomers('browse'));
  $('testPrintBtn').addEventListener('click', testPrint);
  if ($('printerChip')) $('printerChip').addEventListener('click', openPrinterModal);
  if ($('localPrinterSaveBtn')) $('localPrinterSaveBtn').addEventListener('click', saveLocalPrinter);
  if ($('localPrinterTestBtn')) $('localPrinterTestBtn').addEventListener('click', async () => {
    await saveLocalPrinter();
    await testPrint();
  });
  $('backupBtn').addEventListener('click', backupNow);
  $('changePinBtn').addEventListener('click', () => {
    $('pinChange').hidden = !$('pinChange').hidden;
    if (!$('pinChange').hidden) $('currentPin').focus();
  });
  $('savePinBtn').addEventListener('click', savePin);
  $('reprintBtn').addEventListener('click', () => {
    if (S.lastSale) { reprintSale(S.lastSale.id, S.lastSale.ticket_token); closeModal('menuModal'); }
  });
  $('logoutBtn').addEventListener('click', () => {
    if (confirm('¿Cerrar la sesión de caja?')) logout();
  });

  // teclado global
  document.addEventListener('keydown', (e) => {
    const loginVisible = !$('loginScreen').hidden;
    if (loginVisible) {
      if (e.key === 'Enter') { e.preventDefault(); if ($('bootstrapPanel').hidden) submitPin(); }
      else if (/^[0-9]$/.test(e.key) && $('bootstrapPanel').hidden
        && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        setPin(S.pin + e.key);
      } else if (e.key === 'Backspace' && $('bootstrapPanel').hidden
        && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        setPin(S.pin.slice(0, -1));
      }
      return;
    }

    const modal = visibleModal();
    if (e.key === 'Escape') {
      if (modal) { e.preventDefault(); closeModal(modal); }
      else if (!$('shiftScreen').hidden) { /* no cerrar */ }
      else if (document.activeElement === $('scan')) {
        $('scan').value = '';
        S.search = null;
        renderResultsIdle();
      }
      return;
    }
    if (!$('posMain').hidden) {
      if (e.key === 'F2') { e.preventDefault(); focusScan(); }
      else if (e.key === 'F4' && !modal) { e.preventDefault(); openPayment(); }
      else if (e.key === 'F6' && !modal && isCashRegister()) { e.preventDefault(); openPendingSales(); }
      else if (e.key === 'F7' && !modal) { e.preventDefault(); openCashMovements(); }
      else if (e.key === 'F8' && !modal) { e.preventDefault(); clearCart(false); }
      else if (e.key === 'F9' && !modal) { e.preventDefault(); openSales(); }
      else if (e.key === 'F10' && !modal) { e.preventDefault(); openInventory(); }
      else if (e.key.length === 1 && !modal && document.activeElement !== $('scan')
        && document.activeElement.tagName !== 'INPUT') {
        $('scan').focus();
      }
    }
  });

  window.addEventListener('focus', () => {
    const active = document.activeElement;
    if (active && active.classList && active.classList.contains('qty-input')) return;
    focusScan();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshQuiet(); });
  document.addEventListener('click', (e) => {
    if (e.target.closest('.overlay') || $('posMain').hidden) return;
    if (e.target.matches('input, select, textarea')) return;
    focusScan();
  });
}

/* ------------------------------------------------------------ arranque */
async function init() {
  installNumberWheelLock(); // la rueda no altera campos numéricos
  wire();
  tickClock();
  setInterval(tickClock, 1000);
  setInterval(refreshQuiet, 6000);
  setInterval(() => pingImpresoraLocal(), 5000);   // impresora de ESTA PC
  renderCart();

  try {
    S.boot = await api('/api/pos/info');
  } catch (err) {
    $('loginError').textContent = err.message;
    setInterval(() => location.reload(), 15000);
    return;
  }
  S.version = S.boot.version;
  if (!S.boot.configured) { showBootstrap(); return; }
  pingImpresoraLocal(true);
  if (!S.token) { showLogin(); return; }
  await refresh();
}

document.addEventListener('DOMContentLoaded', init);
