import json
import math

from core.db import connect, now_text


FIELD_MAX_LENGTHS = {
    'empresa': 200,
    'nombre_contacto': 100,
    'apellido_contacto': 100,
    'rfc': 20,
    'telefono': 120,
    'email': 254,
    'direccion': 300,
    'ciudad': 100,
    'estado': 100,
    'cp': 20,
    'notas': 2000,
}

FIELD_LABELS = {
    'empresa': 'El nombre de la empresa o proveedor',
    'nombre_contacto': 'El nombre de contacto',
    'apellido_contacto': 'El apellido de contacto',
    'rfc': 'El RFC',
    'telefono': 'El teléfono',
    'email': 'El correo electrónico',
    'direccion': 'La dirección',
    'ciudad': 'La ciudad',
    'estado': 'El estado',
    'cp': 'El código postal',
    'notas': 'Las notas',
}

_AUDIT_FIELDS = (
    'empresa', 'nombre_contacto', 'apellido_contacto', 'rfc', 'telefono',
    'email', 'direccion', 'ciudad', 'estado', 'cp', 'saldo', 'notas', 'activo',
)


def _clean(value, label=None, max_length=None):
    if isinstance(value, (dict, list, tuple, set)):
        raise ValueError(f'{label or "El valor"} no es válido.')
    text = ' '.join(str(value if value is not None else '').split())
    if max_length is not None and len(text) > max_length:
        raise ValueError(
            f'{label or "El campo"} no puede tener más de {max_length} caracteres.'
        )
    return text


def _text_field(value, key, upper=False):
    text = _clean(value, FIELD_LABELS[key], FIELD_MAX_LENGTHS[key])
    return text.upper() if upper else text


def _parse_saldo(value, default=0.0):
    if isinstance(value, bool):
        raise ValueError('El saldo debe ser un número válido.')
    try:
        num = float(default if value is None or value == '' else value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('El saldo debe ser un número válido.')
    if not math.isfinite(num):
        raise ValueError('El saldo debe ser un número finito.')
    return num


def _parse_activo(value):
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        if value == 0:
            return 0
        if value == 1:
            return 1
    elif isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in ('1', 'true', 'si', 'sí', 'activo'):
            return 1
        if normalized in ('0', 'false', 'no', 'inactivo'):
            return 0
    raise ValueError(
        'El campo activo debe ser true/false, si/no, sí, activo/inactivo o 0/1.'
    )


def _row_to_dict(row):
    return {
        'id': row['id'],
        'legacy_id': row['legacy_id'],
        'empresa': row['empresa'],
        'nombre_contacto': row['nombre_contacto'] or '',
        'apellido_contacto': row['apellido_contacto'] or '',
        'rfc': row['rfc'] or '',
        'telefono': row['telefono'] or '',
        'email': row['email'] or '',
        'direccion': row['direccion'] or '',
        'ciudad': row['ciudad'] or '',
        'estado': row['estado'] or '',
        'cp': row['cp'] or '',
        'saldo': float(row['saldo'] or 0),
        'notas': row['notas'] or '',
        'activo': bool(row['activo']),
        'origen': row['origen'] or 'pos',
        'creado_en': row['creado_en'] or '',
        'actualizado_en': row['actualizado_en'] or '',
    }


def _audit_snapshot(row):
    supplier = _row_to_dict(row)
    return {field: supplier[field] for field in _AUDIT_FIELDS}


def _audit_changes(before, after):
    return {
        field: {'antes': before[field], 'despues': after[field]}
        for field in _AUDIT_FIELDS
        if before[field] != after[field]
    }


def _insert_audit(conn, stamp, user_id, action, detail):
    conn.execute(
        'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
        (stamp, user_id, action, json.dumps(detail, ensure_ascii=False, sort_keys=True)),
    )


def search_proveedores(query='', include_deleted=False, limit=100):
    q = _clean(query)[:120]
    limit = min(max(int(limit or 100), 1), 500)
    conn = connect()
    try:
        where = []
        params = []
        if not include_deleted:
            where.append('activo = 1')
        if q:
            # ESCAPE evita que % y _ del usuario funcionen como comodines.
            escaped = q.replace('!', '!!').replace('%', '!%').replace('_', '!_')
            like = f'%{escaped}%'
            where.append(
                """(empresa LIKE ? ESCAPE '!'
                    OR COALESCE(nombre_contacto, '') LIKE ? ESCAPE '!'
                    OR COALESCE(apellido_contacto, '') LIKE ? ESCAPE '!'
                    OR (TRIM(COALESCE(nombre_contacto, '') || ' ' ||
                             COALESCE(apellido_contacto, ''))) LIKE ? ESCAPE '!'
                    OR COALESCE(rfc, '') LIKE ? ESCAPE '!'
                    OR COALESCE(telefono, '') LIKE ? ESCAPE '!'
                    OR COALESCE(email, '') LIKE ? ESCAPE '!')"""
            )
            params.extend([like] * 7)
        where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
        total = conn.execute(f'SELECT COUNT(*) FROM proveedores {where_sql}', params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT * FROM proveedores
                {where_sql}
                ORDER BY activo DESC, empresa COLLATE NOCASE ASC, id ASC
                LIMIT ?""",
            params + [limit],
        ).fetchall()
        return {
            'proveedores': [_row_to_dict(r) for r in rows],
            'total': int(total),
        }
    finally:
        conn.close()


def get_proveedor(proveedor_id):
    conn = connect()
    try:
        row = conn.execute('SELECT * FROM proveedores WHERE id = ?', (int(proveedor_id),)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def create_proveedor(data, user_id=None):
    if not isinstance(data, dict):
        raise ValueError('Datos inválidos.')
    empresa = _text_field(data.get('empresa'), 'empresa')
    if not empresa:
        raise ValueError('El nombre de la empresa o proveedor es obligatorio.')
    values = {
        key: _text_field(data.get(key), key, upper=(key == 'rfc')) or None
        for key in FIELD_MAX_LENGTHS
        if key != 'empresa'
    }
    saldo = _parse_saldo(data.get('saldo'))
    activo = _parse_activo(data.get('activo', True))
    stamp = now_text()
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        cur = conn.execute(
            """INSERT INTO proveedores(
                   empresa, nombre_contacto, apellido_contacto, rfc, telefono,
                   email, direccion, ciudad, estado, cp, saldo, notas,
                   activo, origen, creado_en, actualizado_en
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pos', ?, ?)""",
            (empresa, values['nombre_contacto'], values['apellido_contacto'],
             values['rfc'], values['telefono'], values['email'], values['direccion'],
             values['ciudad'], values['estado'], values['cp'], saldo, values['notas'],
             activo, stamp, stamp),
        )
        new_id = cur.lastrowid
        created = conn.execute('SELECT * FROM proveedores WHERE id = ?', (new_id,)).fetchone()
        _insert_audit(
            conn, stamp, user_id, 'proveedor_create',
            {'id': new_id, 'despues': _audit_snapshot(created)},
        )
        conn.commit()
        return _row_to_dict(created)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_proveedor(proveedor_id, data, user_id=None):
    if not isinstance(data, dict):
        raise ValueError('Datos inválidos.')
    pid = int(proveedor_id)
    stamp = now_text()
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT * FROM proveedores WHERE id = ?', (pid,)).fetchone()
        if not existing:
            raise ValueError('Proveedor no encontrado.')

        empresa = (
            _text_field(data['empresa'], 'empresa')
            if 'empresa' in data else existing['empresa']
        )
        if not empresa:
            raise ValueError('El nombre de la empresa o proveedor es obligatorio.')

        def opt_str(key, upper=False):
            if key not in data:
                return existing[key]
            return _text_field(data[key], key, upper=upper) or None

        nombre_contacto = opt_str('nombre_contacto')
        apellido_contacto = opt_str('apellido_contacto')
        rfc = opt_str('rfc', upper=True)
        telefono = opt_str('telefono')
        email = opt_str('email')
        direccion = opt_str('direccion')
        ciudad = opt_str('ciudad')
        estado = opt_str('estado')
        cp = opt_str('cp')
        notas = opt_str('notas')
        saldo = (
            _parse_saldo(data['saldo'], default=existing['saldo'])
            if 'saldo' in data else float(existing['saldo'] or 0)
        )
        activo = (
            _parse_activo(data['activo'])
            if 'activo' in data else int(existing['activo'])
        )

        conn.execute(
            """UPDATE proveedores SET
                   empresa = ?, nombre_contacto = ?, apellido_contacto = ?,
                   rfc = ?, telefono = ?, email = ?, direccion = ?,
                   ciudad = ?, estado = ?, cp = ?, saldo = ?, notas = ?,
                   activo = ?, actualizado_en = ?
               WHERE id = ?""",
            (empresa, nombre_contacto, apellido_contacto, rfc, telefono,
             email, direccion, ciudad, estado, cp, saldo, notas,
             activo, stamp, pid),
        )
        updated = conn.execute('SELECT * FROM proveedores WHERE id = ?', (pid,)).fetchone()
        before = _audit_snapshot(existing)
        after = _audit_snapshot(updated)
        _insert_audit(
            conn, stamp, user_id, 'proveedor_update',
            {'id': pid, 'cambios': _audit_changes(before, after)},
        )
        conn.commit()
        return _row_to_dict(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_proveedor(proveedor_id, user_id=None):
    pid = int(proveedor_id)
    stamp = now_text()
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT * FROM proveedores WHERE id = ?', (pid,)).fetchone()
        if not existing:
            raise ValueError('Proveedor no encontrado.')
        conn.execute(
            'UPDATE proveedores SET activo = 0, actualizado_en = ? WHERE id = ?',
            (stamp, pid),
        )
        deleted = conn.execute('SELECT * FROM proveedores WHERE id = ?', (pid,)).fetchone()
        before = _audit_snapshot(existing)
        after = _audit_snapshot(deleted)
        _insert_audit(
            conn, stamp, user_id, 'proveedor_delete',
            {'id': pid, 'cambios': _audit_changes(before, after)},
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
