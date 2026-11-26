"""Respaldos, devoluciones, precios de emergencia y exportaciones."""

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
    BASE_DIR, POS_DB, BACKUP_DIR, EXPORT_DIR,
    connect, row_to_dict, money, now_text, today_text, load_config,
)
from core.users import audit
from core.sales import get_sale
from core.inventory import (
    _stock_target, _apply_stock, bump_catalog_version,
)

# ---------------------------------------------------------------- respaldos

def verify_backup(path):
    """Comprueba que un respaldo se pueda abrir y que su contenido sea coherente."""
    result = {'ok': False, 'path': path, 'error': None, 'sales': 0, 'products': 0,
              'users': 0, 'size': 0}
    if not os.path.exists(path):
        result['error'] = 'El respaldo no existe.'
        return result
    result['size'] = os.path.getsize(path)
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity != 'ok':
                result['error'] = f'Integridad: {integrity}'
                return result
            result['sales'] = conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0]
            result['products'] = conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]
            result['users'] = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        finally:
            conn.close()
        result['ok'] = True
    except sqlite3.Error as exc:
        result['error'] = str(exc)
    return result


def list_backups():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    out = []
    for name in os.listdir(BACKUP_DIR):
        if name.startswith('pos_') and name.endswith('.db'):
            path = os.path.join(BACKUP_DIR, name)
            info = verify_backup(path)
            info['name'] = name
            info['modified'] = datetime.datetime.fromtimestamp(
                os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M:%S')
            out.append(info)
    out.sort(key=lambda item: item['modified'], reverse=True)
    return out


def backup(tag='manual', keep=None):
    """Respaldo consistente de pos.db; los manuales nunca se borran solos."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    config = load_config()
    if keep is None:
        keep = int(config.get('backup', {}).get('keep_auto', 168) or 168)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(BACKUP_DIR, f'pos_{stamp}_{tag}.db')
    src = connect()
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    check = verify_backup(dest)
    if not check['ok']:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise ValueError(f'El respaldo no pasó la verificación: {check["error"]}')
    if tag in ('auto', 'arranque', 'turno'):
        _prune_backups(keep, 'auto', 'arranque', 'turno')
    try:
        with open(os.path.join(BACKUP_DIR, 'ultimo_respaldo.json'), 'w', encoding='utf-8') as fh:
            json.dump({
                'tag': tag,
                'path': dest,
                'created_at': now_text(),
                'integrity': 'ok',
                'sales': check['sales'],
                'products': check['products'],
                'users': check['users'],
                'size': check['size'],
            }, fh, ensure_ascii=False, indent=2)
            fh.write('\n')
    except OSError:
        pass
    return dest


def _prune_backups(keep, *tags):
    """Conserva los respaldos más recientes entre varias etiquetas automáticas.

    `auto`, `arranque` y `turno` comparten un mismo límite (`keep_auto`) para
    que no se acumulen varios grupos independientes.
    """
    files = []
    for tag in tags:
        suffix = f'_{tag}.db'
        files.extend(
            os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
            if f.startswith('pos_') and f.endswith(suffix)
        )
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for old in files[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass


def audit_log(limit=200):
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT a.*, u.name AS user_name FROM audit a
               LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- devoluciones

def next_return_folio(conn, day=None):
    day = day or today_text()
    seq = conn.execute(
        "SELECT COUNT(*) FROM returns WHERE substr(created_at,1,10)=?", (day,)
    ).fetchone()[0] + 1
    return f"R-{day.replace('-', '')}-{seq:04d}"


def sale_by_folio(folio):
    folio = (folio or '').strip()
    if not folio:
        return None
    conn = connect()
    try:
        row = conn.execute(
            'SELECT id FROM sales WHERE folio = ? COLLATE NOCASE', (folio,)
        ).fetchone()
        return get_sale(row['id']) if row else None
    finally:
        conn.close()


def sale_return_state(sale_id):
    """Cantidad ya devuelta por línea de venta."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT ri.sale_item_id, COALESCE(SUM(ri.qty),0) AS qty
               FROM return_items ri JOIN returns r ON r.id = ri.return_id
               WHERE r.sale_id = ? AND r.status = 'completed'
               GROUP BY ri.sale_item_id""",
            (sale_id,),
        ).fetchall()
        return {r['sale_item_id']: float(r['qty']) for r in rows}
    finally:
        conn.close()


def sale_with_remaining(sale_id):
    """Venta con la cantidad pendiente de devolver en cada línea."""
    sale = get_sale(sale_id)
    if not sale:
        return None
    returned = sale_return_state(sale_id)
    total_returned = 0.0
    for item in sale['items']:
        already = returned.get(item['id'], 0.0)
        item['returned_qty'] = already
        item['remaining_qty'] = round(float(item['qty']) - already, 3)
        total_returned += already * float(item['unit_price'])
    sale['returned_total'] = money(total_returned)
    sale['pending_total'] = money(float(sale['total']) - total_returned)
    return sale


def create_return(user_id, shift_id, sale_id, items, method, reason, note=''):
    """
    items: [{sale_item_id, qty}]
    Devuelve el registro de la devolución.
    """
    method = (method or '').strip().lower()
    if method not in ('efectivo', 'tarjeta', 'transferencia'):
        raise ValueError('Forma de devolución no válida.')
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('Escribe el motivo de la devolución.')

    sale = get_sale(sale_id)
    if not sale:
        raise ValueError('La venta no existe.')
    if sale['status'] == 'pending':
        raise ValueError('Ese ticket está pendiente de cobro: primero cóbralo en Caja '
                         'o cancélalo; después se puede devolver.')
    if sale['status'] != 'completed':
        raise ValueError('Esa venta está cancelada; no se puede devolver.')

    by_id = {i['id']: i for i in sale['items']}
    returned = sale_return_state(sale_id)
    lines = []
    for raw in items or []:
        try:
            item_id = int(raw.get('sale_item_id'))
            qty = round(float(raw.get('qty') or 0), 2)
        except (TypeError, ValueError):
            raise ValueError('Línea de devolución inválida.')
        if qty <= 0:
            continue
        source = by_id.get(item_id)
        if not source:
            raise ValueError('Alguna línea no pertenece a esa venta.')
        remaining = float(source['qty']) - returned.get(item_id, 0.0)
        if qty > remaining + 0.0001:
            raise ValueError(f'{source["name"]}: sólo quedan {remaining:g} por devolver.')
        lines.append({
            'sale_item_id': item_id,
            'legacy_item_id': source.get('legacy_item_id'),
            'product_key': source.get('product_key'),
            'stock_product_id': source.get('stock_product_id'),
            'stock_factor': float(source.get('stock_factor') or 1),
            'sku': source.get('sku'),
            'name': source['name'],
            'unit': source.get('unit') or None,
            'unit_price': float(source['unit_price']),
            'qty': qty,
        })
    if not lines:
        raise ValueError('Elige al menos una línea para devolver.')

    total = money(sum(l['qty'] * l['unit_price'] for l in lines))
    stamp = now_text()

    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        if method == 'efectivo':
            # El efectivo debe quedar en un turno abierto. Si la venta es de un
            # turno ya cerrado, la devolución se carga al turno actual.
            open_row = conn.execute(
                "SELECT id FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not open_row:
                raise ValueError(
                    'Abre un turno para registrar la devolución en efectivo.'
                )
            shift_id = open_row['id']
        folio = next_return_folio(conn)
        cur = conn.execute(
            """INSERT INTO returns(folio, sale_id, shift_id, user_id, created_at, subtotal,
                                   total, method, status, reason, note)
               VALUES(?,?,?,?,?,?,?,?, 'completed', ?, ?)""",
            (folio, sale_id, shift_id, user_id, stamp, total, total, method, reason,
             (note or '').strip() or None),
        )
        return_id = cur.lastrowid
        for line in lines:
            conn.execute(
                """INSERT INTO return_items(return_id, sale_item_id, legacy_item_id, product_key,
                                           sku, name, unit, unit_price, qty, line_total,
                                           stock_product_id, stock_factor)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (return_id, line['sale_item_id'], line['legacy_item_id'], line['product_key'],
                 line['sku'], line['name'], line.get('unit'), money(line['unit_price']), line['qty'],
                 money(line['qty'] * line['unit_price']), line.get('stock_product_id'),
                 float(line.get('stock_factor') or 1)),
            )
            # La devolución repone las existencias (del base, si es un empaque).
            factor = float(line.get('stock_factor') or 1)
            target = None
            if line.get('stock_product_id'):
                target = conn.execute('SELECT id FROM products WHERE id=?',
                                      (line['stock_product_id'],)).fetchone()
            if not target:
                prow = None
                if line.get('product_key') not in (None, ''):
                    prow = conn.execute('SELECT id FROM products WHERE id=?',
                                        (line['product_key'],)).fetchone()
                elif line.get('legacy_item_id') is not None:
                    prow = conn.execute('SELECT id FROM products WHERE legacy_item_id=?',
                                        (line['legacy_item_id'],)).fetchone()
                target = prow
            if target:
                stock_qty = round(line['qty'] * factor, 3)
                detail = f'Devolución {folio}'
                if factor != 1.0:
                    detail += f' ({line["qty"]:g} empaque = {stock_qty:g} pza)'
                _apply_stock(conn, target['id'], user_id, 'devolucion', stock_qty,
                             detail, ref=folio, sale_id=sale_id, return_id=return_id)
        if method == 'efectivo':
            conn.execute(
                """INSERT INTO cash_movements(shift_id, user_id, created_at, kind, amount, reason, sale_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (shift_id, user_id, stamp, 'devolucion', -total,
                 f'Devolución {folio}: {reason}', sale_id),
            )
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (stamp, user_id, 'devolucion', f'{folio} venta={sale["folio"]} total={total} motivo={reason}'),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_return(return_id)


def get_return(return_id):
    conn = connect()
    try:
        row = conn.execute(
            """SELECT r.*, u.name AS user_name, s.folio AS sale_folio, s.created_at AS sale_created_at
               FROM returns r JOIN users u ON u.id = r.user_id JOIN sales s ON s.id = r.sale_id
               WHERE r.id = ?""",
            (return_id,),
        ).fetchone()
        if not row:
            return None
        data = row_to_dict(row)
        data['items'] = [row_to_dict(x) for x in conn.execute(
            'SELECT * FROM return_items WHERE return_id=? ORDER BY id', (return_id,)
        ).fetchall()]
        data['method_label'] = {
            'efectivo': 'Efectivo', 'tarjeta': 'Tarjeta', 'transferencia': 'Transferencia',
        }.get(data['method'], data['method'])
        return data
    finally:
        conn.close()


def cancel_return(return_id, user_id, reason):
    """Anula una devolución (sólo gerente) y reintegra el efectivo."""
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('Escribe el motivo de la anulación.')
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM returns WHERE id=?', (return_id,)).fetchone()
        if not row:
            raise ValueError('La devolución no existe.')
        if row['status'] != 'completed':
            raise ValueError('Esa devolución ya está anulada.')
        parent = conn.execute('SELECT status, folio FROM sales WHERE id=?',
                              (row['sale_id'],)).fetchone()
        if parent and parent['status'] == 'cancelled':
            raise ValueError(
                'La venta está cancelada: esta devolución ya quedó incluida '
                'en la cancelación y no se puede anular por separado.'
            )
        stamp = now_text()
        conn.execute(
            """UPDATE returns SET status='cancelled', cancelled_at=?, cancelled_by=?, cancel_reason=?
               WHERE id=?""",
            (stamp, user_id, reason, return_id),
        )
        # Anular la devolución vuelve a descontar las existencias.
        for item in conn.execute(
                """SELECT legacy_item_id, product_key, qty, stock_product_id, stock_factor
                     FROM return_items WHERE return_id=?""",
                (return_id,)).fetchall():
            target = None
            if item['stock_product_id']:
                target = conn.execute('SELECT id FROM products WHERE id=?',
                                      (item['stock_product_id'],)).fetchone()
            if not target:
                if item['product_key']:
                    target = conn.execute('SELECT id FROM products WHERE id=?',
                                          (item['product_key'],)).fetchone()
                elif item['legacy_item_id'] is not None:
                    target = conn.execute('SELECT id FROM products WHERE legacy_item_id=?',
                                          (item['legacy_item_id'],)).fetchone()
            if target:
                factor = float(item['stock_factor'] or 1)
                stock_qty = round(float(item['qty']) * factor, 3)
                detail = f'Anulación devolución {row["folio"]}'
                if factor != 1.0:
                    detail += f' ({item["qty"]:g} empaque = {stock_qty:g} pza)'
                _apply_stock(conn, target['id'], user_id, 'anulacion', -stock_qty,
                             detail, ref=row['folio'])
        if row['method'] == 'efectivo':
            # El reintegro se registra en el turno original si sigue abierto;
            # si ya cerró, se carga al turno abierto actual.
            shift = conn.execute('SELECT status FROM shifts WHERE id=?',
                                 (row['shift_id'],)).fetchone()
            target_shift_id = row['shift_id']
            if not shift or shift['status'] != 'open':
                open_row = conn.execute(
                    "SELECT id FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if not open_row:
                    raise ValueError(
                        'Abre un turno para registrar la anulación en efectivo.'
                    )
                target_shift_id = open_row['id']
            conn.execute(
                """INSERT INTO cash_movements(shift_id, user_id, created_at, kind, amount, reason, sale_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (target_shift_id, user_id, stamp, 'anulacion_devolucion', money(row['total']),
                 f'Anulación devolución {row["folio"]}: {reason}', row['sale_id']),
            )
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (stamp, user_id, 'devolucion_anulada', f'{row["folio"]} motivo={reason}'),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_return(return_id)


def list_returns(day=None):
    day = day or today_text()
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT r.*, u.name AS user_name, s.folio AS sale_folio,
                      (SELECT COUNT(*) FROM return_items ri WHERE ri.return_id = r.id) AS lines
               FROM returns r JOIN users u ON u.id = r.user_id JOIN sales s ON s.id = r.sale_id
               WHERE substr(r.created_at,1,10) = ? ORDER BY r.id DESC""",
            (day,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


def returns_summary(day=None):
    day = day or today_text()
    conn = connect()
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS count, COALESCE(SUM(r.total),0) AS total
               FROM returns r JOIN sales s ON s.id = r.sale_id
               WHERE substr(r.created_at,1,10) = ? AND r.status='completed'
                 AND s.status<>'cancelled'""",
            (day,),
        ).fetchone()
        return {'count': row['count'], 'total': money(row['total'])}
    finally:
        conn.close()


# ---------------------------------------------------------------- precios de emergencia

def set_price_override(product_key, price, reason, user_id, original_price, hours=24):
    """Precio local temporal, para cuando 1box no está disponible."""
    price = money(price)
    if price <= 0:
        raise ValueError('El precio debe ser mayor a cero.')
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('Escribe el motivo del cambio de precio.')
    expires = (datetime.datetime.now() + datetime.timedelta(hours=int(hours or 24))).strftime(
        '%Y-%m-%d %H:%M:%S')
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute(
            'UPDATE price_overrides SET active=0 WHERE product_key=? AND active=1',
            (str(product_key),),
        )
        cur = conn.execute(
            """INSERT INTO price_overrides(product_key, original_price, price, reason, user_id,
                                           created_at, expires_at, active)
               VALUES(?,?,?,?,?,?,?,1)""",
            (str(product_key), money(original_price), price, reason, user_id, now_text(), expires),
        )
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'precio_emergencia',
             f'producto={product_key} de {money(original_price)} a {price} motivo={reason}'),
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revert_price_override(product_key, user_id):
    conn = connect()
    try:
        conn.execute('UPDATE price_overrides SET active=0 WHERE product_key=? AND active=1',
                     (str(product_key),))
        conn.commit()
    finally:
        conn.close()
    conn2 = connect()
    try:
        conn2.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, 'precio_emergencia_revertida', f'producto={product_key}'),
        )
        conn2.commit()
    finally:
        conn2.close()


def active_price_overrides():
    """Precios de emergencia vigentes (no expirados), por clave de producto."""
    now = now_text()
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT * FROM price_overrides WHERE active=1
               AND (expires_at IS NULL OR expires_at > ?)""",
            (now,),
        ).fetchall()
        return {r['product_key']: row_to_dict(r) for r in rows}
    finally:
        conn.close()


def prune_price_overrides(price_map):
    """Si el catálogo ya trae el precio nuevo (1box actualizado), el ajuste local sobra."""
    conn = connect()
    try:
        rows = conn.execute(
            'SELECT id, product_key, price FROM price_overrides WHERE active=1').fetchall()
        stale = [r['id'] for r in rows
                 if str(r['product_key']) in price_map
                 and money(price_map[str(r['product_key'])]) == money(r['price'])]
        if stale:
            conn.execute(
                f"UPDATE price_overrides SET active=0 WHERE id IN ({','.join('?' * len(stale))})",
                stale,
            )
            conn.commit()
        return len(stale)
    finally:
        conn.close()


def list_price_overrides():
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT o.*, u.name AS user_name FROM price_overrides o
               LEFT JOIN users u ON u.id = o.user_id
               WHERE o.active = 1 ORDER BY o.id DESC LIMIT 100"""
        ).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- exportaciones

def export_sales_csv(day=None):
    """Exporta las ventas (y devoluciones) de un día para conciliar con 1box."""
    day = day or today_text()
    conn = connect()
    try:
        sales = conn.execute(
            """SELECT s.folio, s.created_at, u.name AS user_name, s.total, s.status,
                      (SELECT GROUP_CONCAT(DISTINCT p.method) FROM payments p WHERE p.sale_id = s.id) AS methods,
                      (SELECT GROUP_CONCAT(si.name || ' x' || si.qty ||
                              CASE WHEN si.unit IS NOT NULL AND si.unit <> ''
                                   THEN ' ' || si.unit ELSE '' END)
                       FROM sale_items si WHERE si.sale_id = s.id) AS items,
                      s.cancel_reason
               FROM sales s JOIN users u ON u.id = s.user_id
               WHERE substr(s.created_at,1,10) = ? ORDER BY s.id""",
            (day,),
        ).fetchall()
        returns = conn.execute(
            """SELECT r.folio, r.created_at, u.name AS user_name, r.total, r.status, r.method,
                      (SELECT GROUP_CONCAT(ri.name || ' x' || ri.qty ||
                              CASE WHEN ri.unit IS NOT NULL AND ri.unit <> ''
                                   THEN ' ' || ri.unit ELSE '' END)
                       FROM return_items ri WHERE ri.return_id = r.id) AS items,
                      r.reason
               FROM returns r JOIN users u ON u.id = r.user_id
               WHERE substr(r.created_at,1,10) = ? ORDER BY r.id""",
            (day,),
        ).fetchall()
    finally:
        conn.close()

    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.join(EXPORT_DIR, f'ventas_{day.replace("-", "")}.csv')
    import csv
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(['Tipo', 'Folio', 'Fecha', 'Hora', 'Cajero', 'Pago', 'Total',
                         'Estado', 'Productos', 'Motivo'])
        for row in sales:
            writer.writerow([
                'Venta', row['folio'], (row['created_at'] or '')[:10], (row['created_at'] or '')[11:16],
                row['user_name'], row['methods'], f"{row['total']:.2f}", row['status'],
                row['items'], row['cancel_reason'] or '',
            ])
        for row in returns:
            writer.writerow([
                'Devolución', row['folio'], (row['created_at'] or '')[:10], (row['created_at'] or '')[11:16],
                row['user_name'], row['method'], f"{row['total']:.2f}", row['status'],
                row['items'], row['reason'] or '',
            ])
    return path, len(sales), len(returns)
