"""
Importa los clientes del histórico 1box a pos.db sin detener el POS.

El origen se abre estrictamente en modo solo lectura. La importación se hace
en una transacción SQLite breve; antes se crea y verifica un respaldo consistente
usando la API de backup de SQLite (compatible con WAL).

Uso:
    runtime\\python.exe importar_clientes.py
    runtime\\python.exe importar_clientes.py --dry-run
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata

import pos_core

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORICO_DB = os.path.join(BASE_DIR, 'Historico', 'historico_1box.db')


def clean(value):
    return ' '.join(str(value or '').split())


def normalized(value):
    value = unicodedata.normalize('NFKD', clean(value))
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    return value.casefold()


def normalize_city(value):
    value = clean(value)
    aliases = {
        'xicotepec de juarez': 'Xicotepec de Juárez',
        'xicotepec de juarez puebla': 'Xicotepec de Juárez, Puebla',
        'xicotepec': 'Xicotepec',
        'puebla': 'Puebla',
        'huauchinango': 'Huauchinango',
        'zihuateutla': 'Zihuateutla',
        'venustiano carranza': 'Venustiano Carranza',
        'ciudad de mexico': 'Ciudad de México',
    }
    return aliases.get(normalized(value), value)


def normalize_state(value):
    value = clean(value)
    aliases = {
        'puebla': 'Puebla',
        'veracruz': 'Veracruz',
        'hidalgo': 'Hidalgo',
        'distrito federal': 'Ciudad de México',
        'ciudad de mexico': 'Ciudad de México',
        'nuevo leon': 'Nuevo León',
        'estado de mexico': 'Estado de México',
        'queretaro': 'Querétaro',
        'tlaxcala': 'Tlaxcala',
        'jalisco': 'Jalisco',
        'xicotepec': 'Puebla',  # localidad capturada erróneamente como estado
        'xicotepec de juarez': 'Puebla',
    }
    return aliases.get(normalized(value), value)


def normalize_phone(value):
    raw = clean(value)
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 12 and digits.startswith('52'):
        digits = digits[2:]
    return digits or None


def split_emails(value):
    parts = [clean(part) for part in re.split(r'[,;]', str(value or ''))]
    emails = []
    for email in parts:
        email = email.replace('hotmail:com', 'hotmail.com').strip()
        if email and email.casefold() not in {e.casefold() for e in emails}:
            emails.append(email)
    return (emails[0] if emails else None,
            json.dumps(emails[1:], ensure_ascii=False) if len(emails) > 1 else None)


def customer_values(row, stamp):
    first = clean(row['first_name'])
    last = clean(row['last_name'])
    full_name = clean(' '.join(part for part in (first, last) if part))
    if not full_name:
        full_name = clean(row['full_name'])
    if not full_name:
        full_name = f'Cliente sin nombre · 1box {row["id"]}'
    is_company = not bool(last) and bool(first)
    is_public = int(row['id']) == 1 or normalized(full_name) == 'publico en general'
    if is_public:
        full_name = 'PÚBLICO EN GENERAL'
        is_company = False
    email, alt_emails = split_emails(row['email'])
    return (
        int(row['id']),
        full_name,
        last or None,
        int(is_company),
        clean(row['account_number']).upper() or None,
        normalize_phone(row['phone_number']),
        email,
        alt_emails,
        clean(row['address_1']) or None,
        normalize_city(row['city']) or None,
        normalize_state(row['state']) or None,
        clean(row['zip']) or None,
        float(row['balance'] or 0),
        float(row['credit_limit'] or 0),
        float(row['points'] or 0),
        1 if int(row['id']) == 3 else 0,
        int(is_public),
        1 if not int(row['deleted'] or 0) else 0,
        stamp,
        stamp,
    )


def create_customer_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_id INTEGER UNIQUE,
            nombre TEXT NOT NULL,
            apellido TEXT,
            es_empresa INTEGER NOT NULL DEFAULT 0,
            rfc TEXT,
            telefono TEXT,
            email TEXT,
            alt_emails TEXT,
            direccion TEXT,
            ciudad TEXT,
            estado TEXT,
            cp TEXT,
            saldo REAL NOT NULL DEFAULT 0,
            limite_credito REAL NOT NULL DEFAULT 0,
            puntos REAL NOT NULL DEFAULT 0,
            es_generico INTEGER NOT NULL DEFAULT 0,
            es_publico INTEGER NOT NULL DEFAULT 0,
            notas TEXT,
            activo INTEGER NOT NULL DEFAULT 1,
            origen TEXT NOT NULL DEFAULT 'historico_1box',
            creado_en TEXT,
            actualizado_en TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sale_customers (
            sale_id INTEGER PRIMARY KEY REFERENCES sales(id) ON DELETE CASCADE,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            customer_name TEXT NOT NULL,
            customer_rfc TEXT,
            created_at TEXT NOT NULL
        )
    """)
    customer_columns = {row[1] for row in conn.execute('PRAGMA table_info(customers)')}
    if 'es_publico' not in customer_columns:
        conn.execute('ALTER TABLE customers ADD COLUMN es_publico INTEGER NOT NULL DEFAULT 0')
    sale_customer_columns = {row[1] for row in conn.execute('PRAGMA table_info(sale_customers)')}
    if 'customer_rfc' not in sale_customer_columns:
        conn.execute('ALTER TABLE sale_customers ADD COLUMN customer_rfc TEXT')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_nombre ON customers(nombre)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_rfc ON customers(rfc)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_telefono ON customers(telefono)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sale_customers_customer ON sale_customers(customer_id)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Valida el origen sin escribir en pos.db.')
    args = parser.parse_args()

    if not os.path.isfile(HISTORICO_DB):
        print(f'No se encontró el histórico: {HISTORICO_DB}', file=sys.stderr)
        return 2

    source_uri = 'file:' + HISTORICO_DB.replace('\\', '/') + '?mode=ro'
    source = sqlite3.connect(source_uri, uri=True, timeout=15)
    source.row_factory = sqlite3.Row
    source.execute('PRAGMA query_only=ON')
    try:
        rows = source.execute("""
            SELECT c.id, c.first_name, c.last_name, c.account_number, c.balance,
                   c.credit_limit, c.points, c.deleted, c.tax_certificate,
                   p.full_name, p.phone_number, p.email, p.address_1, p.city,
                   p.state, p.zip
            FROM customers c
            LEFT JOIN people p ON p.person_id=c.person_id
            ORDER BY c.id
        """).fetchall()
    finally:
        source.close()

    if len(rows) != 403:
        print(f'ADVERTENCIA: se esperaban 403 clientes; el origen contiene {len(rows)}.')
    stamp = pos_core.now_text()
    values = [customer_values(row, stamp) for row in rows]
    print(f'Clientes a importar: {len(values)}')
    print('La base histórica se abrió en solo lectura.')
    if args.dry_run:
        print('Dry run: no se creó respaldo ni se escribió en pos.db.')
        return 0

    backup_path = pos_core.backup('clientes_preimport')
    print(f'Respaldo verificado: {backup_path}')

    conn = sqlite3.connect(pos_core.POS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')
    try:
        conn.execute('BEGIN IMMEDIATE')
        create_customer_schema(conn)
        conn.executemany("""
            INSERT INTO customers(
                legacy_id, nombre, apellido, es_empresa, rfc, telefono, email,
                alt_emails, direccion, ciudad, estado, cp, saldo, limite_credito,
                puntos, es_generico, es_publico, activo, creado_en, actualizado_en
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(legacy_id) DO UPDATE SET
                nombre=excluded.nombre,
                apellido=excluded.apellido,
                es_empresa=excluded.es_empresa,
                rfc=excluded.rfc,
                telefono=excluded.telefono,
                email=excluded.email,
                alt_emails=excluded.alt_emails,
                direccion=excluded.direccion,
                ciudad=excluded.ciudad,
                estado=excluded.estado,
                cp=excluded.cp,
                saldo=excluded.saldo,
                limite_credito=excluded.limite_credito,
                puntos=excluded.puntos,
                es_generico=excluded.es_generico,
                es_publico=excluded.es_publico,
                activo=excluded.activo,
                actualizado_en=excluded.actualizado_en
        """, values)
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(pos_core.SCHEMA_VERSION),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    checkpoint = sqlite3.connect(pos_core.POS_DB, timeout=30)
    try:
        result = checkpoint.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
    finally:
        checkpoint.close()

    verify = sqlite3.connect(f'file:{pos_core.POS_DB}?mode=ro', uri=True)
    try:
        total = verify.execute('SELECT COUNT(*) FROM customers').fetchone()[0]
        active = verify.execute('SELECT COUNT(*) FROM customers WHERE activo=1').fetchone()[0]
        linked = verify.execute('SELECT COUNT(*) FROM sale_customers').fetchone()[0]
        print(f'Importación lista: {total} clientes ({active} activos); enlaces de ventas previos: {linked}.')
        print(f'WAL checkpoint (busy, log, checkpointed): {tuple(result) if result else "sin resultado"}')
    finally:
        verify.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
