"""Turnos de caja y movimientos de efectivo."""

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
from core.db import connect, row_to_dict, money, now_text
from core.users import audit

# ---------------------------------------------------------------- turnos

def open_shift(user_id, opening_cash, notes=''):
    conn = connect()
    try:
        current = conn.execute("SELECT * FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
        if current:
            raise ValueError(f"Ya hay un turno abierto (#{current['id']}). Ciérralo antes de abrir otro.")
        cur = conn.execute(
            """INSERT INTO shifts(user_id, opened_at, opening_cash, status, notes)
               VALUES(?,?,?,'open',?)""",
            (user_id, now_text(), money(opening_cash), notes.strip() if notes else None),
        )
        conn.commit()
        return shift_detail(cur.lastrowid)
    finally:
        conn.close()


def get_open_shift():
    conn = connect()
    try:
        row = conn.execute(
            """SELECT s.*, u.name AS user_name FROM shifts s JOIN users u ON u.id=s.user_id
               WHERE s.status='open' ORDER BY s.id DESC LIMIT 1"""
        ).fetchone()
        return row_to_dict(row)
    finally:
        conn.close()


def shift_summary(shift_id):
    conn = connect()
    try:
        shift = conn.execute(
            """SELECT s.*, u.name AS user_name FROM shifts s JOIN users u ON u.id=s.user_id
               WHERE s.id=?""", (shift_id,)
        ).fetchone()
        if not shift:
            return None
        sales = conn.execute(
            """SELECT COUNT(*) AS count, COALESCE(SUM(total),0) AS total,
                      COALESCE(SUM(change_given),0) AS change_given
               FROM sales WHERE shift_id=? AND status='completed'""",
            (shift_id,),
        ).fetchone()
        by_method = {r['method']: money(r['amount']) for r in conn.execute(
            """SELECT p.method, COALESCE(SUM(p.amount),0) AS amount
               FROM payments p JOIN sales s ON s.id=p.sale_id
               WHERE s.shift_id=? AND s.status='completed'
               GROUP BY p.method""",
            (shift_id,),
        ).fetchall()}
        movements = conn.execute(
            'SELECT COALESCE(SUM(amount),0) AS total FROM cash_movements WHERE shift_id=?',
            (shift_id,),
        ).fetchone()['total']
        cash_in_out = conn.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN kind='ingreso' THEN amount ELSE 0 END), 0) AS cash_in,
                 COUNT(CASE WHEN kind='ingreso' THEN 1 END) AS cash_in_count,
                 COALESCE(SUM(CASE WHEN kind='retiro' THEN ABS(amount) ELSE 0 END), 0) AS cash_out,
                 COUNT(CASE WHEN kind='retiro' THEN 1 END) AS cash_out_count
               FROM cash_movements WHERE shift_id=?""",
            (shift_id,),
        ).fetchone()
        cancelled = conn.execute(
            "SELECT COUNT(*) AS n FROM sales WHERE shift_id=? AND status='cancelled'",
            (shift_id,),
        ).fetchone()['n']
        pending_sales = conn.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(total),0) AS t
               FROM sales WHERE shift_id=? AND status='pending'""",
            (shift_id,),
        ).fetchone()
        returns = conn.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(r.total),0) AS t
               FROM returns r JOIN sales s ON s.id = r.sale_id
               WHERE r.shift_id=? AND r.status='completed' AND s.status<>'cancelled'""",
            (shift_id,),
        ).fetchone()
        # Detalle de entradas/salidas manuales: se imprime en el corte para
        # que se pueda auditar por qué el efectivo cambió.
        movements_list = [
            {
                'kind': r['kind'],
                'amount': money(abs(r['amount'])),
                'reason': r['reason'] or '',
                'user_name': r['user_name'] or '',
                'created_at': r['created_at'],
            }
            for r in conn.execute(
                """SELECT m.kind, m.amount, m.reason, m.created_at, u.name AS user_name
                   FROM cash_movements m LEFT JOIN users u ON u.id = m.user_id
                   WHERE m.shift_id=? AND m.kind IN ('ingreso','retiro') ORDER BY m.id""",
                (shift_id,),
            ).fetchall()
        ]
        returns_list = [
            {
                'folio': r['folio'],
                'total': money(r['total']),
                'method': r['method'],
                'reason': r['reason'] or '',
                'created_at': r['created_at'],
            }
            for r in conn.execute(
                """SELECT r.folio, r.total, r.method, r.reason, r.created_at
                   FROM returns r JOIN sales s ON s.id = r.sale_id
                   WHERE r.shift_id=? AND r.status='completed' AND s.status<>'cancelled'
                   ORDER BY r.id""",
                (shift_id,),
            ).fetchall()
        ]
        cash_net = money(by_method.get('efectivo', 0) - sales['change_given'])
        # El efectivo esperado se calcula con los movimientos reales de caja
        # (ventas en efectivo netas, cancelaciones, ingresos y retiros).
        expected = money(shift['opening_cash'] + movements)
        return {
            'id': shift['id'],
            'user_id': shift['user_id'],
            'user_name': shift['user_name'],
            'opened_at': shift['opened_at'],
            'closed_at': shift['closed_at'],
            'status': shift['status'],
            'opening_cash': money(shift['opening_cash']),
            'counted_cash': money(shift['counted_cash']) if shift['counted_cash'] is not None else None,
            'expected_cash': money(shift['expected_cash']) if shift['expected_cash'] is not None else expected,
            'difference': money(shift['difference']) if shift['difference'] is not None else None,
            'notes': shift['notes'],
            'sales_count': sales['count'],
            'sales_total': money(sales['total']),
            'pending_count': pending_sales['n'],
            'pending_total': money(pending_sales['t']),
            'cancelled_count': cancelled,
            'returns_count': returns['n'],
            'returns_total': money(returns['t']),
            'cash_net': cash_net,
            'by_method': {
                'efectivo': cash_net,
                'tarjeta': by_method.get('tarjeta', 0),
                'transferencia': by_method.get('transferencia', 0),
            },
            'cash_in': money(cash_in_out['cash_in']),
            'cash_in_count': cash_in_out['cash_in_count'],
            'cash_out': money(cash_in_out['cash_out']),
            'cash_out_count': cash_in_out['cash_out_count'],
            'movements': money(movements),
            'movements_list': movements_list,
            'returns_list': returns_list,
            'denoms': [row_to_dict(r) for r in conn.execute(
                'SELECT value, qty, amount FROM shift_denoms WHERE shift_id=? ORDER BY value DESC',
                (shift_id,),
            ).fetchall()],
            'computed_expected_cash': expected,
        }
    finally:
        conn.close()


def shift_detail(shift_id):
    return shift_summary(shift_id)


def close_shift(shift_id, counted_cash, user_id, notes='', denominations=None):
    """Cierra el turno. `denominations` es {valor: piezas} del conteo físico."""
    summary = shift_summary(shift_id)
    if not summary:
        raise ValueError('El turno no existe.')
    if summary['status'] != 'open':
        raise ValueError('El turno ya está cerrado.')
    expected = summary['computed_expected_cash']
    try:
        counted_raw = float(counted_cash)
    except (TypeError, ValueError):
        raise ValueError('El efectivo contado no es válido.')
    if not math.isfinite(counted_raw):
        raise ValueError('El efectivo contado no es válido.')
    if counted_raw < 0:
        raise ValueError('El efectivo contado no puede ser negativo.')
    counted = money(counted_raw)
    difference = money(counted - expected)
    denoms = []
    for raw_value, raw_qty in (denominations or {}).items():
        try:
            value = money(float(raw_value))
            qty = int(raw_qty or 0)
        except (TypeError, ValueError):
            continue
        if value <= 0 or qty <= 0:
            continue
        denoms.append((value, qty, money(value * qty)))
    # El dinero contado y el desglose por denominación tienen que decir lo mismo.
    if denoms:
        total_denoms = money(sum(amount for _v, _q, amount in denoms))
        if abs(total_denoms - counted) > 0.005:
            raise ValueError(
                f'El efectivo contado (${counted:,.2f}) no coincide con la suma del '
                f'conteo por denominación (${total_denoms:,.2f}). Revisa las piezas.'
            )
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute(
            """UPDATE shifts SET status='closed', closed_at=?, counted_cash=?, expected_cash=?,
                      difference=?, notes=COALESCE(?, notes) WHERE id=?""",
            (now_text(), counted, expected, difference, notes.strip() or None, shift_id),
        )
        conn.execute('DELETE FROM shift_denoms WHERE shift_id=?', (shift_id,))
        for value, qty, amount in denoms:
            conn.execute(
                'INSERT INTO shift_denoms(shift_id, value, qty, amount) VALUES(?,?,?,?)',
                (shift_id, value, qty, amount),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    audit(user_id, 'turno_cerrado', f'turno={shift_id} esperado={expected} contado={counted} diferencia={difference}')
    return shift_summary(shift_id)


def cash_movement(shift_id, user_id, kind, amount, reason=''):
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO cash_movements(shift_id, user_id, created_at, kind, amount, reason)
               VALUES(?,?,?,?,?,?)""",
            (shift_id, user_id, now_text(), kind, money(amount), reason or None),
        )
        conn.commit()
    finally:
        conn.close()
    audit(user_id, 'movimiento_efectivo', f'turno={shift_id} {kind} {money(amount)} {reason}')


def list_cash_movements(shift_id):
    """Lista las entradas ('ingreso') y salidas ('retiro') de efectivo del turno."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT m.id, m.shift_id, m.user_id, u.name AS user_name,
                      m.created_at, m.kind, m.amount, m.reason
               FROM cash_movements m
               LEFT JOIN users u ON u.id = m.user_id
               WHERE m.shift_id=? AND m.kind IN ('ingreso', 'retiro')
               ORDER BY m.id DESC""",
            (shift_id,),
        ).fetchall()
        return [
            {
                'id': r['id'],
                'shift_id': r['shift_id'],
                'user_id': r['user_id'],
                'user_name': r['user_name'] or '',
                'created_at': r['created_at'],
                'kind': r['kind'],
                'amount': money(r['amount']),
                'reason': r['reason'] or '',
            }
            for r in rows
        ]
    finally:
        conn.close()


def update_cash_movement(movement_id, shift_id, user_id, kind, signed_amount, reason):
    """Edita una entrada ('ingreso') o salida ('retiro') de efectivo del turno abierto."""
    if kind not in ('ingreso', 'retiro'):
        raise ValueError('El movimiento debe ser ingreso o retiro.')
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('Escribe el motivo del movimiento.')
    conn = connect()
    try:
        row = conn.execute(
            'SELECT * FROM cash_movements WHERE id=?', (movement_id,)
        ).fetchone()
        if not row:
            raise ValueError('El movimiento no existe.')
        if row['shift_id'] != shift_id:
            raise ValueError('Sólo se pueden editar movimientos del turno abierto actual.')
        if row['kind'] not in ('ingreso', 'retiro') or row['sale_id'] is not None:
            raise ValueError('Sólo se pueden editar entradas o salidas manuales de efectivo.')
        old_kind = row['kind']
        old_amount = money(row['amount'])
        old_reason = row['reason'] or ''
        conn.execute(
            """UPDATE cash_movements
                  SET kind=?, amount=?, reason=?
                WHERE id=?""",
            (kind, money(signed_amount), reason, movement_id),
        )
        conn.commit()
    finally:
        conn.close()
    audit(
        user_id,
        'movimiento_efectivo_editado',
        f'id={movement_id} turno={shift_id} antes=({old_kind} {old_amount} "{old_reason}") '
        f'ahora=({kind} {money(signed_amount)} "{reason}")',
    )


def delete_cash_movement(movement_id, shift_id, user_id):
    """Elimina una entrada ('ingreso') o salida ('retiro') de efectivo del turno abierto."""
    conn = connect()
    try:
        row = conn.execute(
            'SELECT * FROM cash_movements WHERE id=?', (movement_id,)
        ).fetchone()
        if not row:
            raise ValueError('El movimiento no existe.')
        if row['shift_id'] != shift_id:
            raise ValueError('Sólo se pueden eliminar movimientos del turno abierto actual.')
        if row['kind'] not in ('ingreso', 'retiro') or row['sale_id'] is not None:
            raise ValueError('Sólo se pueden eliminar entradas o salidas manuales de efectivo.')
        old_kind = row['kind']
        old_amount = money(row['amount'])
        old_reason = row['reason'] or ''
        conn.execute('DELETE FROM cash_movements WHERE id=?', (movement_id,))
        conn.commit()
    finally:
        conn.close()
    audit(
        user_id,
        'movimiento_efectivo_eliminado',
        f'id={movement_id} turno={shift_id} {old_kind} {old_amount} "{old_reason}"',
    )


