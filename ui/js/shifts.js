'use strict';

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
    // Si el servidor envió los bytes ESC/POS pre-renderizados, usarlos directamente
    // (preserva el formato: doble tamaño, negritas y raster del TOTAL), como se
    // hace con los tickets de venta. Fallback a texto plano si no vienen.
    if (local && reportText) {
      let res = null;
      if (data.print && data.print.escpos_b64) {
        res = await imprimirRawLocal(data.print.escpos_b64);
      } else {
        res = await imprimirEnLocal(null, reportText);
      }
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

