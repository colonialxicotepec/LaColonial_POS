"""
Migra el histórico de `1box` (MySQL, solo lectura) a una base SQLite de consulta.

La base resultante (`historico_1box.db`) NO participa en la venta: es para
reportes y consultas (ventas por día, por producto, por cajero, márgenes).
La caja vive en `pos.db`.

Uso:
    python migrar_historico.py                 # todo el histórico
    python migrar_historico.py --desde 2024-01-01
    python migrar_historico.py --destino otra.db
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time
from decimal import Decimal

import pymysql

HIST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HIST_DIR) if os.path.basename(HIST_DIR).lower() == 'historico' else HIST_DIR
DEFAULT_DEST = os.path.join(HIST_DIR if os.path.basename(HIST_DIR).lower() == 'historico' else os.path.join(HIST_DIR, 'Historico'), 'historico_1box.db')
DEFAULT_SOURCE = os.path.join(PROJECT_DIR, 'pos.db')

# --- tablas de 1box -> tablas de SQLite -------------------------------------
SPECS = [
    ('phppos_sales', 'sales', [
        ('sale_id', 'sale_id'), ('sale_time', 'sale_time'), ('customer_id', 'customer_id'),
        ('employee_id', 'employee_id'), ('sold_by_employee_id', 'sold_by_employee_id'),
        ('comment', 'comment'), ('deleted', 'deleted'), ('suspended', 'suspended'),
        ('location_id', 'location_id'), ('register_id', 'register_id'),
        ('payment_type', 'payment_type'), ('cc_ref_no', 'cc_ref_no'),
        ('subtotal', 'subtotal'), ('tax', 'tax'), ('total', 'total'), ('profit', 'profit'),
        ('total_quantity_purchased', 'total_quantity_purchased'), ('non_taxable', 'non_taxable'),
        ('rule_discount', 'rule_discount'), ('return_sale_id', 'return_sale_id'),
        ('tip', 'tip'), ('is_ecommerce', 'is_ecommerce'), ('exchange_rate', 'exchange_rate'),
        ('last_modified', 'last_modified'),
    ]),
    ('phppos_sales_items', 'sale_items', [
        ('sale_id', 'sale_id'), ('item_id', 'item_id'), ('description', 'description'),
        ('quantity_purchased', 'qty'), ('item_cost_price', 'cost_price'),
        ('item_unit_price', 'unit_price'),
        ('regular_item_unit_price_at_time_of_sale', 'regular_price'),
        ('discount_percent', 'discount_percent'), ('subtotal', 'subtotal'),
        ('tax', 'tax'), ('total', 'total'), ('profit', 'profit'),
        ('unit_quantity', 'unit_quantity'), ('damaged_qty', 'damaged_qty'),
    ]),
    ('phppos_sales_payments', 'payments', [
        ('payment_id', 'payment_id'), ('sale_id', 'sale_id'), ('payment_type', 'payment_type'),
        ('payment_amount', 'amount'), ('auth_code', 'auth_code'), ('ref_no', 'ref_no'),
        ('payment_date', 'payment_date'),
    ]),
    ('phppos_customers', 'customers', [
        ('id', 'id'), ('person_id', 'person_id'), ('account_number', 'account_number'),
        ('balance', 'balance'), ('credit_limit', 'credit_limit'), ('points', 'points'),
        ('deleted', 'deleted'), ('tax_certificate', 'tax_certificate'),
        ('internal_notes', 'internal_notes'),
    ]),
    ('phppos_employees', 'employees', [
        ('id', 'id'), ('person_id', 'person_id'), ('username', 'username'),
        ('deleted', 'deleted'), ('inactive', 'inactive'), ('hire_date', 'hire_date'),
        ('termination_date', 'termination_date'), ('commission_percent', 'commission_percent'),
    ]),
    ('phppos_suppliers', 'suppliers', [
        ('id', 'id'), ('person_id', 'person_id'), ('company_name', 'company_name'),
        ('account_number', 'account_number'), ('balance', 'balance'), ('deleted', 'deleted'),
    ]),
    ('phppos_categories', 'categories', [
        ('id', 'id'), ('name', 'name'), ('parent_id', 'parent_id'),
        ('deleted', 'deleted'), ('hide_from_grid', 'hide_from_grid'),
    ]),
    ('phppos_items', 'items', [
        ('item_id', 'item_id'), ('name', 'name'), ('category_id', 'category_id'),
        ('supplier_id', 'supplier_id'), ('item_number', 'item_number'),
        ('product_id', 'product_id'), ('cost_price', 'cost_price'),
        ('unit_price', 'unit_price'), ('promo_price', 'promo_price'),
        ('deleted', 'deleted'), ('item_inactive', 'item_inactive'),
        ('reorder_level', 'reorder_level'), ('tax_included', 'tax_included'),
        ('last_modified', 'last_modified'),
    ]),
    ('phppos_receivings', 'receivings', [
        ('receiving_id', 'receiving_id'), ('receiving_time', 'receiving_time'),
        ('supplier_id', 'supplier_id'), ('employee_id', 'employee_id'),
        ('comment', 'comment'), ('deleted', 'deleted'), ('location_id', 'location_id'),
        ('subtotal', 'subtotal'), ('tax', 'tax'), ('total', 'total'),
        ('payment_type', 'payment_type'),
    ]),
    ('phppos_receivings_items', 'receiving_items', [
        ('receiving_id', 'receiving_id'), ('item_id', 'item_id'), ('description', 'description'),
        ('quantity_purchased', 'qty'), ('item_cost_price', 'cost_price'),
        ('item_unit_price', 'unit_price'), ('subtotal', 'subtotal'),
        ('tax', 'tax'), ('total', 'total'),
    ]),
    ('phppos_locations', 'locations', [
        ('location_id', 'location_id'), ('name', 'name'), ('company', 'company'),
        ('address', 'address'), ('phone', 'phone'), ('email', 'email'),
    ]),
    ('phppos_registers', 'registers', [
        ('register_id', 'register_id'), ('location_id', 'location_id'),
        ('name', 'name'), ('deleted', 'deleted'),
    ]),
]

INDEXES = [
    ('sales', ['sale_time']), ('sales', ['employee_id']), ('sales', ['customer_id']),
    ('sales', ['register_id']),
    ('sale_items', ['sale_id']), ('sale_items', ['item_id']), ('sale_items', ['sku']),
    ('payments', ['sale_id']), ('payments', ['payment_type']),
    ('customers', ['account_number']), ('items', ['sku']), ('items', ['item_number']),
    ('items', ['category_id']), ('receiving_items', ['receiving_id']),
    ('receiving_items', ['item_id']), ('receivings', ['receiving_time']),
]

# Tablas adicionales: operación y configuración de 1box que no están en las
# tablas principales pero que alguien puede necesitar (cortes de caja,
# conteos de inventario, gastos, reglas de precio, historial de precios...).
EXTRA_TABLES = [
    ('phppos_register_log', 'register_log'),
    ('phppos_register_log_payments', 'register_log_payments'),
    ('phppos_register_log_denoms', 'register_log_denoms'),
    ('phppos_register_log_audit', 'register_log_audit'),
    ('phppos_inventory', 'inventory_log'),
    ('phppos_inventory_counts', 'inventory_counts'),
    ('phppos_inventory_counts_items', 'inventory_counts_items'),
    ('phppos_items_pricing_history', 'items_pricing_history'),
    ('phppos_expenses', 'expenses'),
    ('phppos_expenses_categories', 'expenses_categories'),
    ('phppos_price_rules', 'price_rules'),
    ('phppos_price_rules_items', 'price_rules_items'),
    ('phppos_price_rules_price_breaks', 'price_rules_price_breaks'),
    ('phppos_price_tiers', 'price_tiers'),
    ('phppos_item_variations', 'item_variations'),
    ('phppos_items_quantity_units', 'items_quantity_units'),
    ('phppos_location_items', 'location_items'),
    ('phppos_store_accounts', 'store_accounts'),
    ('phppos_sales_coupons', 'sales_coupons'),
    ('phppos_manufacturers', 'manufacturers'),
    ('phppos_additional_item_numbers', 'additional_item_numbers'),
    ('phppos_damaged_items_log', 'damaged_items_log'),
    ('phppos_items_tags', 'items_tags'),
    ('phppos_tags', 'tags'),
]

SQLITE_TYPES = {
    'int': 'INTEGER', 'tinyint': 'INTEGER', 'smallint': 'INTEGER',
    'mediumint': 'INTEGER', 'bigint': 'INTEGER', 'bit': 'INTEGER',
    'decimal': 'REAL', 'double': 'REAL', 'float': 'REAL', 'real': 'REAL',
    'varchar': 'TEXT', 'char': 'TEXT', 'text': 'TEXT', 'longtext': 'TEXT',
    'mediumtext': 'TEXT', 'tinytext': 'TEXT', 'json': 'TEXT', 'enum': 'TEXT',
    'timestamp': 'TEXT', 'datetime': 'TEXT', 'date': 'TEXT', 'time': 'TEXT',
    'year': 'INTEGER',
}

VIEWS = [
    ("""CREATE VIEW v_ventas_dia AS
        SELECT substr(sale_time,1,10) AS fecha,
               COUNT(*) AS tickets,
               ROUND(SUM(total),2) AS total,
               ROUND(SUM(subtotal),2) AS subtotal,
               ROUND(SUM(tax),2) AS iva,
               ROUND(SUM(total_quantity_purchased),2) AS articulos
        FROM sales WHERE deleted = 0 GROUP BY substr(sale_time,1,10)"""),
    ("""CREATE VIEW v_ventas_producto AS
        SELECT si.item_id, si.sku, si.description AS nombre,
               ROUND(SUM(si.qty),3) AS cantidad,
               ROUND(SUM(si.total),2) AS importe,
               ROUND(SUM(si.profit),2) AS utilidad
        FROM sale_items si JOIN sales s ON s.sale_id = si.sale_id
        WHERE s.deleted = 0 GROUP BY si.item_id, si.sku, si.description"""),
    ("""CREATE VIEW v_ventas_cajero AS
        SELECT s.employee_id, p.first_name || ' ' || p.last_name AS cajero,
               COUNT(*) AS tickets, ROUND(SUM(s.total),2) AS total
        FROM sales s LEFT JOIN people p ON p.person_id = s.employee_id
        WHERE s.deleted = 0 GROUP BY s.employee_id"""),
    ("""CREATE VIEW v_ventas_metodo_pago AS
        SELECT payment_type, COUNT(*) AS pagos, ROUND(SUM(amount),2) AS importe
        FROM payments GROUP BY payment_type"""),
    ("""CREATE VIEW v_margen_producto AS
        SELECT i.item_id, i.sku, i.name,
               ROUND(SUM(si.qty),3) AS unidades,
               ROUND(SUM(si.total),2) AS venta,
               ROUND(SUM(si.cost_price * si.qty),2) AS costo,
               ROUND(SUM(si.profit),2) AS utilidad,
               ROUND(100.0 * SUM(si.profit) / NULLIF(SUM(si.total),0), 1) AS margen_pct
        FROM sale_items si JOIN sales s ON s.sale_id = si.sale_id
        JOIN items i ON i.item_id = si.item_id
        WHERE s.deleted = 0 GROUP BY i.item_id, i.sku, i.name"""),
]

BATCH = 20000


def log(msg):
    print(msg, flush=True)


def conv(value):
    """MySQL -> SQLite: Decimal, fechas y binarios."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', 'replace')
    return value


def fetch(conn, sql, args=None):
    cursor = conn.cursor(pymysql.cursors.SSDictCursor)
    cursor.execute(sql, args or ())
    while True:
        rows = cursor.fetchmany(BATCH)
        if not rows:
            break
        yield rows
    cursor.close()


def build_sku_map(pos_db_path):
    """Mapea item_id de 1box -> SKU del POS (para poder consultar por SKU)."""
    if not os.path.exists(pos_db_path):
        return {}
    conn = sqlite3.connect(pos_db_path)
    try:
        rows = conn.execute(
            'SELECT legacy_item_id, sku FROM products WHERE legacy_item_id IS NOT NULL'
        ).fetchall()
    finally:
        conn.close()
    return {int(r[0]): r[1] for r in rows}


def create_schema(conn):
    conn.executescript("""
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

    CREATE TABLE sales (
        sale_id INTEGER PRIMARY KEY, sale_time TEXT, fecha TEXT, customer_id INTEGER,
        employee_id INTEGER, sold_by_employee_id INTEGER, comment TEXT, deleted INTEGER,
        suspended INTEGER, location_id INTEGER, register_id INTEGER, payment_type TEXT,
        cc_ref_no TEXT, subtotal REAL, tax REAL, total REAL, profit REAL,
        total_quantity_purchased REAL, non_taxable REAL, rule_discount REAL,
        return_sale_id INTEGER, tip REAL, is_ecommerce INTEGER, exchange_rate REAL,
        last_modified TEXT
    );

    CREATE TABLE sale_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sale_id INTEGER, item_id INTEGER,
        sku TEXT, description TEXT, qty REAL, cost_price REAL, unit_price REAL,
        regular_price REAL, discount_percent REAL, subtotal REAL, tax REAL, total REAL,
        profit REAL, unit_quantity REAL, damaged_qty REAL
    );

    CREATE TABLE payments (
        payment_id INTEGER PRIMARY KEY, sale_id INTEGER, payment_type TEXT, amount REAL,
        auth_code TEXT, ref_no TEXT, payment_date TEXT
    );

    CREATE TABLE people (
        person_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, full_name TEXT,
        phone_number TEXT, email TEXT, address_1 TEXT, city TEXT, state TEXT, zip TEXT
    );

    CREATE TABLE customers (
        id INTEGER PRIMARY KEY, person_id INTEGER, first_name TEXT, last_name TEXT,
        account_number TEXT, balance REAL, credit_limit REAL, points REAL,
        deleted INTEGER, tax_certificate TEXT, internal_notes TEXT
    );

    CREATE TABLE employees (
        id INTEGER PRIMARY KEY, person_id INTEGER, first_name TEXT, last_name TEXT,
        username TEXT, deleted INTEGER, inactive INTEGER, hire_date TEXT,
        termination_date TEXT, commission_percent REAL
    );

    CREATE TABLE suppliers (
        id INTEGER PRIMARY KEY, person_id INTEGER, first_name TEXT, last_name TEXT,
        company_name TEXT, account_number TEXT, balance REAL, deleted INTEGER
    );

    CREATE TABLE categories (
        id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER, deleted INTEGER,
        hide_from_grid INTEGER
    );

    CREATE TABLE items (
        item_id INTEGER PRIMARY KEY, sku TEXT, name TEXT, category_id INTEGER,
        supplier_id INTEGER, item_number TEXT, product_id TEXT, cost_price REAL,
        unit_price REAL, promo_price REAL, deleted INTEGER, item_inactive INTEGER,
        reorder_level REAL, tax_included INTEGER, last_modified TEXT
    );

    CREATE TABLE receivings (
        receiving_id INTEGER PRIMARY KEY, receiving_time TEXT, supplier_id INTEGER,
        employee_id INTEGER, comment TEXT, deleted INTEGER, location_id INTEGER,
        subtotal REAL, tax REAL, total REAL, payment_type TEXT
    );

    CREATE TABLE receiving_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, receiving_id INTEGER, item_id INTEGER,
        sku TEXT, description TEXT, qty REAL, cost_price REAL, unit_price REAL,
        subtotal REAL, tax REAL, total REAL
    );

    CREATE TABLE locations (
        location_id INTEGER PRIMARY KEY, name TEXT, company TEXT, address TEXT,
        phone TEXT, email TEXT
    );

    CREATE TABLE registers (
        register_id INTEGER PRIMARY KEY, location_id INTEGER, name TEXT, deleted INTEGER
    );
    """)


def migrate(source_sql, dest_path, sku_map, since=None):
    if os.path.exists(dest_path):
        os.remove(dest_path)
    for suffix in ('-wal', '-shm'):
        if os.path.exists(dest_path + suffix):
            os.remove(dest_path + suffix)

    conn = sqlite3.connect(dest_path)
    conn.execute('PRAGMA journal_mode = MEMORY')
    conn.execute('PRAGMA synchronous = OFF')
    create_schema(conn)

    my = pymysql.connect(host='127.0.0.1', user='root', password='',
                         database='1box', charset='utf8mb4')

    people = {}
    for rows in fetch(my, 'SELECT person_id, first_name, last_name, full_name, phone_number, '
                          'email, address_1, city, state, zip FROM phppos_people'):
        for row in rows:
            people[row['person_id']] = row
    log(f'  personas: {len(people):,}')

    counters = {}
    for table_src, table_dst, cols in SPECS:
        src_cols = [c[0] for c in cols]
        dest_cols = [c[1] for c in cols]
        where = ''
        args = ()
        if since and table_src in ('phppos_sales', 'phppos_receivings'):
            where = ' WHERE {} >= %s'.format('sale_time' if table_src == 'phppos_sales' else 'receiving_time')
            args = (since,)
        sql = 'SELECT {} FROM {}{}'.format(', '.join(src_cols), table_src, where)
        total = 0
        for rows in fetch(my, sql, args):
            payload = []
            for row in rows:
                values = [conv(row[c]) for c in src_cols]
                if table_dst == 'sales':
                    st = row.get('sale_time')
                    values.insert(2, st.strftime('%Y-%m-%d') if st else None)
                    dest_cols_use = dest_cols[:2] + ['fecha'] + dest_cols[2:]
                elif table_dst == 'sale_items':
                    values.insert(3, sku_map.get(row['item_id']))
                    dest_cols_use = dest_cols[:3] + ['sku'] + dest_cols[3:]
                elif table_dst == 'receiving_items':
                    values.insert(3, sku_map.get(row['item_id']))
                    dest_cols_use = dest_cols[:3] + ['sku'] + dest_cols[3:]
                elif table_dst == 'items':
                    values.insert(1, sku_map.get(row['item_id']))
                    dest_cols_use = dest_cols[:1] + ['sku'] + dest_cols[1:]
                elif table_dst in ('customers', 'employees', 'suppliers'):
                    person = people.get(row.get('person_id'))
                    values.insert(2, person['first_name'] if person else None)
                    values.insert(3, person['last_name'] if person else None)
                    dest_cols_use = dest_cols[:2] + ['first_name', 'last_name'] + dest_cols[2:]
                else:
                    dest_cols_use = dest_cols
                payload.append(tuple(values))
            conn.executemany(
                f"INSERT OR REPLACE INTO {table_dst} ({', '.join(dest_cols_use)}) "
                f"VALUES ({', '.join('?' * len(dest_cols_use))})", payload)
            total += len(payload)
        conn.commit()
        counters[table_dst] = total
        log(f'  {table_dst:<16} {total:>9,}')

    # gente (para join de nombres)
    conn.executemany(
        'INSERT OR REPLACE INTO people (person_id, first_name, last_name, full_name, '
        'phone_number, email, address_1, city, state, zip) VALUES (?,?,?,?,?,?,?,?,?,?)',
        [(p['person_id'], conv(p['first_name']), conv(p['last_name']), conv(p['full_name']),
          conv(p['phone_number']), conv(p['email']), conv(p['address_1']), conv(p['city']),
          conv(p['state']), conv(p['zip']))
         for p in people.values()])
    conn.commit()

    log('  índices...')
    for table, cols in INDEXES:
        names = ', '.join(cols)
        conn.execute(f'CREATE INDEX IF NOT EXISTS ix_{table}_{"_".join(cols)} '
                     f'ON {table} ({names})')
    conn.commit()

    log('  tablas de operación y configuración...')
    extra = migrar_extra(my, conn)

    log('  vistas...')
    for sql in VIEWS:
        conn.execute(sql)
    conn.commit()

    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.executemany('INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)', [
        ('origen', 'MySQL 1box (127.0.0.1) - solo lectura'),
        ('migrado_en', stamp),
        ('desde', since or 'histórico completo'),
        ('filas', json.dumps(counters)),
        ('tablas_extra', str(extra)),
    ])
    conn.commit()
    conn.execute('ANALYZE')
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    conn.execute('VACUUM')
    conn.close()
    my.close()
    return counters


def migrar_extra(my, conn):
    """Copia tablas adicionales de 1box con el mismo esquema (tipos mapeados)."""
    total_tablas = 0
    for src, dst in EXTRA_TABLES:
        cur = my.cursor()
        cur.execute(
            """SELECT COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA='1box' AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION""",
            (src,))
        cols = cur.fetchall()
        cur.close()
        if not cols:
            continue
        definiciones = []
        for nombre, tipo, key in cols:
            tipo_sql = SQLITE_TYPES.get(str(tipo).lower(), 'TEXT')
            # Sin PRIMARY KEY: algunas tablas de 1box tienen claves de texto
            # mixtas y SQLite las rechazaría como INTEGER PRIMARY KEY.
            definiciones.append(f'"{nombre}" {tipo_sql}')
        conn.execute(f'DROP TABLE IF EXISTS "{dst}"')
        conn.execute(f'CREATE TABLE "{dst}" ({", ".join(definiciones)})')
        nombres = [c[0] for c in cols]
        # OJO: en MySQL las comillas dobles son literales de texto, no
        # identificadores: hay que usar acentos graves para el SELECT.
        quoted = ', '.join(f'`{n}`' for n in nombres)
        quoted_sqlite = ', '.join(f'"{n}"' for n in nombres)
        marcas = ', '.join('?' * len(nombres))
        insert = f'INSERT OR REPLACE INTO "{dst}" ({quoted_sqlite}) VALUES ({marcas})'
        total = 0
        for rows in fetch(my, f'SELECT {quoted} FROM `{src}`'):
            payload = [tuple(conv(r[n]) for n in nombres) for r in rows]
            conn.executemany(insert, payload)
            total += len(payload)
        conn.commit()
        total_tablas += 1
        log(f'  {dst:<26} {total:>9,}')
    return total_tablas


def main():
    parser = argparse.ArgumentParser(description='Migra el histórico de 1box a SQLite.')
    parser.add_argument('--destino', default=DEFAULT_DEST)
    parser.add_argument('--pos-db', default=DEFAULT_SOURCE,
                        help='Base del POS (para mapear item_id -> SKU)')
    parser.add_argument('--desde', default=None, help='Filtro de fecha (AAAA-MM-DD)')
    args = parser.parse_args()

    print('=' * 64)
    print('  MIGRACION DEL HISTORICO DE 1box (SOLO LECTURA)')
    print('=' * 64)
    print(f'  Destino : {args.destino}')
    print(f'  Desde   : {args.desde or "todo el histórico"}')
    sku_map = build_sku_map(args.pos_db)
    print(f'  SKUs del POS mapeados: {len(sku_map):,}')
    inicio = time.time()
    counters = migrate(None, args.destino, sku_map, args.desde)
    size = os.path.getsize(args.destino) / (1024 * 1024)
    print('-' * 64)
    print(f'  Total   : {sum(counters.values()):,} registros en {time.time() - inicio:.1f} s')
    print(f'  Tamaño  : {size:.1f} MB')
    print('  Listo. Esta base es de SOLO CONSULTA (reportes).')
    print('=' * 64)
    return 0


if __name__ == '__main__':
    sys.exit(main())
