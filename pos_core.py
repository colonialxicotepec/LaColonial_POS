"""
Núcleo del punto de venta: configuración, base local, usuarios, turnos,
ventas, pagos, movimientos de efectivo y respaldos.

La base del POS (pos.db) es independiente del catálogo
(Historico/Auxiliares/inventario_moderno.db).
El catálogo se lee; aquí solo se escriben operaciones de caja.
"""

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POS_DB = os.path.join(BASE_DIR, 'pos.db')
CONFIG_PATH = os.path.join(BASE_DIR, 'pos_config.json')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
TICKET_DIR = os.path.join(BASE_DIR, 'tickets')
EXPORT_DIR = os.path.join(BASE_DIR, 'exportes')

SCHEMA_VERSION = 10

DEFAULT_UNITS = (
    ('Pieza', 'pza'),
    ('Kilogramo', 'kg'),
    ('Paquete', 'paq'),
    ('Caja', 'cja'),
)
DEFAULT_UNIT = 'Pieza'
DEFAULT_UNIT_SYMBOL = 'pza'

DEFAULT_CATEGORY = 'Papelería'
DEFAULT_CATEGORIES = (
    'Papelería',
)

DEFAULT_LOCATIONS = (
    'fila 1 sección 1',
    'fila 1 sección 2',
    'fila 2 sección 1',
    'fila 2 sección 2',
    'fila 3 sección 1',
    'fila 3 sección 2',
    'fila 4 sección 1',
    'fila 4 sección 2',
    'fila 5 sección 1',
    'fila 5 sección 2',
)
DEFAULT_LOCATION = 'fila 1 sección 1'

DEFAULT_REASON = 'Error de Conteo'
DEFAULT_REASONS = (
    'Error de Conteo',
    'Compra',
    'Caducidad',
)

DEFAULT_CONFIG = {
    'store': {
        'name': 'La Colonial',
        'address': '',
        'phone': '',
        'rfc': '',
        'footer': '¡Gracias por su compra!',
        'show_address': True,
    },
    'printer': {
        'name': '',
        'width': 42,
        'open_drawer': True,
        'copies': 1,
        'print_sale': True,
        'print_shift': True,
        # 'raster' = TOTAL dibujado como imagen de puntos (trazo N veces más
        # grueso); 'escpos' = texto con doble tamaño (si la impresora no
        # acepta el comando de imagen).
        'total_style': 'raster',
    },
    'rules': {
        'blocked_names': ['descuento', 'abono a cuenta'],
        'blocked_skus': [],
        'min_price': 0.01,
        'allow_zero_stock': True,
        'max_cash_input': 50000,
        'max_qty_per_line': 999,
        'allow_emergency_prices': True,
        'override_hours': 24,
        # Efectivo máximo que conviene dejar en cajón para el día siguiente.
        # El corte sugiere retirar el excedente en billetes.
        'fondo_maximo': 1400,
    },
    'security': {
        'session_hours': 12,
        'manager_required_for_cancel': True,
        'manager_required_for_return': True,
        'max_users': 8,
        'min_pin_length': 4,
    },
    'network': {
        'allow_lan': False,
        'allowed_ips': [],
    },
    'backup': {
        'auto_minutes': 60,
        'keep_auto': 72,
    },
}


# ---------------------------------------------------------------- utilidades

def money(value):
    """Redondea a centavos (evita errores de punto flotante)."""
    return float(Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def now_text():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today_text():
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------- configuración

def _merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='utf-8-sig') as fh:
                cfg = _merge(cfg, json.load(fh))
        except (OSError, ValueError):
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    return cfg


def _ticket_secret():
    cfg = load_config()
    secret = (cfg.get('security', {}) or {}).get('ticket_secret')
    if not secret:
        secret = ensure_config().get('security', {}).get('ticket_secret', '')
    if not secret:
        raise ValueError('No se pudo preparar el secreto de los tickets.')
    return secret


def ticket_token(sale_id):
    """Token firmado para ver el ticket de una venta sin exponer la sesión."""
    message = f'pos-ticket:{int(sale_id)}'.encode('utf-8')
    return hmac.new(_ticket_secret().encode('utf-8'), message, hashlib.sha256).hexdigest()[:32]


def verify_ticket_token(sale_id, token):
    expected = ticket_token(sale_id)
    return bool(token) and hmac.compare_digest(expected, str(token))


def ensure_config():
    """Crea pos_config.json la primera vez y garantiza el secreto de tickets."""
    cfg = load_config()
    security = cfg.setdefault('security', {})
    if not security.get('ticket_secret'):
        security['ticket_secret'] = secrets.token_hex(32)
        save_config(cfg)
    return cfg


# ---------------------------------------------------------------- base de datos

def connect():
    conn = sqlite3.connect(POS_DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def row_to_dict(row):
    return dict(row) if row is not None else None


def _table_columns(conn, table):
    return {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}


def _add_column(conn, table, column, definition):
    if column not in _table_columns(conn, table):
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def _seed_units(conn):
    stamp = now_text()
    for name, symbol in DEFAULT_UNITS:
        conn.execute(
            'INSERT OR IGNORE INTO units(name, symbol, active, created_at) VALUES(?,?,1,?)',
            (name, symbol, stamp),
        )


def _seed_categories(conn):
    stamp = now_text()
    for name in DEFAULT_CATEGORIES:
        conn.execute(
            'INSERT OR IGNORE INTO categories(name, active, created_at) VALUES(?,1,?)',
            (name, stamp),
        )
    # También incorporamos las categorías ya presentes en el inventario.
    rows = conn.execute(
        "SELECT DISTINCT TRIM(category) AS c FROM products "
        "WHERE category IS NOT NULL AND TRIM(category)<>'' ORDER BY c COLLATE NOCASE"
    ).fetchall()
    for r in rows:
        name = (r['c'] or '').strip()
        if name:
            conn.execute(
                'INSERT OR IGNORE INTO categories(name, active, created_at) VALUES(?,1,?)',
                (name, stamp),
            )


def _seed_locations(conn):
    stamp = now_text()
    for name in DEFAULT_LOCATIONS:
        conn.execute(
            'INSERT OR IGNORE INTO locations(name, active, created_at) VALUES(?,1,?)',
            (name, stamp),
        )


def _seed_reasons(conn):
    stamp = now_text()
    for name in DEFAULT_REASONS:
        conn.execute(
            'INSERT OR IGNORE INTO reasons(name, active, created_at) VALUES(?,1,?)',
            (name, stamp),
        )


def _migrate_schema(conn):
    """Lleva una pos.db existente al esquema actual sin perder datos."""
    _add_column(conn, 'products', 'unit', "TEXT NOT NULL DEFAULT 'Pieza'")
    _add_column(conn, 'products', 'unit_symbol', "TEXT NOT NULL DEFAULT 'pza'")
    _add_column(conn, 'sale_items', 'unit', 'TEXT')
    _add_column(conn, 'return_items', 'unit', 'TEXT')
    # Esquema 3: empaques / presentaciones. Un producto puede ser empaque de
    # otro (base): 1 empaque = base_qty unidades del base y el stock que se
    # descuenta es el del base. sale_items guarda a quién se movió el stock
    # y con qué factor para que devoluciones y cancelaciones reviertan igual.
    _add_column(conn, 'products', 'base_product_id', 'INTEGER REFERENCES products(id)')
    _add_column(conn, 'products', 'base_qty', 'REAL NOT NULL DEFAULT 1')
    _add_column(conn, 'sale_items', 'stock_product_id', 'INTEGER')
    _add_column(conn, 'sale_items', 'stock_factor', 'REAL NOT NULL DEFAULT 1')
    # Esquema 6: la devolución guarda a quién movió el stock y con qué factor.
    # Sin esto, anular la devolución de un empaque descontaba la cantidad
    # equivocada (el empaque en vez de las piezas del producto base).
    _add_column(conn, 'return_items', 'stock_product_id', 'INTEGER')
    _add_column(conn, 'return_items', 'stock_factor', 'REAL NOT NULL DEFAULT 1')
    conn.execute(
        """UPDATE return_items
              SET stock_product_id = (
                    SELECT si.stock_product_id FROM sale_items si
                     WHERE si.id = return_items.sale_item_id),
                  stock_factor = COALESCE((
                    SELECT si.stock_factor FROM sale_items si
                     WHERE si.id = return_items.sale_item_id), 1)
            WHERE stock_product_id IS NULL"""
    )

    conn.execute(
        "UPDATE products SET unit=?, unit_symbol=? "
        "WHERE unit IS NULL OR TRIM(unit)='' OR unit_symbol IS NULL OR TRIM(unit_symbol)=''",
        (DEFAULT_UNIT, DEFAULT_UNIT_SYMBOL),
    )
    _seed_units(conn)
    _seed_categories(conn)
    _seed_locations(conn)
    _seed_reasons(conn)

    # Esquema 7: soporte para ventas despachadas ('pending') pendientes de cobro en Caja,
    # con trazabilidad de quién y cuándo las cobró (paid_at, paid_by).
    sales_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sales'"
    ).fetchone()
    sales_sql = (sales_sql_row['sql'] or '') if sales_sql_row else ''
    if "'pending'" not in sales_sql:
        conn.commit()
        conn.execute('PRAGMA foreign_keys = OFF')
        try:
            conn.executescript("""
            BEGIN;
            DROP TABLE IF EXISTS sales_new;
            CREATE TABLE sales_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folio TEXT NOT NULL UNIQUE,
                shift_id INTEGER NOT NULL REFERENCES shifts(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                subtotal REAL NOT NULL,
                discount REAL NOT NULL DEFAULT 0,
                total REAL NOT NULL,
                paid_total REAL NOT NULL,
                change_given REAL NOT NULL DEFAULT 0,
                item_count REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('pending', 'completed', 'cancelled')),
                paid_at TEXT,
                paid_by INTEGER,
                cancelled_at TEXT,
                cancelled_by INTEGER,
                cancel_reason TEXT,
                customer_name TEXT,
                note TEXT
            );
            INSERT INTO sales_new(
                id, folio, shift_id, user_id, created_at, subtotal, discount, total,
                paid_total, change_given, item_count, status, paid_at, paid_by,
                cancelled_at, cancelled_by, cancel_reason, customer_name, note
            )
            SELECT
                id, folio, shift_id, user_id, created_at, subtotal, discount, total,
                paid_total, change_given, item_count, status, created_at, user_id,
                cancelled_at, cancelled_by, cancel_reason, customer_name, note
            FROM sales;
            DROP TABLE sales;
            ALTER TABLE sales_new RENAME TO sales;
            CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at);
            CREATE INDEX IF NOT EXISTS idx_sales_shift ON sales(shift_id);
            CREATE INDEX IF NOT EXISTS idx_sales_status ON sales(status);
            COMMIT;
            """)
        finally:
            conn.execute('PRAGMA foreign_keys = ON')
    else:
        _add_column(conn, 'sales', 'paid_at', 'TEXT')
        _add_column(conn, 'sales', 'paid_by', 'INTEGER')

    # Clientes importados del histórico y su relación con ventas. Se agregan
    # como tablas nuevas para no alterar `sales` ni bloquear flujos existentes.
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
    _add_column(conn, 'customers', 'es_publico', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'sale_customers', 'customer_rfc', 'TEXT')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_nombre ON customers(nombre)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_rfc ON customers(rfc)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_telefono ON customers(telefono)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sale_customers_customer ON sale_customers(customer_id)")
    conn.execute(
        """INSERT OR IGNORE INTO customers(
               legacy_id, nombre, es_publico, activo, origen
           ) VALUES(1, 'PÚBLICO EN GENERAL', 1, 1, 'historico_1box')"""
    )


def init_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.makedirs(TICKET_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    conn = connect()
    try:
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA synchronous = FULL')
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('gerente', 'administrador', 'cajero')),
            pin_salt TEXT NOT NULL,
            pin_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            opening_cash REAL NOT NULL DEFAULT 0,
            counted_cash REAL,
            expected_cash REAL,
            difference REAL,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folio TEXT NOT NULL UNIQUE,
            shift_id INTEGER NOT NULL REFERENCES shifts(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            subtotal REAL NOT NULL,
            discount REAL NOT NULL DEFAULT 0,
            total REAL NOT NULL,
            paid_total REAL NOT NULL,
            change_given REAL NOT NULL DEFAULT 0,
            item_count REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('pending', 'completed', 'cancelled')),
            paid_at TEXT,
            paid_by INTEGER,
            cancelled_at TEXT,
            cancelled_by INTEGER,
            cancel_reason TEXT,
            customer_name TEXT,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
            legacy_item_id INTEGER,
            product_key TEXT,
            sku TEXT,
            barcode TEXT,
            name TEXT NOT NULL,
            unit TEXT,
            unit_price REAL NOT NULL,
            cost_price REAL NOT NULL DEFAULT 0,
            qty REAL NOT NULL,
            line_total REAL NOT NULL,
            stock_product_id INTEGER,
            stock_factor REAL NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
            method TEXT NOT NULL CHECK (method IN ('efectivo', 'tarjeta', 'transferencia')),
            amount REAL NOT NULL,
            reference TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cash_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id INTEGER NOT NULL REFERENCES shifts(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            amount REAL NOT NULL,
            reason TEXT,
            sale_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS shift_denoms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id INTEGER NOT NULL REFERENCES shifts(id),
            value REAL NOT NULL,
            qty INTEGER NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL,
            user_id INTEGER,
            action TEXT NOT NULL,
            detail TEXT
        );

        CREATE TABLE IF NOT EXISTS returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folio TEXT NOT NULL UNIQUE,
            sale_id INTEGER NOT NULL REFERENCES sales(id),
            shift_id INTEGER NOT NULL REFERENCES shifts(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            subtotal REAL NOT NULL,
            total REAL NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('completed', 'cancelled')),
            reason TEXT,
            note TEXT,
            cancelled_at TEXT,
            cancelled_by INTEGER,
            cancel_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS return_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id INTEGER NOT NULL REFERENCES returns(id) ON DELETE CASCADE,
            sale_item_id INTEGER NOT NULL REFERENCES sale_items(id),
            legacy_item_id INTEGER,
            product_key TEXT,
            sku TEXT,
            name TEXT NOT NULL,
            unit TEXT,
            unit_price REAL NOT NULL,
            qty REAL NOT NULL,
            line_total REAL NOT NULL,
            stock_product_id INTEGER,
            stock_factor REAL NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL UNIQUE REFERENCES products(id),
            kind TEXT NOT NULL DEFAULT 'stock_negativo',
            stock REAL NOT NULL,
            message TEXT,
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS price_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_key TEXT NOT NULL,
            original_price REAL NOT NULL,
            price REAL NOT NULL,
            reason TEXT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            symbol TEXT NOT NULL COLLATE NOCASE,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_item_id INTEGER,
            sku TEXT NOT NULL UNIQUE,
            barcode TEXT,
            item_number TEXT,
            alt_codes TEXT,
            name TEXT NOT NULL,
            category TEXT,
            location TEXT,
            unit TEXT NOT NULL DEFAULT 'Pieza',
            unit_symbol TEXT NOT NULL DEFAULT 'pza',
            cost_price REAL NOT NULL DEFAULT 0,
            sale_price REAL NOT NULL DEFAULT 0,
            stock REAL NOT NULL DEFAULT 0,
            units_sold_period REAL NOT NULL DEFAULT 0,
            last_sold_at TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT '1box',
            base_product_id INTEGER REFERENCES products(id),
            base_qty REAL NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            updated_by INTEGER
        );

        CREATE TABLE IF NOT EXISTS stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            user_id INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            qty REAL NOT NULL,
            stock_before REAL NOT NULL,
            stock_after REAL NOT NULL,
            reason TEXT,
            ref TEXT,
            sale_id INTEGER,
            return_id INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode);
        CREATE INDEX IF NOT EXISTS idx_products_active ON products(active);
        CREATE INDEX IF NOT EXISTS idx_products_legacy ON products(legacy_item_id);
        CREATE INDEX IF NOT EXISTS idx_movements_product ON stock_movements(product_id);
        CREATE INDEX IF NOT EXISTS idx_movements_created ON stock_movements(created_at);

        CREATE INDEX IF NOT EXISTS idx_returns_created ON returns(created_at);
        CREATE INDEX IF NOT EXISTS idx_returns_sale ON returns(sale_id);
        CREATE INDEX IF NOT EXISTS idx_return_items_return ON return_items(return_id);
        CREATE INDEX IF NOT EXISTS idx_return_items_sale_item ON return_items(sale_item_id);
        CREATE INDEX IF NOT EXISTS idx_overrides_item ON price_overrides(product_key);
        CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at);
        CREATE INDEX IF NOT EXISTS idx_sales_shift ON sales(shift_id);
        CREATE INDEX IF NOT EXISTS idx_sales_status ON sales(status);
        CREATE INDEX IF NOT EXISTS idx_items_sale ON sale_items(sale_id);
        CREATE INDEX IF NOT EXISTS idx_items_legacy ON sale_items(legacy_item_id);
        CREATE INDEX IF NOT EXISTS idx_payments_sale ON payments(sale_id);
        CREATE INDEX IF NOT EXISTS idx_movements_shift ON cash_movements(shift_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        """)
        _migrate_schema(conn)
        conn.execute(
            'INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            ('schema_version', str(SCHEMA_VERSION)),
        )
        conn.commit()
    finally:
        conn.close()


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
        keep = int(config.get('backup', {}).get('keep_auto', 72) or 72)
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
    if tag in ('auto', 'arranque'):
        _prune_backups(keep, tag)
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


def _prune_backups(keep, tag):
    """Conserva los respaldos más recientes de una etiqueta automática."""
    suffix = f'_{tag}.db'
    files = sorted(
        (os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
         if f.startswith('pos_') and f.endswith(suffix)),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
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
