'use strict';

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

