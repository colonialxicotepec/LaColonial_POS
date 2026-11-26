"""Usuarios, PIN, sesiones y auditoría."""

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
from core.db import connect, row_to_dict, now_text

# ---------------------------------------------------------------- usuarios

def _pin_hash(pin, salt_hex):
    return hashlib.pbkdf2_hmac(
        'sha256', str(pin).encode('utf-8'), bytes.fromhex(salt_hex), 120_000
    ).hex()


def make_pin(pin):
    salt = secrets.token_hex(16)
    return salt, _pin_hash(pin, salt)


def check_pin(row, pin):
    if not row or not pin:
        return False
    return secrets.compare_digest(row['pin_hash'], _pin_hash(pin, row['pin_salt']))


def users_exist():
    conn = connect()
    try:
        return conn.execute('SELECT COUNT(*) FROM users WHERE active=1').fetchone()[0] > 0
    finally:
        conn.close()


def create_user(name, role, pin, max_users=8):
    conn = connect()
    try:
        active = conn.execute('SELECT COUNT(*) FROM users WHERE active=1').fetchone()[0]
        if active >= int(max_users or 8):
            raise ValueError(f'No se pueden crear más de {max_users} usuarios activos.')
        salt, hashed = make_pin(pin)
        cur = conn.execute(
            'INSERT INTO users(name, role, pin_salt, pin_hash, active, created_at) VALUES(?,?,?,?,1,?)',
            (name.strip(), role, salt, hashed, now_text()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def find_user_by_pin(pin):
    """Busca al usuario activo cuyo PIN coincide. Devuelve dict o None."""
    if not pin:
        return None
    conn = connect()
    try:
        rows = conn.execute('SELECT * FROM users WHERE active=1').fetchall()
    finally:
        conn.close()
    for row in rows:
        if check_pin(row, pin):
            return {'id': row['id'], 'name': row['name'], 'role': row['role']}
    return None


def get_user(user_id):
    conn = connect()
    try:
        row = conn.execute('SELECT id, name, role, active FROM users WHERE id=?', (user_id,)).fetchone()
        return row_to_dict(row)
    finally:
        conn.close()


def create_session(user_id):
    token = secrets.token_urlsafe(24)
    stamp = now_text()
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO sessions(token, user_id, created_at, last_seen) VALUES(?,?,?,?)',
            (token, user_id, stamp, stamp),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def get_session(token, session_hours=12):
    if not token:
        return None
    conn = connect()
    try:
        row = conn.execute(
            """SELECT s.token, s.user_id, s.last_seen, u.name, u.role, u.active
               FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?""",
            (token,),
        ).fetchone()
        if not row or not row['active']:
            return None
        try:
            seen = datetime.datetime.fromisoformat(row['last_seen'])
        except ValueError:
            return None
        if datetime.datetime.now() - seen > datetime.timedelta(hours=session_hours):
            conn.execute('DELETE FROM sessions WHERE token=?', (token,))
            conn.commit()
            return None
        conn.execute('UPDATE sessions SET last_seen=? WHERE token=?', (now_text(), token))
        conn.commit()
        return {'token': token, 'user_id': row['user_id'], 'name': row['name'], 'role': row['role']}
    finally:
        conn.close()


def delete_session(token):
    if not token:
        return
    conn = connect()
    try:
        conn.execute('DELETE FROM sessions WHERE token=?', (token,))
        conn.commit()
    finally:
        conn.close()


def change_pin(user_id, new_pin):
    salt, hashed = make_pin(new_pin)
    conn = connect()
    try:
        conn.execute('UPDATE users SET pin_salt=?, pin_hash=? WHERE id=?', (salt, hashed, user_id))
        conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- auditoría

def audit(user_id, action, detail=''):
    conn = connect()
    try:
        conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (now_text(), user_id, action, str(detail)[:2000]),
        )
        conn.commit()
    finally:
        conn.close()



def list_users():
    conn = connect()
    try:
        rows = conn.execute(
            'SELECT id, name, role, active, created_at FROM users ORDER BY role DESC, name'
        ).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


def set_user_active(user_id, active):
    conn = connect()
    try:
        conn.execute('UPDATE users SET active=? WHERE id=?', (1 if active else 0, user_id))
        conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        conn.commit()
    finally:
        conn.close()
    audit(None if active else user_id, 'usuario_actualizado',
          f'usuario={user_id} activo={1 if active else 0}')


