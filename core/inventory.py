"""Inventario propio, unidades, categorías, ubicaciones, motivos y notificaciones."""

import datetime
import hashlib
import hmac
import json
import math
import os
import secrets
import shutil
import sqlite3
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from core.db import (
    connect, row_to_dict, money, now_text, today_text, ticket_token,
    DEFAULT_UNIT, DEFAULT_UNIT_SYMBOL, DEFAULT_CATEGORY, DEFAULT_LOCATION,
    DEFAULT_REASON,
)
from core.users import audit

# ---------------------------------------------------------------- inventario propio

def catalog_version():
    """Versión del inventario propio; cambia cuando se toca un producto."""
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='catalog_version'").fetchone()
        return row['value'] if row else '0'
    finally:
        conn.close()


def bump_catalog_version(conn=None):
    """Incrementa la versión del inventario (el catálogo en memoria se recarga)."""
    if conn is not None:
        row = conn.execute("SELECT value FROM meta WHERE key='catalog_version'").fetchone()
        try:
            nxt = str(int(row['value']) + 1) if row else '1'
        except (TypeError, ValueError):
            nxt = '1'
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('catalog_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (nxt,))
        return nxt
    own = connect()
    try:
        row = own.execute("SELECT value FROM meta WHERE key='catalog_version'").fetchone()
        try:
            nxt = str(int(row['value']) + 1) if row else '1'
        except (TypeError, ValueError):
            nxt = '1'
        own.execute(
            "INSERT INTO meta(key,value) VALUES('catalog_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (nxt,))
        own.commit()
        return nxt
    finally:
        own.close()


def get_product(product_id):
    conn = connect()
    try:
        row = conn.execute(
            """SELECT p.*, b.name AS base_name, b.sku AS base_sku,
                      b.stock AS base_stock, b.active AS base_active
               FROM products p LEFT JOIN products b ON b.id = p.base_product_id
               WHERE p.id=?""", (product_id,)
        ).fetchone()
        data = row_to_dict(row)
        if data and data.get('base_product_id'):
            # Para un empaque, el stock visible es el del base convertido.
            base_stock = float(data.get('base_stock') or 0)
            factor = float(data.get('base_qty') or 1)
            data['own_stock'] = data.get('stock')
            data['stock'] = round(base_stock / factor, 3)
        return data
    finally:
        conn.close()


def product_by_legacy(legacy_item_id):
    conn = connect()
    try:
        row = conn.execute('SELECT * FROM products WHERE legacy_item_id=?', (legacy_item_id,)).fetchone()
        return row_to_dict(row)
    finally:
        conn.close()


def product_by_sku(sku):
    conn = connect()
    try:
        row = conn.execute('SELECT * FROM products WHERE sku=? COLLATE NOCASE', (sku,)).fetchone()
        return row_to_dict(row)
    finally:
        conn.close()


def next_manual_sku():
    """SKU manual consecutivo (M-00001, M-00002…), corroborado como libre."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT sku FROM products WHERE sku LIKE 'M-%' COLLATE NOCASE"
        ).fetchall()
        max_num = 0
        existing = set()
        for r in rows:
            raw = str(r['sku'] or '').strip().upper()
            existing.add(raw)
            if raw.startswith('M-'):
                rest = raw[2:].split('-', 1)[0]
                if rest.isdigit() and len(rest) <= 5 and rest.startswith('0'):
                    num = int(rest)
                    if num > max_num:
                        max_num = num
                elif rest.isdigit() and int(rest) < 1000:
                    num = int(rest)
                    if num > max_num:
                        max_num = num
        nxt = max_num + 1
        while True:
            candidate = f'M-{nxt:05d}'
            if candidate not in existing and not conn.execute(
                'SELECT 1 FROM products WHERE sku=? COLLATE NOCASE', (candidate,)
            ).fetchone():
                return candidate
            nxt += 1
    finally:
        conn.close()


def _resolve_unit(conn, name=None):
    """Devuelve (nombre, símbolo) de una unidad activa."""
    name = str(name or '').strip()
    if name:
        row = conn.execute(
            'SELECT name, symbol FROM units WHERE name=? COLLATE NOCASE AND active=1',
            (name,),
        ).fetchone()
        if not row:
            raise ValueError(f'La unidad “{name}” no existe o fue eliminada.')
        return row['name'], row['symbol']
    row = conn.execute(
        'SELECT name, symbol FROM units WHERE name=? COLLATE NOCASE', (DEFAULT_UNIT,)
    ).fetchone()
    if not row:
        return DEFAULT_UNIT, DEFAULT_UNIT_SYMBOL
    return row['name'], row['symbol']


def list_units(include_inactive=False):
    conn = connect()
    try:
        sql = """SELECT u.*,
                        (SELECT COUNT(*) FROM products p WHERE p.unit = u.name) AS product_count
                 FROM units u"""
        if not include_inactive:
            sql += ' WHERE u.active = 1'
        sql += ' ORDER BY u.id'
        return [row_to_dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def create_unit(user_id, name, symbol):
    name = str(name or '').strip()
    symbol = str(symbol or '').strip().lower()
    if not (2 <= len(name) <= 20):
        raise ValueError('El nombre de la unidad debe tener entre 2 y 20 caracteres.')
    if not symbol:
        raise ValueError('Escribe una abreviatura (ejemplo: kg, gr, pza).')
    symbol = ''.join(ch for ch in symbol if ch.isalnum() or ch in '.-')
    if not (1 <= len(symbol) <= 4):
        raise ValueError('La abreviatura debe tener de 1 a 4 caracteres (ejemplo: kg).')

    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        dup_name = conn.execute(
            'SELECT name FROM units WHERE name=? COLLATE NOCASE', (name,)
        ).fetchone()
        if dup_name:
            raise ValueError(f'Ya existe la unidad “{dup_name["name"]}”.')
        dup_symbol = conn.execute(
            'SELECT name FROM units WHERE symbol=? COLLATE NOCASE', (symbol,)
        ).fetchone()
        if dup_symbol:
            raise ValueError(
                f'La abreviatura “{symbol}” ya la usa “{dup_symbol["name"]}”.'
            )
        cur = conn.execute(
            'INSERT INTO units(name, symbol, active, created_at) VALUES(?,?,1,?)',
            (name, symbol, now_text()),
        )
        unit_id = cur.lastrowid
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'unidad_creada', f'{name} ({symbol})'),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return next(u for u in list_units(True) if u['id'] == unit_id)


def set_unit_active(user_id, unit_id, active):
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM units WHERE id=?', (unit_id,)).fetchone()
        if not row:
            raise ValueError('La unidad no existe.')
        if active:
            conn.execute('UPDATE units SET active=1 WHERE id=?', (unit_id,))
            action = 'unidad_reactivada'
        else:
            if row['name'].lower() == DEFAULT_UNIT.lower():
                raise ValueError(
                    f'“{DEFAULT_UNIT}” es la unidad predeterminada y no se puede eliminar.'
                )
            used = conn.execute(
                'SELECT COUNT(*) FROM products WHERE unit=? COLLATE NOCASE', (row['name'],)
            ).fetchone()[0]
            if used:
                raise ValueError(
                    f'No se puede eliminar “{row["name"]}”: la usan {used} producto(s). '
                    'Cámbiales la unidad primero.'
                )
            active_units = conn.execute(
                'SELECT COUNT(*) FROM units WHERE active=1 AND id<>?', (unit_id,)
            ).fetchone()[0]
            if not active_units:
                raise ValueError('Debe quedar al menos una unidad activa.')
            conn.execute('UPDATE units SET active=0 WHERE id=?', (unit_id,))
            action = 'unidad_eliminada'
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, action, f'{row["name"]} ({row["symbol"]})'),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return next(u for u in list_units(True) if u['id'] == unit_id)


# ---------------------------------------------------------------- categorías y ubicaciones

def list_categories(include_inactive=False):
    conn = connect()
    try:
        sql = """SELECT c.*,
                        (SELECT COUNT(*) FROM products p
                          WHERE p.category = c.name COLLATE NOCASE) AS product_count
                 FROM categories c"""
        if not include_inactive:
            sql += ' WHERE c.active = 1'
        sql += " ORDER BY CASE WHEN LOWER(c.name)=LOWER(?) THEN 0 ELSE 1 END, c.name COLLATE NOCASE"
        return [row_to_dict(r) for r in conn.execute(sql, (DEFAULT_CATEGORY,))]
    finally:
        conn.close()


def create_category(user_id, name):
    name = str(name or '').strip()
    if not (2 <= len(name) <= 60):
        raise ValueError('El nombre de la categoría debe tener entre 2 y 60 caracteres.')
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        dup = conn.execute(
            'SELECT name FROM categories WHERE name=? COLLATE NOCASE', (name,)
        ).fetchone()
        if dup:
            raise ValueError(f'Ya existe la categoría “{dup["name"]}”.')
        cur = conn.execute(
            'INSERT INTO categories(name, active, created_at) VALUES(?,1,?)',
            (name, now_text()),
        )
        cat_id = cur.lastrowid
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'categoria_creada', name),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return next(c for c in list_categories(True) if c['id'] == cat_id)


def set_category_active(user_id, category_id, active):
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM categories WHERE id=?', (category_id,)).fetchone()
        if not row:
            raise ValueError('La categoría no existe.')
        if active:
            conn.execute('UPDATE categories SET active=1 WHERE id=?', (category_id,))
            action = 'categoria_reactivada'
        else:
            if row['name'].lower() == DEFAULT_CATEGORY.lower():
                raise ValueError(
                    f'“{DEFAULT_CATEGORY}” es la categoría predeterminada y no se puede eliminar.'
                )
            used = conn.execute(
                'SELECT COUNT(*) FROM products WHERE category=? COLLATE NOCASE', (row['name'],)
            ).fetchone()[0]
            if used:
                raise ValueError(
                    f'No se puede eliminar “{row["name"]}”: la usan {used} producto(s). '
                    'Cámbiales la categoría primero.'
                )
            active_cats = conn.execute(
                'SELECT COUNT(*) FROM categories WHERE active=1 AND id<>?', (category_id,)
            ).fetchone()[0]
            if not active_cats:
                raise ValueError('Debe quedar al menos una categoría activa.')
            conn.execute('UPDATE categories SET active=0 WHERE id=?', (category_id,))
            action = 'categoria_eliminada'
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, action, row['name']),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return next(c for c in list_categories(True) if c['id'] == category_id)


def list_locations(include_inactive=False):
    conn = connect()
    try:
        sql = """SELECT l.*,
                        (SELECT COUNT(*) FROM products p
                          WHERE p.location = l.name COLLATE NOCASE) AS product_count
                 FROM locations l"""
        if not include_inactive:
            sql += ' WHERE l.active = 1'
        sql += ' ORDER BY l.id'
        return [row_to_dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def create_location(user_id, name):
    name = str(name or '').strip()
    if not (2 <= len(name) <= 30):
        raise ValueError('La ubicación debe tener entre 2 y 30 caracteres.')
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        dup = conn.execute(
            'SELECT name FROM locations WHERE name=? COLLATE NOCASE', (name,)
        ).fetchone()
        if dup:
            raise ValueError(f'Ya existe la ubicación “{dup["name"]}”.')
        cur = conn.execute(
            'INSERT INTO locations(name, active, created_at) VALUES(?,1,?)',
            (name, now_text()),
        )
        loc_id = cur.lastrowid
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'ubicacion_creada', name),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return next(l for l in list_locations(True) if l['id'] == loc_id)


def set_location_active(user_id, location_id, active):
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM locations WHERE id=?', (location_id,)).fetchone()
        if not row:
            raise ValueError('La ubicación no existe.')
        if active:
            conn.execute('UPDATE locations SET active=1 WHERE id=?', (location_id,))
            action = 'ubicacion_reactivada'
        else:
            used = conn.execute(
                'SELECT COUNT(*) FROM products WHERE location=? COLLATE NOCASE', (row['name'],)
            ).fetchone()[0]
            if used:
                raise ValueError(
                    f'No se puede eliminar “{row["name"]}”: la usan {used} producto(s). '
                    'Cámbiales la ubicación primero.'
                )
            active_locs = conn.execute(
                'SELECT COUNT(*) FROM locations WHERE active=1 AND id<>?', (location_id,)
            ).fetchone()[0]
            if not active_locs:
                raise ValueError('Debe quedar al menos una ubicación activa.')
            conn.execute('UPDATE locations SET active=0 WHERE id=?', (location_id,))
            action = 'ubicacion_eliminada'
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, action, row['name']),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return next(l for l in list_locations(True) if l['id'] == location_id)


def list_reasons(include_inactive=False):
    conn = connect()
    try:
        sql = """SELECT r.*,
                        (SELECT COUNT(*) FROM stock_movements sm
                          WHERE sm.reason = r.name COLLATE NOCASE) AS movement_count
                 FROM reasons r"""
        if not include_inactive:
            sql += ' WHERE r.active = 1'
        sql += " ORDER BY CASE WHEN LOWER(r.name)=LOWER(?) THEN 0 ELSE 1 END, r.id"
        return [row_to_dict(r) for r in conn.execute(sql, (DEFAULT_REASON,))]
    finally:
        conn.close()


def create_reason(user_id, name):
    name = str(name or '').strip()
    if not (2 <= len(name) <= 60):
        raise ValueError('El motivo debe tener entre 2 y 60 caracteres.')
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        dup = conn.execute(
            'SELECT name FROM reasons WHERE name=? COLLATE NOCASE', (name,)
        ).fetchone()
        if dup:
            raise ValueError(f'Ya existe el motivo “{dup["name"]}”.')
        cur = conn.execute(
            'INSERT INTO reasons(name, active, created_at) VALUES(?,1,?)',
            (name, now_text()),
        )
        reason_id = cur.lastrowid
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'motivo_creado', name),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return next(r for r in list_reasons(True) if r['id'] == reason_id)


def set_reason_active(user_id, reason_id, active):
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM reasons WHERE id=?', (reason_id,)).fetchone()
        if not row:
            raise ValueError('El motivo no existe.')
        if active:
            conn.execute('UPDATE reasons SET active=1 WHERE id=?', (reason_id,))
            action = 'motivo_reactivado'
        else:
            if row['name'].lower() == DEFAULT_REASON.lower():
                raise ValueError(
                    f'“{DEFAULT_REASON}” es el motivo predeterminado y no se puede eliminar.'
                )
            active_reasons = conn.execute(
                'SELECT COUNT(*) FROM reasons WHERE active=1 AND id<>?', (reason_id,)
            ).fetchone()[0]
            if not active_reasons:
                raise ValueError('Debe quedar al menos un motivo activo.')
            conn.execute('UPDATE reasons SET active=0 WHERE id=?', (reason_id,))
            action = 'motivo_eliminado'
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, action, row['name']),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return next(r for r in list_reasons(True) if r['id'] == reason_id)


def create_product(user_id, data):
    """Alta manual de producto (queda en el inventario propio)."""
    name = (data.get('name') or '').strip()
    if not name:
        raise ValueError('Escribe el nombre del producto.')
    sku = (data.get('sku') or '').strip().upper() or next_manual_sku()
    if product_by_sku(sku):
        raise ValueError(f'El SKU {sku} ya existe.')
    barcode = (data.get('barcode') or '').strip() or None
    if barcode:
        conn = connect()
        try:
            dup = conn.execute('SELECT name FROM products WHERE barcode=? COLLATE NOCASE',
                               (barcode,)).fetchone()
        finally:
            conn.close()
        if dup:
            raise ValueError(f'El código {barcode} ya lo usa “{dup["name"]}”.')
    price = money(data.get('sale_price') or 0)
    if price <= 0:
        raise ValueError('El precio de venta debe ser mayor a cero.')
    stock = float(data.get('stock') or 0)
    alt_codes = (data.get('alt_codes') or '').strip() or None
    uconn = connect()
    try:
        unit, unit_symbol = _resolve_unit(uconn, data.get('unit'))
    finally:
        uconn.close()
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        base_id, base_qty = _validate_presentation(
            conn, data.get('base_product_id'), data.get('base_qty'))
        own_stock = 0.0 if base_id else stock
        cur = conn.execute(
            """INSERT INTO products(legacy_item_id, sku, barcode, item_number, alt_codes, name,
                                   category, location, unit, unit_symbol,
                                   cost_price, sale_price, stock, base_product_id, base_qty,
                                   active, source, created_at, updated_at, updated_by)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'manual',?,?,?)""",
            (None, sku, barcode, None, alt_codes, name,
             (data.get('category') or '').strip() or None,
             (data.get('location') or '').strip() or None,
             unit, unit_symbol,
             money(data.get('cost_price') or 0), price, own_stock,
             base_id, base_qty,
             now_text(), now_text(), user_id),
        )
        product_id = cur.lastrowid
        if stock:
            if base_id:
                stock_qty = round(stock * base_qty, 3)
                _apply_stock(conn, base_id, user_id, 'alta', stock_qty,
                             f'{(data.get("reason") or "Alta de producto")}'
                             f' ({stock:g} empaque de {name})')
            else:
                conn.execute(
                    """INSERT INTO stock_movements(product_id, user_id, created_at, kind, qty,
                                                  stock_before, stock_after, reason)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (product_id, user_id, now_text(), 'alta', stock, 0.0, stock,
                     (data.get('reason') or 'Alta de producto')),
                )
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'alta_producto',
             f'{sku} {name} unidad={unit} stock={stock} precio={price}'
             + (f' empaque_de={base_id} factor={base_qty:g}' if base_id else '')),
        )
        bump_catalog_version(conn)
        conn.commit()
        return get_product(product_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_product(user_id, product_id, changes, reason=''):
    """Edita datos del producto (precio, costo, nombre, ubicación…)."""
    allowed = {'name', 'category', 'location', 'cost_price', 'sale_price', 'barcode',
               'alt_codes', 'item_number', 'unit'}
    unit_pair = None
    if changes and changes.get('unit') is not None:
        uconn = connect()
        try:
            unit_pair = _resolve_unit(uconn, changes['unit'])
        finally:
            uconn.close()
    sets, values = [], []
    wants_base = ('base_product_id' in (changes or {}) or 'base_qty' in (changes or {}))
    for key, value in (changes or {}).items():
        if key not in allowed:
            continue
        if key == 'unit':
            if not unit_pair:
                continue
            sets.extend(['unit=?', 'unit_symbol=?'])
            values.extend(unit_pair)
            continue
        if key in ('cost_price', 'sale_price'):
            value = money(value)
            if key == 'sale_price' and value <= 0:
                raise ValueError('El precio de venta debe ser mayor a cero.')
        elif key in ('barcode', 'alt_codes', 'item_number'):
            value = (str(value).strip() or None) if value else None
        elif key in ('name', 'category', 'location'):
            value = (str(value).strip() or None) if value else None
            if key == 'name' and not value:
                raise ValueError('El nombre no puede quedar vacío.')
        sets.append(f'{key}=?')
        values.append(value)
    if not sets and not wants_base:
        raise ValueError('No hay cambios que guardar.')
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
        if not row:
            raise ValueError('El producto no existe.')
        if 'barcode' in changes and changes['barcode']:
            dup = conn.execute('SELECT name FROM products WHERE barcode=? COLLATE NOCASE AND id<>?',
                               (str(changes['barcode']).strip(), product_id)).fetchone()
            if dup:
                raise ValueError(f'El código ya lo usa “{dup["name"]}".')
        if wants_base:
            base_id, base_qty = _validate_presentation(
                conn, changes.get('base_product_id'), changes.get('base_qty'),
                exclude_id=product_id)
            sets.extend(['base_product_id=?', 'base_qty=?'])
            values.extend([base_id, base_qty])
        sets.append('updated_at=?')
        values.append(now_text())
        sets.append('updated_by=?')
        values.append(user_id)
        values.append(product_id)
        conn.execute(f"UPDATE products SET {', '.join(sets)} WHERE id=?", values)
        changed = ', '.join(f'{k}={changes[k]}' for k in changes if k in allowed)
        if wants_base:
            changed += f' base_product_id={base_id} base_qty={base_qty:g}'
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'edita_producto',
             f'producto={product_id} ({row["name"]}) cambios: {changed} motivo={reason}'),
        )
        bump_catalog_version(conn)
        conn.commit()
        return get_product(product_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _stock_target(conn, product_id):
    """Producto cuyo stock se mueve y el factor de conversión.

    Un empaque (presentación) vinculado a un producto base descuenta
    existencias del base: 1 empaque = base_qty unidades del base.
    Devuelve (id_producto_base, factor, nombre_base).
    """
    row = conn.execute(
        'SELECT id, base_product_id, base_qty FROM products WHERE id=?', (product_id,)
    ).fetchone()
    if not row:
        return product_id, 1.0, None
    if row['base_product_id'] and float(row['base_qty'] or 0) > 0:
        base = conn.execute(
            'SELECT id, name FROM products WHERE id=?', (row['base_product_id'],)
        ).fetchone()
        if base:
            return base['id'], float(row['base_qty']), base['name']
    return row['id'], 1.0, None


def _validate_presentation(conn, base_product_id, base_qty, exclude_id=None):
    """Valida el vínculo empaque -> producto base. Devuelve (id_base, factor)."""
    if base_product_id in (None, '', 0, '0'):
        return None, 1.0
    try:
        base_id = int(base_product_id)
    except (TypeError, ValueError):
        raise ValueError('El producto base no es válido.')
    if exclude_id is not None and base_id == int(exclude_id):
        raise ValueError('Un producto no puede ser empaque de sí mismo.')
    base = conn.execute(
        'SELECT id, name, active, base_product_id FROM products WHERE id=?', (base_id,)
    ).fetchone()
    if not base:
        raise ValueError('El producto base no existe.')
    if base['base_product_id']:
        raise ValueError('No se pueden anidar empaques: elige un producto simple.')
    if not base['active']:
        raise ValueError(f'El producto base “{base["name"]}" está dado de baja.')
    try:
        factor = float(base_qty if base_qty not in (None, '') else 1)
    except (TypeError, ValueError):
        raise ValueError('Las unidades por empaque no son válidas.')
    if factor <= 0:
        raise ValueError('Las unidades por empaque deben ser mayores a cero.')
    return base_id, round(factor, 3)


def _resolve_stock_notification(conn, product_id):
    """Cierra el aviso cuando el producto deja de estar en negativo."""
    stamp = now_text()
    conn.execute(
        """UPDATE notifications SET resolved_at=?, updated_at=?
            WHERE product_id=? AND resolved_at IS NULL""",
        (stamp, stamp, product_id),
    )


def notify_negative_stock(product_id, source='consulta'):
    """
    Crea o actualiza el aviso de stock negativo de un producto.

    El aviso permanece abierto hasta que las existencias vuelvan a cero o más.
    """
    conn = connect()
    try:
        row = conn.execute(
            'SELECT id, name, sku, stock FROM products WHERE id=?', (product_id,)
        ).fetchone()
        if not row:
            return None
        stamp = now_text()
        if float(row['stock'] or 0) >= 0:
            _resolve_stock_notification(conn, product_id)
            conn.commit()
            return None
        message = f'{row["name"]} ({row["sku"]}): stock {float(row["stock"]):g}'
        current = conn.execute(
            'SELECT id FROM notifications WHERE product_id=?', (product_id,)
        ).fetchone()
        if current:
            conn.execute(
                """UPDATE notifications
                      SET stock=?, message=?, source=?, updated_at=?, resolved_at=NULL
                    WHERE product_id=?""",
                (float(row['stock']), message, source, stamp, product_id),
            )
        else:
            conn.execute(
                """INSERT INTO notifications(product_id, kind, stock, message, source,
                                              created_at, updated_at)
                   VALUES(?, 'stock_negativo', ?, ?, ?, ?, ?)""",
                (product_id, float(row['stock']), message, source, stamp, stamp),
            )
        conn.commit()
        return {'product_id': product_id, 'stock': float(row['stock']), 'message': message}
    finally:
        conn.close()


def open_notifications(include_resolved=False):
    conn = connect()
    try:
        where = '' if include_resolved else 'WHERE n.resolved_at IS NULL'
        rows = conn.execute(
            f"""SELECT n.*, p.name, p.sku, p.stock, p.active
                  FROM notifications n JOIN products p ON p.id = n.product_id
                  {where}
                 ORDER BY n.updated_at DESC, n.id DESC"""
        ).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


def notification_count():
    conn = connect()
    try:
        return conn.execute(
            'SELECT COUNT(*) FROM notifications WHERE resolved_at IS NULL'
        ).fetchone()[0]
    finally:
        conn.close()


def _apply_stock(conn, product_id, user_id, kind, qty, reason='', ref=None,
                  sale_id=None, return_id=None):
    row = conn.execute('SELECT stock FROM products WHERE id=?', (product_id,)).fetchone()
    if not row:
        return None
    before = round(float(row['stock']), 3)
    after = round(before + float(qty), 3)
    conn.execute('UPDATE products SET stock=?, updated_at=?, updated_by=? WHERE id=?',
                 (after, now_text(), user_id, product_id))
    conn.execute(
        """INSERT INTO stock_movements(product_id, user_id, created_at, kind, qty,
                                      stock_before, stock_after, reason, ref, sale_id, return_id)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (product_id, user_id, now_text(), kind, float(qty), before, after,
         reason or None, ref or None, sale_id, return_id),
    )
    if after >= 0:
        _resolve_stock_notification(conn, product_id)
    bump_catalog_version(conn)
    return after


def set_stock(user_id, product_id, qty, reason, kind='ajuste'):
    """Alta o baja de existencias, con usuario, fecha y hora del movimiento."""
    qty = float(qty)
    if qty == 0:
        raise ValueError('La cantidad debe ser distinta de cero.')
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('Escribe el motivo del movimiento.')
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
        if not row:
            raise ValueError('El producto no existe.')
        # Un empaque mueve las existencias del producto base.
        target_id, factor, base_name = _stock_target(conn, product_id)
        stock_qty = round(qty * factor, 3)
        if factor != 1.0:
            reason = f'{reason} ({qty:g} empaque = {stock_qty:g} de {base_name})'
        after = _apply_stock(conn, target_id, user_id, kind, stock_qty, reason)
        if after is None:
            raise ValueError('El producto no existe.')
        if after < 0:
            raise ValueError('El ajuste dejaría el stock en negativo.')
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'ajuste_stock',
             f'{row["name"]} ({row["sku"]}) {qty:+g} -> {after:g} motivo={reason}'),
        )
        bump_catalog_version(conn)
        conn.commit()
        return get_product(product_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_product_active(user_id, product_id, active, reason=''):
    """Baja (desactivar) o alta (reactivar) de un producto del catálogo."""
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
        if not row:
            raise ValueError('El producto no existe.')
        if not row['active'] and active and row['base_product_id']:
            base = conn.execute('SELECT name, active FROM products WHERE id=?',
                                (row['base_product_id'],)).fetchone()
            if base and not base['active']:
                raise ValueError(f'No se puede reactivar: su producto base '
                                 f'“{base["name"]}" está dado de baja.')
        conn.execute('UPDATE products SET active=?, updated_at=?, updated_by=? WHERE id=?',
                     (1 if active else 0, now_text(), user_id, product_id))
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'producto_activo' if active else 'producto_baja',
             f'{row["name"]} ({row["sku"]}) motivo={reason}'),
        )
        bump_catalog_version(conn)
        conn.commit()
        return get_product(product_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def product_movements(product_id=None, limit=100):
    conn = connect()
    try:
        if product_id:
            rows = conn.execute(
                """SELECT m.*, p.name AS product_name, p.sku, u.name AS user_name
                   FROM stock_movements m
                   LEFT JOIN products p ON p.id = m.product_id
                   LEFT JOIN users u ON u.id = m.user_id
                   WHERE m.product_id = ? ORDER BY m.id DESC LIMIT ?""",
                (product_id, limit)).fetchall()
        else:
            rows = conn.execute(
                """SELECT m.*, p.name AS product_name, p.sku, u.name AS user_name
                   FROM stock_movements m
                   LEFT JOIN products p ON p.id = m.product_id
                   LEFT JOIN users u ON u.id = m.user_id
                   ORDER BY m.id DESC LIMIT ?""",
                (limit,)).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


def import_products(rows, user_id=None, replace=False, reset_stock=False):
    """
    Importa el catálogo limpio (Historico/Auxiliares/inventario_moderno.db)
    al inventario propio.
    rows: lista de dicts con los campos del catálogo.
    """
    created = updated = skipped = 0
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        stamp = now_text()
        for row in rows:
            sku = (row.get('sku') or '').strip()
            if not sku:
                skipped += 1
                continue
            existing = conn.execute('SELECT id, stock FROM products WHERE sku=? COLLATE NOCASE',
                                    (sku,)).fetchone()
            alt = row.get('alt_codes') or ''
            if isinstance(alt, (list, tuple)):
                alt = '|'.join(str(a) for a in alt if a)
            values = (
                row.get('legacy_item_id'), (row.get('barcode') or '').strip() or None,
                (row.get('item_number') or '').strip() or None, alt or None,
                (row.get('name') or '').strip(), (row.get('category') or '').strip() or None,
                (row.get('location') or '').strip() or None,
                money(row.get('cost_price') or 0), money(row.get('sale_price') or 0),
                float(row.get('units_sold_period') or 0),
                row.get('last_sold_at') or None,
            )
            stock_value = float(row.get('stock') or 0)
            if existing:
                conn.execute(
                    """UPDATE products SET legacy_item_id=COALESCE(?,legacy_item_id), barcode=?, item_number=?,
                                          alt_codes=?, name=?, category=?, location=?, cost_price=?, sale_price=?,
                                          units_sold_period=?, last_sold_at=?, active=1, updated_at=?, updated_by=?
                       WHERE id=?""",
                    values + (stamp, user_id, existing['id']))
                if reset_stock:
                    _apply_stock(conn, existing['id'], user_id, 'importacion',
                                 stock_value - float(existing['stock']),
                                 'Inventario inicial (importación)')
                updated += 1
            else:
                cur = conn.execute(
                    """INSERT INTO products(legacy_item_id, sku, barcode, item_number, alt_codes, name,
                                           category, location, cost_price, sale_price, stock,
                                           units_sold_period, last_sold_at, active, source,
                                           created_at, updated_at, updated_by)
                       VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,1,'1box',?,?,?)""",
                    (row.get('legacy_item_id'), sku,
                     (row.get('barcode') or '').strip() or None,
                     (row.get('item_number') or '').strip() or None, alt or None,
                     (row.get('name') or '').strip(),
                     (row.get('category') or '').strip() or None,
                     (row.get('location') or '').strip() or None,
                     money(row.get('cost_price') or 0), money(row.get('sale_price') or 0),
                     float(row.get('units_sold_period') or 0), row.get('last_sold_at') or None,
                     stamp, stamp, user_id))
                product_id = cur.lastrowid
                _apply_stock(conn, product_id, user_id, 'importacion',
                             stock_value, 'Inventario inicial (importación)')
                created += 1
        if replace:
            conn.execute('UPDATE products SET active=0, updated_at=?, updated_by=? WHERE source=? AND active=1',
                         (stamp, user_id, '1box'))
        bump_catalog_version(conn)
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (stamp, user_id, 'importacion_inventario',
             f'nuevos={created} actualizados={updated} omitidos={skipped} reinicio_stock={reset_stock}'),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {'created': created, 'updated': updated, 'skipped': skipped,
            'total': len(rows), 'version': catalog_version()}


def products_count():
    conn = connect()
    try:
        row = conn.execute('SELECT COUNT(*) n, SUM(active) a FROM products').fetchone()
        return {'total': row['n'], 'active': row['a'] or 0}
    finally:
        conn.close()


def today_summary():
    day = today_text()
    conn = connect()
    try:
        totals = conn.execute(
            """SELECT COUNT(*) AS count, COALESCE(SUM(total),0) AS total,
                      COALESCE(SUM(change_given),0) AS change_given
               FROM sales WHERE substr(created_at,1,10)=? AND status='completed'""",
            (day,),
        ).fetchone()
        by_method = {r['method']: money(r['amount']) for r in conn.execute(
            """SELECT p.method, COALESCE(SUM(p.amount),0) AS amount
               FROM payments p JOIN sales s ON s.id=p.sale_id
               WHERE substr(s.created_at,1,10)=? AND s.status='completed'
               GROUP BY p.method""",
            (day,),
        ).fetchall()}
        by_method['efectivo'] = money(by_method.get('efectivo', 0) - totals['change_given'])
        sales = []
        for row in conn.execute(
            """SELECT s.id, s.folio, s.created_at, s.total, s.status, u.name AS user_name,
                      (SELECT GROUP_CONCAT(DISTINCT method) FROM payments WHERE sale_id=s.id) AS methods,
                      (SELECT COALESCE(SUM(rt.total),0) FROM returns rt
                        WHERE rt.sale_id=s.id AND rt.status='completed') AS returns_total,
                      u.role AS user_role
               FROM sales s JOIN users u ON u.id=s.user_id
               WHERE substr(s.created_at,1,10)=?
               ORDER BY s.id DESC LIMIT 100""",
            (day,),
        ).fetchall():
            item = row_to_dict(row)
            item['total'] = money(item['total'])
            item['returns_total'] = money(item['returns_total'])
            item['ticket_token'] = ticket_token(item['id'])
            sales.append(item)
        returns = [row_to_dict(r) for r in conn.execute(
            """SELECT r.id, r.folio, r.created_at, r.total, r.status, r.method, r.reason,
                      u.name AS user_name, s.folio AS sale_folio, s.status AS sale_status
               FROM returns r JOIN users u ON u.id=r.user_id JOIN sales s ON s.id=r.sale_id
               WHERE substr(r.created_at,1,10)=? ORDER BY r.id DESC""",
            (day,),
        ).fetchall()]
        for item in returns:
            item['total'] = money(item['total'])
        # Las devoluciones de una venta cancelada ya quedaron incluidas en la
        # cancelación: no se cuentan otra vez en el neto del día.
        active_returns = [r for r in returns
                          if r['status'] == 'completed' and r['sale_status'] != 'cancelled']
        ret_count = len(active_returns)
        ret_total = money(sum(r['total'] for r in active_returns))
        pending_rows = conn.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(total),0) AS t
               FROM sales WHERE status='pending'"""
        ).fetchone()
        return {
            'date': day,
            'count': totals['count'],
            'total': money(totals['total']),
            'change_given': money(totals['change_given']),
            'by_method': by_method,
            'returns_count': ret_count,
            'returns_total': ret_total,
            'net_total': money(money(totals['total']) - ret_total),
            'pending_count': pending_rows['n'],
            'pending_total': money(pending_rows['t']),
            'sales': sales,
            'returns': returns,
        }
    finally:
        conn.close()


