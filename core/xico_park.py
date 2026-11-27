"""
Módulo independiente XICO-PARK.

Utiliza su propia base de datos SQLite (`xico_park.db`) totalmente separada
de `pos.db` para registrar entradas, salidas y gastos sin interferir en
ningún turno, corte ni cálculo de la caja principal.
"""

import datetime
import os
import sqlite3
from decimal import Decimal, ROUND_HALF_UP

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XICO_DB = os.path.join(BASE_DIR, 'xico_park.db')

VALID_KINDS = ('entrada', 'salida', 'gasto')


def _money(value):
    return float(Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _today():
    return datetime.date.today().isoformat()


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _connect():
    conn = sqlite3.connect(XICO_DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def init_xico_db():
    with _connect() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS xico_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('entrada', 'salida', 'gasto')),
                amount REAL NOT NULL,
                concept TEXT NOT NULL,
                user_name TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                cancelled_by TEXT NOT NULL DEFAULT '',
                cancelled_at TEXT NOT NULL DEFAULT '',
                cancel_reason TEXT NOT NULL DEFAULT ''
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_xico_day ON xico_movements(day, active)')
        conn.commit()


def add_movement(kind, amount, concept, user_name='', day=None):
    init_xico_db()
    kind = str(kind or '').strip().lower()
    if kind not in VALID_KINDS:
        raise ValueError('Tipo inválido. Usa entrada, salida o gasto.')
    try:
        amt = _money(float(amount))
    except (TypeError, ValueError):
        raise ValueError('Monto inválido.')
    if amt <= 0:
        raise ValueError('El monto debe ser mayor a cero.')
    concept = str(concept or '').strip()[:200]
    if not concept:
        raise ValueError('Escribe el concepto o motivo.')
    target_day = str(day or _today()).strip()[:10] or _today()
    stamp = _now()
    with _connect() as conn:
        cur = conn.execute(
            '''
            INSERT INTO xico_movements (day, created_at, kind, amount, concept, user_name, active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ''',
            (target_day, stamp, kind, amt, concept, str(user_name or '').strip()[:80]),
        )
        conn.commit()
        row = conn.execute('SELECT * FROM xico_movements WHERE id = ?', (cur.lastrowid,)).fetchone()
        return dict(row)


def cancel_movement(movement_id, reason='', user_name=''):
    init_xico_db()
    try:
        mid = int(movement_id)
    except (TypeError, ValueError):
        raise ValueError('Movimiento inválido.')
    reason = str(reason or '').strip()[:200] or 'Cancelado por usuario'
    with _connect() as conn:
        row = conn.execute('SELECT * FROM xico_movements WHERE id = ?', (mid,)).fetchone()
        if not row:
            raise ValueError('El movimiento no existe.')
        if not row['active']:
            raise ValueError('Ese movimiento ya estaba cancelado.')
        conn.execute(
            '''
            UPDATE xico_movements
            SET active = 0, cancelled_by = ?, cancelled_at = ?, cancel_reason = ?
            WHERE id = ?
            ''',
            (str(user_name or '').strip()[:80], _now(), reason, mid),
        )
        conn.commit()
        updated = conn.execute('SELECT * FROM xico_movements WHERE id = ?', (mid,)).fetchone()
        return dict(updated)


def day_summary(day=None):
    init_xico_db()
    target_day = str(day or _today()).strip()[:10] or _today()
    with _connect() as conn:
        rows = conn.execute(
            '''
            SELECT * FROM xico_movements
            WHERE day = ?
            ORDER BY id DESC
            ''',
            (target_day,),
        ).fetchall()
    movements = [dict(r) for r in rows]
    entradas = _money(sum(m['amount'] for m in movements if m['active'] and m['kind'] == 'entrada'))
    salidas = _money(sum(m['amount'] for m in movements if m['active'] and m['kind'] == 'salida'))
    gastos = _money(sum(m['amount'] for m in movements if m['active'] and m['kind'] == 'gasto'))
    salidas_gastos = _money(salidas + gastos)
    neto = _money(entradas - salidas_gastos)
    active_count = sum(1 for m in movements if m['active'])
    return {
        'day': target_day,
        'movements': movements,
        'count': active_count,
        'totals': {
            'entradas': entradas,
            'salidas': salidas,
            'gastos': gastos,
            'salidas_gastos': salidas_gastos,
            'neto': neto,
        },
    }
