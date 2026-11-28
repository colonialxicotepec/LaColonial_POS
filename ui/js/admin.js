'use strict';

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
  'customersModal', 'proveedoresModal', 'xicoParkModal'];

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

