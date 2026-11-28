/* ============================================================
 * Módulo de Proveedores
 * ============================================================ */
let proveedoresSearchTimer = null;
let proveedoresSearchSeq = 0;

function renderProveedorPermissions() {
  const manager = canManage();
  $('proveedorNewBtn').hidden = !manager;
  $('proveedorNewBtn').disabled = S.busy;
  $('proveedoresSubtitle').textContent = manager
    ? 'Consulta, da de alta, edita, elimina o reactiva proveedores.'
    : 'Consulta proveedores en modo solo lectura.';
  if (!manager) $('proveedorFormWrap').hidden = true;
  if (!$('proveedoresModal').hidden) renderProveedores();
}

function setProveedoresBusy(busy) {
  S.busy = busy;
  renderProveedorPermissions();
  const disabled = busy || !canManage();
  $('proveedorSaveBtn').disabled = disabled;
  $('proveedoresBody').querySelectorAll('button').forEach((btn) => {
    btn.disabled = disabled;
  });
}

function scheduleProveedoresSearch() {
  clearTimeout(proveedoresSearchTimer);
  // Invalida desde que cambia el texto, no solo cuando sale la petición nueva.
  proveedoresSearchSeq += 1;
  proveedoresSearchTimer = setTimeout(loadProveedores, 180);
}

function openProveedores() {
  clearTimeout(proveedoresSearchTimer);
  proveedoresSearchSeq += 1;
  $('proveedoresSearch').value = '';
  $('proveedoresIncludeDeleted').checked = false;
  $('proveedorFormWrap').hidden = true;
  S.proveedorRows = [];
  $('proveedoresBody').innerHTML = '';
  $('proveedoresStatus').textContent = 'Cargando proveedores…';
  renderProveedorPermissions();
  openModal('proveedoresModal');
  loadProveedores();
  setTimeout(() => $('proveedoresSearch').focus(), 50);
}

async function loadProveedores() {
  const seq = ++proveedoresSearchSeq;
  const query = $('proveedoresSearch').value.trim();
  const deleted = $('proveedoresIncludeDeleted').checked ? '1' : '0';
  $('proveedoresStatus').textContent = 'Buscando…';
  try {
    const data = await api(`/api/pos/proveedores?q=${encodeURIComponent(query)}&eliminados=${deleted}&limit=200`);
    if (seq !== proveedoresSearchSeq) return;
    S.proveedorRows = data.proveedores || [];
    renderProveedores();
    $('proveedoresStatus').textContent = `${data.total || 0} proveedor(es) encontrado(s).`;
  } catch (err) {
    if (seq !== proveedoresSearchSeq) return;
    S.proveedorRows = [];
    $('proveedoresBody').innerHTML = '';
    $('proveedoresStatus').textContent = err.message || 'No se pudieron cargar los proveedores.';
  }
}

function renderProveedores() {
  const body = $('proveedoresBody');
  const rows = S.proveedorRows || [];
  const manager = canManage();
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="muted">Sin proveedores encontrados.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((p) => {
    const contacto = [p.nombre_contacto, p.apellido_contacto].filter(Boolean).join(' ') || '—';
    const estado = p.activo ? 'Activo' : 'Inactivo';
    const disabled = S.busy ? ' disabled' : '';
    const acciones = manager
      ? `<button type="button" class="ghost small" data-prov-edit="${esc(p.id)}"${disabled}>Editar</button>
         ${p.activo
           ? `<button type="button" class="ghost danger-soft small" data-prov-del="${esc(p.id)}"${disabled}>Eliminar</button>`
           : `<button type="button" class="ghost small" data-prov-reactivate="${esc(p.id)}"${disabled}>Reactivar</button>`}`
      : '<span class="muted">—</span>';
    return `<tr>
      <td><strong>${esc(p.empresa)}</strong></td>
      <td>${esc(contacto)}</td>
      <td>${esc(p.rfc || '—')}</td>
      <td>${esc(p.telefono || '—')}</td>
      <td>${esc(p.email || '—')}</td>
      <td>${estado}</td>
      <td style="white-space:nowrap;">${acciones}</td>
    </tr>`;
  }).join('');
}

function openProveedorForm(prov) {
  if (!canManage() || S.busy) return;
  const form = $('proveedorForm');
  form.reset();
  $('proveedorFormTitle').textContent = prov ? `Editar proveedor #${prov.id}` : 'Nuevo proveedor';
  $('provId').value = prov ? prov.id : '';
  $('provEmpresa').value = prov ? (prov.empresa || '') : '';
  $('provNombre').value = prov ? (prov.nombre_contacto || '') : '';
  $('provApellido').value = prov ? (prov.apellido_contacto || '') : '';
  $('provRfc').value = prov ? (prov.rfc || '') : '';
  $('provTelefono').value = prov ? (prov.telefono || '') : '';
  $('provEmail').value = prov ? (prov.email || '') : '';
  $('provDireccion').value = prov ? (prov.direccion || '') : '';
  $('provCiudad').value = prov ? (prov.ciudad || '') : '';
  $('provEstado').value = prov ? (prov.estado || '') : '';
  $('provCp').value = prov ? (prov.cp || '') : '';
  $('provSaldo').value = prov ? (prov.saldo ?? 0) : 0;
  $('provNotas').value = prov ? (prov.notas || '') : '';
  $('proveedoresStatus').textContent = '';
  $('proveedorFormWrap').hidden = false;
  $('provEmpresa').focus();
}

async function saveProveedor() {
  if (!canManage() || S.busy) return;
  const form = $('proveedorForm');
  if (!form.reportValidity()) return;

  const id = $('provId').value.trim();
  const payload = {
    empresa: $('provEmpresa').value.trim(),
    nombre_contacto: $('provNombre').value.trim(),
    apellido_contacto: $('provApellido').value.trim(),
    rfc: $('provRfc').value.trim(),
    telefono: $('provTelefono').value.trim(),
    email: $('provEmail').value.trim(),
    direccion: $('provDireccion').value.trim(),
    ciudad: $('provCiudad').value.trim(),
    estado: $('provEstado').value.trim(),
    cp: $('provCp').value.trim(),
    saldo: Number($('provSaldo').value || 0),
    notas: $('provNotas').value.trim(),
  };
  if (id) payload.id = Number(id);
  if (!payload.empresa) {
    $('proveedoresStatus').textContent = 'El nombre de la empresa es obligatorio.';
    $('provEmpresa').focus();
    return;
  }

  const url = id ? '/api/pos/proveedores/update' : '/api/pos/proveedores/create';
  setProveedoresBusy(true);
  $('proveedoresStatus').textContent = 'Guardando…';
  try {
    await api(url, payload);
    form.reset();
    $('proveedorFormWrap').hidden = true;
    $('proveedoresStatus').textContent = id ? 'Proveedor actualizado.' : 'Proveedor creado.';
    await loadProveedores();
  } catch (err) {
    $('proveedoresStatus').textContent = err.message || 'Error al guardar proveedor.';
  } finally {
    setProveedoresBusy(false);
  }
}

async function deleteProveedor(id) {
  if (!canManage() || S.busy) return;
  const pid = Number(id);
  if (!Number.isFinite(pid) || pid <= 0) return;
  if (!confirm('¿Eliminar (desactivar) este proveedor?')) return;

  setProveedoresBusy(true);
  $('proveedoresStatus').textContent = 'Eliminando proveedor…';
  try {
    await api('/api/pos/proveedores/delete', { id: pid });
    $('proveedoresStatus').textContent = 'Proveedor eliminado.';
    await loadProveedores();
  } catch (err) {
    $('proveedoresStatus').textContent = err.message || 'Error al eliminar proveedor.';
  } finally {
    setProveedoresBusy(false);
  }
}

async function reactivateProveedor(id) {
  if (!canManage() || S.busy) return;
  const pid = Number(id);
  if (!Number.isFinite(pid) || pid <= 0) return;
  if (!confirm('¿Reactivar este proveedor?')) return;

  setProveedoresBusy(true);
  $('proveedoresStatus').textContent = 'Reactivando proveedor…';
  try {
    await api('/api/pos/proveedores/update', { id: pid, activo: true });
    $('proveedoresStatus').textContent = 'Proveedor reactivado.';
    await loadProveedores();
  } catch (err) {
    $('proveedoresStatus').textContent = err.message || 'Error al reactivar proveedor.';
  } finally {
    setProveedoresBusy(false);
  }
}
