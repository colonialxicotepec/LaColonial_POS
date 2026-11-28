'use strict';

/* ------------------------------------------------------------ sesión */
function logoutLocal() {
  S.token = '';
  S.user = null;
  S.shift = null;
  S.proveedorRows = [];
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
    const editingQty = !!(document.activeElement
      && document.activeElement.matches && document.activeElement.matches('.qty-input'));
    if (S.shift && !editingQty) renderCart();
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
  renderProveedorPermissions();
  $('invBtn').hidden = !manager;
  if ($('xicoParkBtn')) $('xicoParkBtn').hidden = !manager;
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

