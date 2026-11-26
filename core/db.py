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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
        'intervalo_horas': 4,
        'keep_auto': 168,
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


