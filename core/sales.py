"""Ventas, cobros, pendientes, cancelaciones y clientes."""

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
    connect, row_to_dict, money, now_text, today_text,
    ticket_token, DEFAULT_UNIT_SYMBOL,
)
from core.users import audit
from core.inventory import (
    _stock_target, _apply_stock, bump_catalog_version, get_product,
)

# ---------------------------------------------------------------- ventas

def next_folio(conn, day=None):
    day = day or today_text()
    seq = conn.execute(
        "SELECT COUNT(*) FROM sales WHERE substr(created_at,1,10)=?", (day,)
    ).fetchone()[0] + 1
    return f"{day.replace('-', '')}-{seq:04d}"


def _customer_search_key(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    return ' '.join(value.casefold().split())


def search_customers(query='', include_deleted=False, limit=50):
    """Busca clientes activos importados y agrega estadísticas de ventas del POS."""
    q = _customer_search_key(query)
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 50
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT c.*,
                      COALESCE(a.sales_count, 0) AS sales_count,
                      COALESCE(a.total, 0) AS total_comprado,
                      a.last_sale
               FROM customers c
               LEFT JOIN (
                   SELECT sc.customer_id, COUNT(*) AS sales_count,
                          SUM(s.total) AS total, MAX(s.created_at) AS last_sale
                   FROM sale_customers sc
                   JOIN sales s ON s.id=sc.sale_id
                   WHERE s.status='completed'
                   GROUP BY sc.customer_id
               ) a ON a.customer_id=c.id
               WHERE (?=1 OR c.activo=1) AND c.es_generico=0 AND c.es_publico=0
               ORDER BY c.nombre COLLATE NOCASE, c.id""",
            (1 if include_deleted else 0,),
        ).fetchall()
        matches = []
        for row in rows:
            item = row_to_dict(row)
            if q:
                haystack = _customer_search_key(' '.join(str(item.get(k) or '') for k in (
                    'nombre', 'apellido', 'rfc', 'telefono', 'email', 'alt_emails',
                    'direccion', 'ciudad', 'estado', 'cp',
                )))
                if any(term not in haystack for term in q.split()):
                    continue
            item['saldo'] = money(item.get('saldo'))
            item['limite_credito'] = money(item.get('limite_credito'))
            item['total_comprado'] = money(item.get('total_comprado'))
            matches.append(item)
        return {'customers': matches[:limit], 'total': len(matches)}
    finally:
        conn.close()


def get_customer(customer_id, include_sales=True):
    """Obtiene ficha y, opcionalmente, las últimas ventas del cliente."""
    conn = connect()
    try:
        row = conn.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone()
        if not row:
            return None
        customer = row_to_dict(row)
        summary = conn.execute(
            """SELECT COUNT(*) AS sales_count, COALESCE(SUM(s.total), 0) AS total_comprado,
                      MAX(s.created_at) AS last_sale
               FROM sale_customers sc JOIN sales s ON s.id=sc.sale_id
               WHERE sc.customer_id=? AND s.status='completed'""",
            (customer_id,),
        ).fetchone()
        customer.update({
            'sales_count': summary['sales_count'],
            'total_comprado': money(summary['total_comprado']),
            'last_sale': summary['last_sale'],
        })
        customer['saldo'] = money(customer.get('saldo'))
        customer['limite_credito'] = money(customer.get('limite_credito'))
        if include_sales:
            customer['sales'] = [
                row_to_dict(sale) for sale in conn.execute(
                    """SELECT s.id, s.folio, s.created_at, s.total, s.status,
                              (SELECT GROUP_CONCAT(p.method, ' + ')
                               FROM payments p WHERE p.sale_id=s.id) AS payment_methods
                       FROM sale_customers sc
                       JOIN sales s ON s.id=sc.sale_id
                       WHERE sc.customer_id=? AND s.status='completed'
                       ORDER BY s.created_at DESC, s.id DESC LIMIT 20""",
                    (customer_id,),
                ).fetchall()
            ]
        return customer
    finally:
        conn.close()


def _sale_customer(conn, customer_id):
    if customer_id in (None, ''):
        row = conn.execute(
            """SELECT id, nombre, rfc FROM customers
               WHERE es_publico=1 AND activo=1 ORDER BY id LIMIT 1"""
        ).fetchone()
        return row_to_dict(row)
    try:
        customer_id = int(customer_id)
    except (TypeError, ValueError):
        raise ValueError('Cliente inválido.')
    row = conn.execute(
        'SELECT id, nombre, rfc FROM customers WHERE id=? AND activo=1 AND es_generico=0 AND es_publico=0',
        (customer_id,),
    ).fetchone()
    if not row:
        raise ValueError('El cliente no existe, está inactivo o no se puede asociar a ventas.')
    return row_to_dict(row)


def _link_sale_customer(conn, sale_id, customer, stamp):
    if not customer:
        return
    conn.execute(
        """INSERT INTO sale_customers(
               sale_id, customer_id, customer_name, customer_rfc, created_at
           ) VALUES(?,?,?,?,?)""",
        (sale_id, customer['id'], customer['nombre'], customer.get('rfc'), stamp),
    )


def create_sale(user_id, shift_id, items, payments, customer_name='', note='', customer_id=None):
    """
    items: [{legacy_item_id, sku, barcode, name, unit, unit_price, cost_price, qty}]
    payments: [{method, amount, reference}]
    Los importes ya vienen validados contra el catálogo por la capa HTTP.
    """
    for item in items:
        item['qty'] = round(float(item.get('qty') or 0), 2)
        if item['qty'] <= 0:
            raise ValueError('Las cantidades deben ser mayores a cero.')
    subtotal = money(sum(money(i['unit_price'] * i['qty']) for i in items))
    total = subtotal
    paid = money(sum(money(p['amount']) for p in payments))
    change = money(paid - total)
    item_count = sum(float(i['qty']) for i in items)
    stamp = now_text()

    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        customer = _sale_customer(conn, customer_id)
        if customer:
            customer_name = customer['nombre']
        folio = next_folio(conn)
        cur = conn.execute(
            """INSERT INTO sales(folio, shift_id, user_id, created_at, subtotal, discount, total,
                                 paid_total, change_given, item_count, status, paid_at, paid_by,
                                 customer_name, note)
               VALUES(?,?,?,?,?,0,?,?,?,?, 'completed', ?, ?, ?, ?)""",
            (folio, shift_id, user_id, stamp, subtotal, total, paid, change, item_count,
             stamp, user_id, customer_name or None, note or None),
        )
        sale_id = cur.lastrowid
        _link_sale_customer(conn, sale_id, customer, stamp)
        for item in items:
            qty = round(float(item['qty']), 2)
            line_total = money(item['unit_price'] * qty)
            # Si el POS ya es dueño del inventario, la venta descuenta existencias.
            # Un empaque descuenta del producto base: 1 empaque = factor unidades.
            prow = None
            if item.get('product_key') not in (None, ''):
                prow = conn.execute('SELECT id FROM products WHERE id=?',
                                    (item['product_key'],)).fetchone()
            elif item.get('legacy_item_id') is not None:
                prow = conn.execute('SELECT id FROM products WHERE legacy_item_id=?',
                                    (item['legacy_item_id'],)).fetchone()
            target_id, factor, base_name = None, 1.0, None
            if prow:
                target_id, factor, base_name = _stock_target(conn, prow['id'])
            stock_qty = round(qty * factor, 3)
            detail = f'Venta {folio}'
            if factor != 1.0:
                detail += f' ({qty:g} empaque = {stock_qty:g} de {base_name})'
            conn.execute(
                """INSERT INTO sale_items(sale_id, legacy_item_id, product_key, sku, barcode, name,
                                          unit, unit_price, cost_price, qty, line_total,
                                          stock_product_id, stock_factor)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sale_id, item.get('legacy_item_id'), str(item.get('product_key') or '') or None,
                 item.get('sku'), item.get('barcode'),
                 item['name'], item.get('unit') or DEFAULT_UNIT_SYMBOL,
                 money(item['unit_price']), money(item.get('cost_price', 0)),
                 qty, line_total, target_id, factor),
            )
            if target_id:
                _apply_stock(conn, target_id, user_id, 'venta', -stock_qty,
                             detail, ref=folio, sale_id=sale_id)
        for payment in payments:
            conn.execute(
                """INSERT INTO payments(sale_id, method, amount, reference, created_at)
                   VALUES(?,?,?,?,?)""",
                (sale_id, payment['method'], money(payment['amount']),
                 payment.get('reference') or None, stamp),
            )
        cash_net = money(sum(
            money(p['amount']) for p in payments if p['method'] == 'efectivo'
        ) - change)
        if cash_net:
            conn.execute(
                """INSERT INTO cash_movements(shift_id, user_id, created_at, kind, amount, reason, sale_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (shift_id, user_id, stamp, 'venta', cash_net, f'Venta {folio}', sale_id),
            )
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (stamp, user_id, 'venta', f'{folio} total={total} pago={paid}'),
        )
        conn.commit()
        return get_sale(sale_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_pending_sale(user_id, shift_id, items, customer_name='', note='', customer_id=None):
    """Registra una venta despachada por un vendedor como pendiente de cobro.

    - Descuenta (separa) el inventario de inmediato para evitar vender dos veces.
    - NO registra pagos ni mueve el efectivo esperado de Caja.
    - Queda con status='pending' hasta que Caja la cobre o la cancele.
    """
    for item in items:
        item['qty'] = round(float(item.get('qty') or 0), 2)
        if item['qty'] <= 0:
            raise ValueError('Las cantidades deben ser mayores a cero.')
    subtotal = money(sum(money(i['unit_price'] * i['qty']) for i in items))
    total = subtotal
    item_count = sum(float(i['qty']) for i in items)
    stamp = now_text()

    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        customer = _sale_customer(conn, customer_id)
        if customer:
            customer_name = customer['nombre']
        folio = next_folio(conn)
        cur = conn.execute(
            """INSERT INTO sales(folio, shift_id, user_id, created_at, subtotal, discount, total,
                                 paid_total, change_given, item_count, status, customer_name, note)
               VALUES(?,?,?,?,?,0,?, 0, 0, ?, 'pending', ?, ?)""",
            (folio, shift_id, user_id, stamp, subtotal, total, item_count,
             customer_name or None, note or None),
        )
        sale_id = cur.lastrowid
        _link_sale_customer(conn, sale_id, customer, stamp)
        for item in items:
            qty = round(float(item['qty']), 2)
            line_total = money(item['unit_price'] * qty)
            prow = None
            if item.get('product_key') not in (None, ''):
                prow = conn.execute('SELECT id FROM products WHERE id=?',
                                    (item['product_key'],)).fetchone()
            elif item.get('legacy_item_id') is not None:
                prow = conn.execute('SELECT id FROM products WHERE legacy_item_id=?',
                                    (item['legacy_item_id'],)).fetchone()
            target_id, factor, base_name = None, 1.0, None
            if prow:
                target_id, factor, base_name = _stock_target(conn, prow['id'])
            stock_qty = round(qty * factor, 3)
            detail = f'Despacho {folio} (pendiente de cobro)'
            if factor != 1.0:
                detail += f' ({qty:g} empaque = {stock_qty:g} de {base_name})'
            conn.execute(
                """INSERT INTO sale_items(sale_id, legacy_item_id, product_key, sku, barcode, name,
                                          unit, unit_price, cost_price, qty, line_total,
                                          stock_product_id, stock_factor)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sale_id, item.get('legacy_item_id'), str(item.get('product_key') or '') or None,
                 item.get('sku'), item.get('barcode'),
                 item['name'], item.get('unit') or DEFAULT_UNIT_SYMBOL,
                 money(item['unit_price']), money(item.get('cost_price', 0)),
                 qty, line_total, target_id, factor),
            )
            if target_id:
                _apply_stock(conn, target_id, user_id, 'venta', -stock_qty,
                             detail, ref=folio, sale_id=sale_id)
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (stamp, user_id, 'venta_despachada', f'{folio} total={total} pendiente_cobro'),
        )
        conn.commit()
        return get_sale(sale_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_pending_sale(sale_id, cashier_user_id, shift_id, payments):
    """Cobra en Caja una venta que estaba en estado 'pending'."""
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        sale = conn.execute('SELECT * FROM sales WHERE id=?', (sale_id,)).fetchone()
        if not sale:
            raise ValueError('El ticket no existe.')
        if sale['status'] == 'completed':
            raise ValueError('Este ticket ya fue cobrado.')
        if sale['status'] == 'cancelled':
            raise ValueError('Este ticket fue cancelado.')
        if sale['status'] != 'pending':
            raise ValueError('Este ticket no está pendiente de cobro.')

        total = money(sale['total'])
        paid = money(sum(money(p['amount']) for p in payments))
        if paid + 0.005 < total:
            raise ValueError(f'El pago es insuficiente: faltan ${total - paid:,.2f}.')
        change = money(paid - total)
        stamp = now_text()
        linked_customer = conn.execute(
            'SELECT customer_id FROM sale_customers WHERE sale_id=?', (sale_id,)
        ).fetchone()
        if not linked_customer and not (sale['customer_name'] or '').strip():
            default_customer = _sale_customer(conn, None)
            if default_customer:
                _link_sale_customer(conn, sale_id, default_customer, stamp)
                conn.execute(
                    'UPDATE sales SET customer_name=? WHERE id=?',
                    (default_customer['nombre'], sale_id),
                )

        conn.execute(
            """UPDATE sales
                  SET status='completed', shift_id=?, paid_total=?, change_given=?,
                      paid_at=?, paid_by=?
                WHERE id=?""",
            (shift_id, paid, change, stamp, cashier_user_id, sale_id),
        )
        conn.execute('DELETE FROM payments WHERE sale_id=?', (sale_id,))
        for payment in payments:
            conn.execute(
                """INSERT INTO payments(sale_id, method, amount, reference, created_at)
                   VALUES(?,?,?,?,?)""",
                (sale_id, payment['method'], money(payment['amount']),
                 payment.get('reference') or None, stamp),
            )
        cash_net = money(sum(
            money(p['amount']) for p in payments if p['method'] == 'efectivo'
        ) - change)
        if cash_net:
            conn.execute(
                """INSERT INTO cash_movements(shift_id, user_id, created_at, kind, amount, reason, sale_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (shift_id, cashier_user_id, stamp, 'venta', cash_net,
                 f'Cobro ticket {sale["folio"]}', sale_id),
            )
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (stamp, cashier_user_id, 'venta_cobrada',
             f'{sale["folio"]} total={total} pago={paid} cambio={change}'),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_sale(sale_id)


def list_pending_sales():
    """Devuelve los tickets despachados pendientes de cobro en Caja."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT s.id, s.folio, s.shift_id, s.user_id, u.name AS user_name,
                      s.created_at, s.total, s.item_count, s.customer_name, s.note,
                      (SELECT GROUP_CONCAT(si.name || ' x' || si.qty ||
                              CASE WHEN si.unit IS NOT NULL AND si.unit <> ''
                                   THEN ' ' || si.unit ELSE '' END, ' · ')
                       FROM sale_items si WHERE si.sale_id = s.id) AS items_summary
               FROM sales s JOIN users u ON u.id = s.user_id
               WHERE s.status = 'pending'
               ORDER BY s.id ASC"""
        ).fetchall()
        out = []
        for r in rows:
            item = row_to_dict(r)
            item['total'] = money(item['total'])
            item['ticket_token'] = ticket_token(item['id'])
            out.append(item)
        return out
    finally:
        conn.close()


def get_sale(sale_id):
    conn = connect()
    try:
        sale = conn.execute(
            """SELECT s.*, u.name AS user_name, u.role AS user_role,
                      p.name AS paid_by_name,
                      sc.customer_id AS linked_customer_id,
                      sc.customer_rfc AS customer_rfc
               FROM sales s
               JOIN users u ON u.id=s.user_id
               LEFT JOIN users p ON p.id=s.paid_by
               LEFT JOIN sale_customers sc ON sc.sale_id=s.id
               WHERE s.id=?""", (sale_id,)
        ).fetchone()
        if not sale:
            return None
        data = row_to_dict(sale)
        data['customer_id'] = data.pop('linked_customer_id', None)
        data['items'] = [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM sale_items WHERE sale_id=? ORDER BY id', (sale_id,)
        ).fetchall()]
        data['payments'] = [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM payments WHERE sale_id=? ORDER BY id', (sale_id,)
        ).fetchall()]
        data['method_label'] = ' + '.join(
            {'efectivo': 'Efectivo', 'tarjeta': 'Tarjeta', 'transferencia': 'Transferencia'}.get(
                p['method'], p['method']) for p in data['payments']
        )
        data['ticket_token'] = ticket_token(sale_id)
        return data
    finally:
        conn.close()


def cancel_sale(sale_id, user_id, reason):
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        sale = conn.execute('SELECT * FROM sales WHERE id=?', (sale_id,)).fetchone()
        if not sale:
            raise ValueError('La venta no existe.')
        if sale['status'] == 'cancelled':
            raise ValueError('Esa venta ya está cancelada.')
        if sale['status'] not in ('completed', 'pending'):
            raise ValueError('Esa venta no se puede cancelar.')
        was_pending = (sale['status'] == 'pending')
        if not (reason or '').strip():
            raise ValueError('Escribe el motivo de la cancelación.')
        stamp = now_text()
        conn.execute(
            """UPDATE sales SET status='cancelled', cancelled_at=?, cancelled_by=?, cancel_reason=?
               WHERE id=?""",
            (stamp, user_id, reason.strip(), sale_id),
        )
        # Devoluciones previas: sólo se repone lo que el cliente todavía tiene
        # y sólo se reintegra el valor que falta por devolver.
        returned = {r['sale_item_id']: float(r['qty']) for r in conn.execute(
            """SELECT ri.sale_item_id, COALESCE(SUM(ri.qty),0) AS qty
               FROM return_items ri JOIN returns r ON r.id = ri.return_id
               WHERE r.sale_id=? AND r.status='completed'
               GROUP BY ri.sale_item_id""",
            (sale_id,),
        ).fetchall()}
        return_count = conn.execute(
            "SELECT COUNT(*) FROM returns WHERE sale_id=? AND status='completed'",
            (sale_id,),
        ).fetchone()[0]
        restored = 0.0
        for item in conn.execute(
                """SELECT id, legacy_item_id, product_key, qty,
                          stock_product_id, stock_factor
                   FROM sale_items WHERE sale_id=?""",
                (sale_id,)).fetchall():
            qty = round(float(item['qty']) - returned.get(item['id'], 0.0), 3)
            if qty <= 0.0001:
                continue
            factor = float(item['stock_factor'] or 1)
            if item['stock_product_id']:
                target = conn.execute('SELECT id FROM products WHERE id=?',
                                      (item['stock_product_id'],)).fetchone()
                if target:
                    stock_qty = round(qty * factor, 3)
                    detail = f'Cancelación {sale["folio"]} (saldo sin devolver)'
                    if factor != 1.0:
                        detail += f' ({qty:g} empaque = {stock_qty:g} pza)'
                    _apply_stock(conn, target['id'], user_id, 'cancelacion', stock_qty,
                                 detail, ref=sale['folio'], sale_id=sale_id)
                    restored += stock_qty
                continue
            prow = None
            if item['product_key']:
                prow = conn.execute('SELECT id FROM products WHERE id=?',
                                    (item['product_key'],)).fetchone()
            elif item['legacy_item_id'] is not None:
                prow = conn.execute('SELECT id FROM products WHERE legacy_item_id=?',
                                    (item['legacy_item_id'],)).fetchone()
            if prow:
                _apply_stock(conn, prow['id'], user_id, 'cancelacion', qty,
                             f'Cancelación {sale["folio"]} (saldo sin devolver)',
                             ref=sale['folio'], sale_id=sale_id)
                restored += qty
        cash_paid = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE sale_id=? AND method='efectivo'",
            (sale_id,),
        ).fetchone()[0]
        cash_net = money(cash_paid - sale['change_given'])
        # Se descuenta todo lo ya devuelto (sin importar la forma de pago):
        # la cancelación sólo debe reintegrar el valor que falta.
        refunded = money(conn.execute(
            """SELECT COALESCE(SUM(total),0) FROM returns
               WHERE sale_id=? AND status='completed'""",
            (sale_id,),
        ).fetchone()[0])
        cash_refund = money(cash_net - refunded)
        shift = conn.execute('SELECT status FROM shifts WHERE id=?', (sale['shift_id'],)).fetchone()
        if cash_refund > 0:
            # Si la venta pertenece a un turno cerrado, el reintegro se carga al
            # turno abierto actual para que la caja cuadre.
            target_shift_id = sale['shift_id']
            if not shift or shift['status'] != 'open':
                open_row = conn.execute(
                    "SELECT id FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if not open_row:
                    raise ValueError(
                        'Abre un turno para registrar la cancelación en efectivo.'
                    )
                target_shift_id = open_row['id']
            note = f'Cancelación {sale["folio"]}: {reason.strip()}'
            if return_count:
                note += ' (descontando devoluciones previas)'
            conn.execute(
                """INSERT INTO cash_movements(shift_id, user_id, created_at, kind, amount, reason, sale_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (target_shift_id, user_id, stamp, 'cancelacion', -cash_refund, note, sale_id),
            )
        detail = f'{sale["folio"]} motivo={reason.strip()}'
        if was_pending:
            detail += ' (pendiente_sin_cobro)'
        if return_count:
            detail += (f' devoluciones_previas={return_count}'
                       f' reposicion={restored:g} reembolso={cash_refund:.2f}')
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (stamp, user_id, 'venta_cancelada', detail),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_sale(sale_id)


def sales_since(iso_stamp):
    """Cantidad vendida por producto desde una fecha, neta de devoluciones."""
    if not iso_stamp:
        return {}
    conn = connect()
    try:
        sold = {r['legacy_item_id']: float(r['qty']) for r in conn.execute(
            """SELECT si.legacy_item_id, COALESCE(SUM(si.qty),0) AS qty
               FROM sale_items si JOIN sales s ON s.id=si.sale_id
               WHERE s.status='completed' AND s.created_at >= ?
               GROUP BY si.legacy_item_id""",
            (iso_stamp,),
        ).fetchall()}
        returned = {r['legacy_item_id']: float(r['qty']) for r in conn.execute(
            """SELECT ri.legacy_item_id, COALESCE(SUM(ri.qty),0) AS qty
               FROM return_items ri
               JOIN returns r ON r.id=ri.return_id
               JOIN sales s ON s.id=r.sale_id
               WHERE r.status='completed' AND s.status='completed' AND r.created_at >= ?
               GROUP BY ri.legacy_item_id""",
            (iso_stamp,),
        ).fetchall()}
        for legacy, qty in returned.items():
            sold[legacy] = sold.get(legacy, 0.0) - qty
        return sold
    finally:
        conn.close()


def count_sales_since(iso_stamp):
    """Ventas del POS posteriores a la última sincronización del catálogo."""
    if not iso_stamp:
        return 0
    conn = connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sales WHERE status='completed' AND created_at >= ?",
            (iso_stamp,),
        ).fetchone()[0]
    finally:
        conn.close()


def count_sales_total():
    """Total de ventas cobradas en esta caja (no existen en 1box)."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total),0) AS total FROM sales WHERE status='completed'"
        ).fetchone()
        return {'count': row['n'], 'total': money(row['total'])}
    finally:
        conn.close()


