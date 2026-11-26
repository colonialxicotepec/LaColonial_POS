'use strict';

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

