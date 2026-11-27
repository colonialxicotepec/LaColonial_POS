'use strict';

/* ------------------------------------------------------------ XICO-PARK */

function xicoTodayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function setXicoKind(kind) {
  const k = ['salida', 'gasto', 'entrada'].includes(kind) ? kind : 'salida';
  if ($('xicoKind')) $('xicoKind').value = k;
  document.querySelectorAll('#xicoKinds button[data-xicokind]').forEach((b) => {
    b.classList.toggle('active', b.dataset.xicokind === k);
  });
  const labels = { salida: 'Registrar salida', gasto: 'Registrar gasto', entrada: 'Registrar entrada' };
  if ($('xicoSaveBtn')) $('xicoSaveBtn').textContent = labels[k] || 'Registrar';
}

async function openXicoPark() {
  if (!canManage()) {
    toast('Solo administrador o gerente puede acceder a XICO-PARK.', 'bad');
    return;
  }
  if (!$('xicoDate').value) $('xicoDate').value = xicoTodayIso();
  $('xicoError').textContent = '';
  setXicoKind('salida');
  openModal('xicoParkModal');
  await loadXicoPark();
  setTimeout(() => $('xicoAmount').focus(), 40);
}

async function loadXicoPark() {
  const day = $('xicoDate').value || xicoTodayIso();
  try {
    const data = await api('/api/pos/xico-park/summary?day=' + encodeURIComponent(day));
    renderXicoPark(data);
  } catch (err) {
    $('xicoError').textContent = err.message;
  }
}

function renderXicoPark(data) {
  const t = data.totals || {};
  $('xicoTotEntradas').textContent = fmtMoney(t.entradas || 0);
  $('xicoTotSalidas').textContent = fmtMoney(t.salidas || 0);
  $('xicoTotGastos').textContent = fmtMoney(t.gastos || 0);
  $('xicoTotSG').textContent = fmtMoney(t.salidas_gastos || 0);
  $('xicoNeto').textContent = fmtMoney(t.neto || 0);

  const body = $('xicoBody');
  const rows = data.movements || [];
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:18px">Sin movimientos registrados para esta fecha.</td></tr>';
    return;
  }
  const badge = {
    entrada: '<span class="chip shift-open">Entrada</span>',
    salida: '<span class="chip">Salida</span>',
    gasto: '<span class="chip" style="background:#fee2e2;color:#991b1b">Gasto</span>',
  };
  body.innerHTML = rows.map((m) => {
    const canceled = !m.active;
    const style = canceled ? ' style="opacity:0.45;text-decoration:line-through"' : '';
    const hour = (m.created_at || '').slice(11, 16);
    return `<tr${style}>
      <td>${esc(hour)}</td>
      <td>${badge[m.kind] || esc(m.kind)}</td>
      <td>${esc(m.concept)}${canceled ? ` <span class="muted small">(Anulado: ${esc(m.cancel_reason || '')})</span>` : ''}</td>
      <td class="muted small">${esc(m.user_name || '')}</td>
      <td class="num"><b>${fmtMoney(m.amount)}</b></td>
      <td class="num">${canceled ? '' : `<button type="button" class="ghost small" onclick="cancelXicoParkMovement(${m.id})">Anular</button>`}</td>
    </tr>`;
  }).join('');
}

async function saveXicoParkMovement() {
  $('xicoError').textContent = '';
  const kind = ($('xicoKind') && $('xicoKind').value) || 'salida';
  const amount = parseFloat($('xicoAmount').value);
  const concept = ($('xicoConcept').value || '').trim();
  const day = $('xicoDate').value || xicoTodayIso();
  if (!amount || amount <= 0) {
    $('xicoError').textContent = 'Escribe un monto válido mayor a cero.';
    $('xicoAmount').focus();
    return;
  }
  if (!concept) {
    $('xicoError').textContent = 'Escribe el concepto o motivo.';
    $('xicoConcept').focus();
    return;
  }
  try {
    const data = await api('/api/pos/xico-park/movement', { kind, amount, concept, day });
    $('xicoAmount').value = '';
    $('xicoConcept').value = '';
    renderXicoPark(data);
    toast('Movimiento guardado en XICO-PARK.', 'ok');
    $('xicoAmount').focus();
  } catch (err) {
    $('xicoError').textContent = err.message;
  }
}

async function cancelXicoParkMovement(id) {
  const reason = prompt('Motivo de anulación (opcional):', 'Error de captura');
  if (reason === null) return;
  try {
    const data = await api('/api/pos/xico-park/cancel', { id, reason });
    renderXicoPark(data);
    toast('Movimiento anulado.', 'ok');
  } catch (err) {
    toast(err.message, 'bad');
  }
}

async function printXicoPark() {
  const day = $('xicoDate').value || xicoTodayIso();
  try {
    const local = await pingImpresoraLocal();
    const data = await api('/api/pos/xico-park/print', { day, impresion_local: local });
    let ok = data.printed;
    let printer = data.printer;
    if (data.escpos_b64) {
      const res = await imprimirRawLocal(data.escpos_b64);
      if (res && res.ok) { ok = true; printer = res.impresora || printer; }
    } else if (local && data.ticket_lines && data.ticket_lines.length) {
      const res = await imprimirEnLocal(data.ticket_lines, data.ticket_text, false);
      if (res && res.ok) { ok = true; printer = res.impresora || printer; }
    }
    if (ok) toast(`Resumen XICO-PARK impreso${printer ? ' en ' + printer : ''}.`, 'ok');
    else toast(data.print_error || 'No se pudo imprimir el resumen.', 'bad');
  } catch (err) {
    toast(err.message, 'bad');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if ($('xicoParkBtn')) $('xicoParkBtn').addEventListener('click', openXicoPark);
  if ($('xicoDate')) $('xicoDate').addEventListener('change', loadXicoPark);
  if ($('xicoSaveBtn')) $('xicoSaveBtn').addEventListener('click', saveXicoParkMovement);
  if ($('xicoPrintBtn')) $('xicoPrintBtn').addEventListener('click', printXicoPark);
  document.querySelectorAll('#xicoKinds button[data-xicokind]').forEach((btn) => {
    btn.addEventListener('click', () => setXicoKind(btn.dataset.xicokind));
  });
  if ($('xicoConcept')) {
    $('xicoConcept').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); saveXicoParkMovement(); }
    });
  }
});
