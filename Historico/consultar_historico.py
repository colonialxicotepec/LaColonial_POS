"""
Consulta del inventario histórico de 1box (solo lectura).

Sirve una interfaz local para buscar productos del sistema anterior
(`historico_1box.db`) por código de barras, SKU, código adicional o nombre.

- Sólo escucha en 127.0.0.1 (no se publica en la red).
- Abre la base en modo `mode=ro` + `query_only`: es imposible modificarla.
- La base es un archivo histórico: no se sincroniza ni se actualiza.

Uso:
    python consultar_historico.py
    python consultar_historico.py --puerto 8095 --no-browser
"""

import json
import os
import socket
import sqlite3
import sys
import threading
import time
import unicodedata
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

APP_VERSION = '1.0'

HIST_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(HIST_DIR)
DB_PATH = os.path.join(HIST_DIR, 'historico_1box.db')
if not os.path.exists(DB_PATH):
    DB_PATH = os.path.join(BASE_DIR, 'historico_1box.db')
UI_DIR = os.path.join(HIST_DIR, 'ui')
CONFIG_PATH = os.path.join(HIST_DIR, 'historico_config.json')

HOST = '127.0.0.1'          # 0.0.0.0 = también en la red local
PORT_BASE = 8095
MAX_RESULTS = 60
MAX_DETAIL_ROWS = 8
ALLOWED_IPS = []            # vacío = cualquiera de la red local (si está publicada)

DEFAULT_CONFIG = {
    'puerto': PORT_BASE,
    'publicar_en_red': False,
    'ips_permitidas': [],
}


# --------------------------------------------------------------------------
# utilidades de texto
# --------------------------------------------------------------------------
def strip_accents(text):
    return ''.join(
        c for c in unicodedata.normalize('NFKD', text)
        if not unicodedata.combining(c)
    )


def norm(text):
    """Minúsculas, sin acentos y con espacios simples (para buscar)."""
    if not text:
        return ''
    return ' '.join(strip_accents(str(text).lower()).split())


def clean_code(value):
    return (value or '').strip().upper()


def money(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def qty(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def clean_text(value, limit=120):
    """Quita saltos de línea y etiquetas que 1box guardó como texto plano."""
    text = str(value or '').replace('<br />', ' ').replace('<br>', ' ')
    text = ' '.join(text.split())
    return text[:limit]


def load_config():
    """Lee Historico\\historico_config.json (puerto y publicación en red)."""
    cfg = dict(DEFAULT_CONFIG)
    cfg['ips_permitidas'] = list(DEFAULT_CONFIG['ips_permitidas'])
    if not os.path.exists(CONFIG_PATH):
        return cfg, None
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return cfg, (f'No se pudo leer {os.path.basename(CONFIG_PATH)} '
                     f'({exc}); se usan valores por omisión.')
    if not isinstance(data, dict):
        return cfg, f'{os.path.basename(CONFIG_PATH)} no tiene el formato esperado.'

    puerto = data.get('puerto')
    if isinstance(puerto, int) and 1 <= puerto <= 65535:
        cfg['puerto'] = puerto
    elif puerto is not None:
        return cfg, 'El puerto configurado no es válido; se usa 8095.'

    cfg['publicar_en_red'] = bool(data.get('publicar_en_red', False))

    ips = data.get('ips_permitidas')
    if isinstance(ips, list):
        cfg['ips_permitidas'] = [str(ip).strip() for ip in ips if str(ip).strip()]

    return cfg, None


# --------------------------------------------------------------------------
# índice del histórico
# --------------------------------------------------------------------------
class HistoricoCatalog:
    """Carga `historico_1box.db` en memoria una sola vez."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.error = None
        self.loading = True
        self.loaded_at = None
        self.db_updated = None
        self.products = []
        self.by_id = {}
        self.by_code = {}
        self.code_all = {}
        self.last_sale = None
        # Terceros (clientes, proveedores, fabricantes y personas).
        self.third_parties = []
        self.third_by_key = {}
        self._lock = threading.Lock()

    # ---------- conexión ----------
    def connect(self):
        path = self.db_path.replace('\\', '/')
        uri = 'file:' + quote(path, safe='/:') + '?mode=ro'
        con = sqlite3.connect(uri, uri=True, timeout=10)
        con.execute('PRAGMA query_only = ON')
        con.row_factory = sqlite3.Row
        return con

    # ---------- carga ----------
    def load(self):
        with self._lock:
            try:
                self._load()
                self.error = None
            except sqlite3.Error as exc:
                self.error = str(exc)
                self.products = []
            except OSError as exc:
                self.error = f'No se pudo leer la base: {exc}'
                self.products = []
            finally:
                self.loading = False

    def _load(self):
        con = self.connect()
        try:
            stats = self._load_stats(con)
            extra_codes = self._load_extra_codes(con)
            suppliers = self._load_suppliers(con)

            sql = """
                SELECT i.item_id, i.sku, i.name, i.item_number,
                       i.cost_price, i.unit_price, i.promo_price,
                       i.deleted, i.item_inactive, i.last_modified,
                       c.name AS category,
                       li.location, li.quantity
                FROM items i
                LEFT JOIN categories c ON c.id = i.category_id
                LEFT JOIN location_items li
                       ON li.item_id = i.item_id AND li.location_id = 1
                ORDER BY i.name
            """
            rows = con.execute(sql).fetchall()
            # Los terceros se cargan con la misma conexión, antes de cerrarla.
            self._load_third_parties(con)
        finally:
            con.close()

        products = []
        by_id = {}
        by_code = {}
        code_all = {}

        for row in rows:
            item_id = row['item_id']
            sold = stats.get(item_id, {})
            extras = extra_codes.get(item_id, [])

            item = {
                'id': item_id,
                'sku': (row['sku'] or '').strip(),
                'barcode': (row['item_number'] or '').strip(),
                'alt_codes': [c['code'] for c in extras if c['code']],
                'variations': [c for c in extras if c['kind'] == 'variacion'],
                'name': row['name'] or '',
                'category': row['category'] or '',
                'supplier': suppliers.get(item_id, ''),
                'location': (row['location'] or '').strip(),
                'stock': qty(row['quantity']),
                'cost': money(row['cost_price']),
                'price': money(row['unit_price']),
                'promo': money(row['promo_price']) or None,
                'deleted': bool(row['deleted']),
                'inactive': bool(row['item_inactive']),
                'last_modified': row['last_modified'] or '',
                'sold': qty(sold.get('units')),
                'sales_total': money(sold.get('total')),
                'last_sold': sold.get('last_sold') or '',
                'price_changed': stats.get(item_id, {}).get('price_changed') or '',
                'last_move': stats.get(item_id, {}).get('last_move'),
            }
            item['_search_name'] = norm(item['name'])
            item['_search_cat'] = norm(item['category'])
            item['_search_codes'] = norm(' '.join(
                [item['barcode'], item['sku'], item['category']]
                + item['alt_codes']
            ))

            products.append(item)
            by_id[item_id] = item

            for code in [item['barcode'], item['sku']] + item['alt_codes']:
                code = clean_code(code)
                if not code:
                    continue
                bucket = code_all.setdefault(code, [])
                if not any(existing is item for existing in bucket):
                    bucket.append(item)

        # Prioridad: código de barras > SKU > códigos adicionales/variaciones.
        for item in products:
            for code in (item['barcode'], item['sku']):
                if code:
                    by_code.setdefault(clean_code(code), item)
        for item in products:
            for code in item['alt_codes']:
                if code:
                    by_code.setdefault(clean_code(code), item)

        self.products = products
        self.by_id = by_id
        self.by_code = by_code
        self.code_all = code_all
        self.loaded_at = time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            self.db_updated = time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(self.db_path))
            )
        except OSError:
            self.db_updated = ''

    def _load_stats(self, con):
        """Ventas por producto, último cambio de precio y último movimiento."""
        stats = {}

        # Ventas (sin tickets cancelados).
        for row in con.execute("""
            SELECT si.item_id AS item_id,
                   SUM(si.qty) AS units,
                   SUM(si.total) AS total,
                   MAX(s.sale_time) AS last_sold
            FROM sale_items si
            JOIN sales s ON s.sale_id = si.sale_id
            WHERE s.deleted = 0
            GROUP BY si.item_id
        """):
            stats[row['item_id']] = {
                'units': row['units'],
                'total': row['total'],
                'last_sold': row['last_sold'],
            }

        self.last_sale = con.execute(
            'SELECT MAX(sale_time) FROM sales WHERE deleted = 0'
        ).fetchone()[0]

        for row in con.execute("""
            SELECT item_id, MAX(on_date) AS last_change
            FROM items_pricing_history
            GROUP BY item_id
        """):
            stats.setdefault(row['item_id'], {})['price_changed'] = row['last_change']

        for row in con.execute("""
            SELECT l.trans_items AS item_id,
                   l.trans_date AS moved_at,
                   l.trans_comment AS comment,
                   l.trans_inventory AS change,
                   l.trans_current_quantity AS current
            FROM inventory_log l
            JOIN (
                SELECT trans_items, MAX(trans_id) AS last_id
                FROM inventory_log
                GROUP BY trans_items
            ) m ON m.last_id = l.trans_id
        """):
            stats.setdefault(row['item_id'], {})['last_move'] = {
                'at': row['moved_at'],
                'comment': row['comment'] or '',
                'change': qty(row['change']),
                'current': qty(row['current']),
            }

        return stats

    def _load_extra_codes(self, con):
        extras = {}

        for row in con.execute("""
            SELECT item_id, item_number
            FROM additional_item_numbers
            WHERE item_number IS NOT NULL AND TRIM(item_number) <> ''
        """):
            extras.setdefault(row['item_id'], []).append({
                'code': row['item_number'].strip(),
                'kind': 'adicional',
                'name': '',
                'price': None,
            })

        for row in con.execute("""
            SELECT item_id, item_number, name, unit_price
            FROM item_variations
            WHERE item_number IS NOT NULL AND TRIM(item_number) <> ''
        """):
            extras.setdefault(row['item_id'], []).append({
                'code': row['item_number'].strip(),
                'kind': 'variacion',
                'name': row['name'] or '',
                'price': money(row['unit_price']) or None,
            })

        return extras

    def _load_third_parties(self, con):
        """Clientes, proveedores, fabricantes y personas (para la pestaña de terceros)."""
        # --- agregados ---
        sales_agg = {}
        for row in con.execute("""
            SELECT customer_id, COUNT(*) AS n, SUM(total) AS total, MAX(sale_time) AS last
            FROM sales
            WHERE customer_id IS NOT NULL AND deleted = 0
            GROUP BY customer_id
        """):
            sales_agg[row['customer_id']] = {
                'n': row['n'], 'total': money(row['total']), 'last': row['last'] or '',
            }

        recv_agg = {}
        for row in con.execute("""
            SELECT supplier_id, COUNT(*) AS n, SUM(total) AS total, MAX(receiving_time) AS last
            FROM receivings
            WHERE supplier_id IS NOT NULL AND deleted = 0
            GROUP BY supplier_id
        """):
            recv_agg[row['supplier_id']] = {
                'n': row['n'], 'total': money(row['total']), 'last': row['last'] or '',
            }

        account_agg = {}
        for row in con.execute("""
            SELECT customer_id, COUNT(*) AS n, MAX(date) AS last
            FROM store_accounts
            GROUP BY customer_id
        """):
            account_agg[row['customer_id']] = {'n': row['n'], 'last': row['last'] or ''}

        items_by_supplier = {}
        for row in con.execute("""
            SELECT supplier_id, COUNT(*) AS n FROM items
            WHERE supplier_id IS NOT NULL GROUP BY supplier_id
        """):
            items_by_supplier[row['supplier_id']] = row['n']

        items_by_manufacturer = {}
        # Nota: en 1box la tabla `items` no tiene columna de fabricante, así que
        # las marcas no se pueden ligar a artículos; se listan como catálogo.

        # --- clientes ---
        thirds = []
        for row in con.execute("""
            SELECT c.id AS id, c.person_id AS person_id,
                   c.first_name AS first_name, c.last_name AS last_name,
                   c.account_number AS account_number, c.balance AS balance,
                   c.credit_limit AS credit_limit, c.points AS points,
                   c.deleted AS deleted, c.tax_certificate AS tax_certificate,
                   p.full_name AS full_name, p.phone_number AS phone_number,
                   p.email AS email, p.address_1 AS address_1, p.city AS city,
                   p.state AS state, p.zip AS zip
            FROM customers c
            LEFT JOIN people p ON p.person_id = c.person_id
        """):
            agg = sales_agg.get(row['id'], {})
            acct = account_agg.get(row['id'], {})
            thirds.append({
                'key': f'cliente:{row["id"]}',
                'tipo': 'cliente',
                'id': row['id'],
                'person_id': row['person_id'],
                'nombre': clean_text(row['first_name'], 120) or clean_text(row['full_name'], 120),
                'apellido': clean_text(row['last_name'], 60),
                'razon_social': '',
                'contacto': clean_text(row['phone_number'], 40),
                'email': clean_text(row['email'], 80),
                'direccion': self._address(row['address_1'], row['city'], row['state'], row['zip']),
                'cuenta': clean_text(row['account_number'], 40),
                'saldo': money(row['balance']),
                'limite': money(row['credit_limit']),
                'puntos': qty(row['points']),
                'rfc': clean_text(row['tax_certificate'], 30),
                'eliminado': bool(row['deleted']),
                'operaciones': agg.get('n', 0),
                'importe': agg.get('total', 0.0),
                'ultima': agg.get('last', ''),
                'extra': f'{acct.get("n", 0)} movimiento(s) en cuenta corriente',
                '_busqueda': norm(' '.join([
                    row['first_name'] or '', row['last_name'] or '',
                    row['full_name'] or '', row['phone_number'] or '',
                    row['email'] or '', row['account_number'] or '',
                    row['tax_certificate'] or '',
                ])),
            })

        # --- proveedores ---
        for row in con.execute("""
            SELECT s.id AS id, s.person_id AS person_id,
                   s.first_name AS first_name, s.last_name AS last_name,
                   s.company_name AS company_name, s.account_number AS account_number,
                   s.balance AS balance, s.deleted AS deleted,
                   p.full_name AS full_name, p.phone_number AS phone_number,
                   p.email AS email, p.address_1 AS address_1, p.city AS city,
                   p.state AS state, p.zip AS zip
            FROM suppliers s
            LEFT JOIN people p ON p.person_id = s.person_id
        """):
            agg = recv_agg.get(row['id'], {})
            nombre = (row['company_name'] or '').strip() or (
                ' '.join(filter(None, [(row['first_name'] or '').strip(),
                                       (row['last_name'] or '').strip()]))
            ) or (row['full_name'] or '').strip()
            thirds.append({
                'key': f'proveedor:{row["id"]}',
                'tipo': 'proveedor',
                'id': row['id'],
                'person_id': row['person_id'],
                'nombre': clean_text(nombre, 120),
                'apellido': '',
                'razon_social': clean_text(row['company_name'], 120),
                'contacto': clean_text(row['phone_number'], 40),
                'email': clean_text(row['email'], 80),
                'direccion': self._address(row['address_1'], row['city'], row['state'], row['zip']),
                'cuenta': clean_text(row['account_number'], 40),
                'saldo': money(row['balance']),
                'limite': 0.0,
                'puntos': 0.0,
                'rfc': '',
                'eliminado': bool(row['deleted']),
                'operaciones': agg.get('n', 0),
                'importe': agg.get('total', 0.0),
                'ultima': agg.get('last', ''),
                'extra': f'{items_by_supplier.get(row["id"], 0)} artículo(s) surtidos',
                '_busqueda': norm(' '.join([
                    row['company_name'] or '', row['first_name'] or '',
                    row['last_name'] or '', row['full_name'] or '',
                    row['phone_number'] or '', row['email'] or '',
                    row['account_number'] or '',
                ])),
            })

        # --- fabricantes / marcas ---
        for row in con.execute('SELECT id, name, deleted FROM manufacturers'):
            thirds.append({
                'key': f'fabricante:{row["id"]}',
                'tipo': 'fabricante',
                'id': row['id'],
                'person_id': None,
                'nombre': (row['name'] or '').strip(),
                'apellido': '',
                'razon_social': (row['name'] or '').strip(),
                'contacto': '',
                'email': '',
                'direccion': '',
                'cuenta': '',
                'saldo': 0.0,
                'limite': 0.0,
                'puntos': 0.0,
                'rfc': '',
                'eliminado': bool(row['deleted']),
                'operaciones': 0,
                'importe': 0.0,
                'ultima': '',
                'extra': 'Marca registrada en 1box',
                '_busqueda': norm(row['name'] or ''),
            })

        # --- personas sueltas (contactos y empleados que no son cliente/proveedor) ---
        linked = {t['person_id'] for t in thirds if t.get('person_id')}
        for row in con.execute("""
            SELECT p.person_id AS person_id, p.first_name AS first_name,
                   p.last_name AS last_name, p.full_name AS full_name,
                   p.phone_number AS phone_number, p.email AS email,
                   p.address_1 AS address_1, p.city AS city, p.state AS state,
                   p.zip AS zip,
                   (SELECT COUNT(*) FROM employees e WHERE e.person_id = p.person_id) AS es_empleado
            FROM people p
        """):
            if row['person_id'] in linked:
                continue
            nombre = ' '.join(filter(None, [(row['first_name'] or '').strip(),
                                            (row['last_name'] or '').strip()]))
            nombre = nombre or (row['full_name'] or '').strip()
            thirds.append({
                'key': f'persona:{row["person_id"]}',
                'tipo': 'persona',
                'id': row['person_id'],
                'person_id': row['person_id'],
                'nombre': nombre,
                'apellido': (row['last_name'] or '').strip(),
                'razon_social': '',
                'contacto': (row['phone_number'] or '').strip(),
                'email': (row['email'] or '').strip(),
                'direccion': self._address(row['address_1'], row['city'], row['state'], row['zip']),
                'cuenta': '',
                'saldo': 0.0,
                'limite': 0.0,
                'puntos': 0.0,
                'rfc': '',
                'eliminado': False,
                'operaciones': 0,
                'importe': 0.0,
                'ultima': '',
                'extra': 'Empleado de 1box' if row['es_empleado'] else 'Contacto suelto',
                '_busqueda': norm(' '.join([
                    row['first_name'] or '', row['last_name'] or '', row['full_name'] or '',
                    row['phone_number'] or '', row['email'] or '',
                ])),
            })

        thirds.sort(key=lambda t: (t['tipo'], t['nombre'].lower()))
        self.third_parties = thirds
        self.third_by_key = {t['key']: t for t in thirds}

    @staticmethod
    def _address(address_1, city, state, zip_code):
        parts = [clean_text(address_1, 120), clean_text(city, 60),
                 clean_text(state, 60), clean_text(zip_code, 20)]
        return ', '.join(p for p in parts if p)

    def _load_suppliers(self, con):
        suppliers = {}
        for row in con.execute("""
            SELECT s.id AS id,
                   COALESCE(NULLIF(TRIM(s.company_name), ''),
                            TRIM(COALESCE(p.full_name, '')), '') AS nombre
            FROM suppliers s
            LEFT JOIN people p ON p.person_id = s.person_id
        """):
            suppliers[row['id']] = row['nombre']
        # item_id -> proveedor: se resuelve abajo con un mapa aparte.
        by_item = {}
        for row in con.execute('SELECT item_id, supplier_id FROM items'):
            name = suppliers.get(row['supplier_id'])
            if name:
                by_item[row['item_id']] = name
        return by_item

    # ---------- consulta ----------
    @staticmethod
    def _public(item):
        return {k: v for k, v in item.items() if not k.startswith('_')}

    def status(self):
        total = len(self.products)
        deleted = sum(1 for p in self.products if p['deleted'])
        with_stock = sum(1 for p in self.products if p['stock'] > 0)
        with_code = sum(1 for p in self.products if p['barcode'] or p['alt_codes'])
        counts = {t: 0 for t in self.TIPOS_TERCEROS}
        for third in self.third_parties:
            counts[third['tipo']] = counts.get(third['tipo'], 0) + 1
        return {
            'ok': self.error is None,
            'loading': self.loading,
            'error': self.error,
            'products': total,
            'active': total - deleted,
            'deleted': deleted,
            'with_stock': with_stock,
            'with_code': with_code,
            'codes': len(self.code_all),
            'last_sale': self.last_sale or '',
            'loaded_at': self.loaded_at,
            'db_updated': self.db_updated,
            'db_file': os.path.basename(self.db_path),
            'allow_lan': HOST not in ('127.0.0.1', 'localhost'),
            'third_parties': len(self.third_parties),
            'third_counts': counts,
        }

    def _allowed(self, item, include_deleted):
        return include_deleted or not item['deleted']

    def search(self, query, limit=MAX_RESULTS, include_deleted=True):
        if self.loading:
            return {
                'loading': True, 'query': query, 'total': 0, 'returned': 0,
                'exact': None, 'exact_options': [], 'results': [],
            }
        if self.error:
            return {'ok': False, 'error': self.error, 'query': query,
                    'total': 0, 'returned': 0, 'exact': None,
                    'exact_options': [], 'results': []}

        q = norm(query)
        raw = clean_code(query)
        empty = {
            'query': query, 'total': 0, 'returned': 0,
            'exact': None, 'exact_options': [], 'results': [],
        }
        if not q:
            return empty

        exact = self.by_code.get(raw)
        if exact and not self._allowed(exact, include_deleted):
            exact = None

        tokens = q.split()
        scored = []
        for item in self.products:
            if not self._allowed(item, include_deleted):
                continue
            name = item['_search_name']
            code_hit = len(q) >= 3 and q in item['_search_codes']
            if code_hit:
                rank = -1
            elif tokens and all(t in name for t in tokens):
                rank = 0 if name.startswith(q) else 1
            elif tokens and all(t in (name + ' ' + item['_search_cat']) for t in tokens):
                rank = 2
            else:
                continue
            # Los eliminados van al final entre iguales, sin desaparecer.
            scored.append((rank, 1 if item['deleted'] else 0, item['name'].lower(), item))

        scored.sort(key=lambda t: (t[0], t[1], t[2]))
        total = len(scored)
        results = [self._public(t[3]) for t in scored[:limit]]

        exact_out = self._public(exact) if exact else None
        exact_options = []
        if exact:
            options = self.code_all.get(raw) or [exact]
            exact_options = [
                self._public(i) for i in options
                if self._allowed(i, include_deleted)
            ]
            if exact_out and not any(r['id'] == exact_out['id'] for r in results):
                results.insert(0, dict(exact_out))
                total += 1
                if len(results) > limit:
                    results = results[:limit]

        return {
            'query': query,
            'total': total,
            'returned': len(results),
            'exact': exact_out,
            'exact_options': exact_options,
            'results': results,
        }

    # ---------- terceros: clientes, proveedores, fabricantes y personas ----------
    TIPOS_TERCEROS = ('cliente', 'proveedor', 'fabricante', 'persona')
    ALIAS_TIPOS = {
        'clientes': 'cliente', 'proveedores': 'proveedor',
        'fabricantes': 'fabricante', 'marcas': 'fabricante', 'personas': 'persona',
    }

    def _public_third(self, third):
        return {k: v for k, v in third.items() if not k.startswith('_')}

    def search_thirds(self, query, tipo='todos', limit=60, include_deleted=True):
        """Búsqueda de clientes, proveedores, fabricantes y personas."""
        if self.loading:
            return {'loading': True, 'query': query, 'total': 0, 'returned': 0,
                    'results': [], 'counts': {}}
        if self.error:
            return {'ok': False, 'error': self.error, 'query': query,
                    'total': 0, 'returned': 0, 'results': [], 'counts': {}}

        tipo = self.ALIAS_TIPOS.get(tipo, tipo)
        if tipo not in ('todos',) + self.TIPOS_TERCEROS:
            tipo = 'todos'

        q = norm(query)
        tokens = q.split() if q else []
        counts = {t: 0 for t in self.TIPOS_TERCEROS}
        scored = []

        for third in self.third_parties:
            if tipo != 'todos' and third['tipo'] != tipo:
                continue
            if third['eliminado'] and not include_deleted:
                continue
            name = norm(third['nombre'])
            if tokens:
                if all(t in name for t in tokens):
                    rank = 0 if name.startswith(q) else 1
                elif all(t in third['_busqueda'] for t in tokens):
                    rank = 2
                else:
                    continue
            else:
                rank = 3
            counts[third['tipo']] = counts.get(third['tipo'], 0) + 1
            scored.append((rank, 1 if third['eliminado'] else 0,
                           third['nombre'].lower(), third))

        scored.sort(key=lambda t: (t[0], t[1], t[2]))
        total = len(scored)
        return {
            'ok': True,
            'query': query,
            'tipo': tipo,
            'total': total,
            'returned': min(total, limit),
            'counts': counts,
            'results': [self._public_third(t[3]) for t in scored[:limit]],
        }

    def third_detail(self, key):
        """Ficha completa de un tercero: datos, historial y artículos relacionados."""
        third = self.third_by_key.get(key)
        if not third:
            return None
        out = self._public_third(third)

        con = self.connect()
        try:
            if third['tipo'] == 'cliente':
                out['historial'] = [
                    {
                        'at': r['sale_time'] or '',
                        'ref': f"#{r['sale_id']}",
                        'total': money(r['total']),
                        'pago': clean_text(r['payment_type']),
                        'detalle': clean_text(r['comment']),
                    }
                    for r in con.execute("""
                        SELECT sale_id, sale_time, total, payment_type, comment
                        FROM sales
                        WHERE customer_id = ? AND deleted = 0
                        ORDER BY sale_time DESC LIMIT ?
                    """, (third['id'], MAX_DETAIL_ROWS * 3))
                ]
                out['cuenta_corriente'] = [
                    {
                        'at': r['date'] or '',
                        'ref': f"#{r['sale_id']}" if r['sale_id'] else '—',
                        'monto': money(r['transaction_amount']),
                        'saldo': money(r['balance']),
                        'detalle': clean_text(r['comment']),
                    }
                    for r in con.execute("""
                        SELECT sno, sale_id, transaction_amount, date, balance, comment
                        FROM store_accounts
                        WHERE customer_id = ?
                        ORDER BY date DESC LIMIT ?
                    """, (third['id'], MAX_DETAIL_ROWS * 3))
                ]
                out['articulos'] = []
            elif third['tipo'] == 'proveedor':
                out['historial'] = [
                    {
                        'at': r['receiving_time'] or '',
                        'ref': f"#{r['receiving_id']}",
                        'total': money(r['total']),
                        'pago': clean_text(r['payment_type']),
                        'detalle': clean_text(r['comment']),
                        'articulos': r['articulos'],
                    }
                    for r in con.execute("""
                        SELECT rc.receiving_id, rc.receiving_time, rc.total,
                               rc.payment_type, rc.comment,
                               (SELECT COUNT(*) FROM receiving_items ri
                                 WHERE ri.receiving_id = rc.receiving_id) AS articulos
                        FROM receivings rc
                        WHERE rc.supplier_id = ? AND rc.deleted = 0
                        ORDER BY rc.receiving_time DESC LIMIT ?
                    """, (third['id'], MAX_DETAIL_ROWS * 3))
                ]
                out['cuenta_corriente'] = []
                out['articulos'] = [
                    {
                        'id': r['item_id'],
                        'nombre': r['name'] or '',
                        'sku': r['sku'] or '',
                        'precio': money(r['unit_price']),
                        'costo': money(r['cost_price']),
                    }
                    for r in con.execute("""
                        SELECT item_id, name, sku, unit_price, cost_price
                        FROM items
                        WHERE supplier_id = ?
                        ORDER BY name LIMIT ?
                    """, (third['id'], MAX_DETAIL_ROWS * 4))
                ]
            elif third['tipo'] == 'fabricante':
                out['historial'] = []
                out['cuenta_corriente'] = []
                out['articulos'] = []
            else:  # persona
                out['historial'] = []
                out['cuenta_corriente'] = []
                out['articulos'] = []
        finally:
            con.close()
        return out

    def detail(self, item_id):
        item = self.by_id.get(item_id)
        if not item:
            return None
        out = self._public(item)

        con = self.connect()
        try:
            out['price_history'] = [
                {
                    'at': r['on_date'] or '',
                    'price': money(r['unit_price']),
                    'cost': money(r['cost_price']),
                    'employee': r['employee'] or '',
                }
                for r in con.execute("""
                    SELECT h.on_date, h.unit_price, h.cost_price,
                           TRIM(COALESCE(e.first_name, '') || ' ' ||
                                COALESCE(e.last_name, '')) AS employee
                    FROM items_pricing_history h
                    LEFT JOIN employees e ON e.id = h.employee_id
                    WHERE h.item_id = ?
                    ORDER BY h.on_date DESC, h.id DESC
                    LIMIT ?
                """, (item_id, MAX_DETAIL_ROWS))
            ]

            out['movements'] = [
                {
                    'at': r['trans_date'] or '',
                    'comment': r['trans_comment'] or '',
                    'change': qty(r['trans_inventory']),
                    'current': qty(r['trans_current_quantity']),
                    'employee': r['employee'] or '',
                }
                for r in con.execute("""
                    SELECT l.trans_date, l.trans_comment, l.trans_inventory,
                           l.trans_current_quantity,
                           TRIM(COALESCE(e.first_name, '') || ' ' ||
                                COALESCE(e.last_name, '')) AS employee
                    FROM inventory_log l
                    LEFT JOIN employees e ON e.id = l.trans_user
                    WHERE l.trans_items = ?
                    ORDER BY l.trans_id DESC
                    LIMIT ?
                """, (item_id, MAX_DETAIL_ROWS))
            ]

            out['recent_sales'] = [
                {
                    'sale_id': r['sale_id'],
                    'at': r['sale_time'] or '',
                    'qty': qty(r['qty']),
                    'price': money(r['unit_price']),
                    'total': money(r['total']),
                }
                for r in con.execute("""
                    SELECT s.sale_id, s.sale_time, si.qty, si.unit_price, si.total
                    FROM sale_items si
                    JOIN sales s ON s.sale_id = si.sale_id
                    WHERE si.item_id = ? AND s.deleted = 0
                    ORDER BY s.sale_time DESC
                    LIMIT ?
                """, (item_id, MAX_DETAIL_ROWS))
            ]
        finally:
            con.close()

        return out


CATALOG = HistoricoCatalog(DB_PATH)


# --------------------------------------------------------------------------
# servidor HTTP
# --------------------------------------------------------------------------
class Handler(SimpleHTTPRequestHandler):
    server_version = f'HistoricoLaColonial/{APP_VERSION}'

    def log_message(self, fmt, *args):
        pass  # el lector dispara muchas peticiones; consola limpia

    def end_headers(self):
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _ip_permitida(self):
        """Si hay lista de IPs permitidas, sólo entran esas."""
        if not ALLOWED_IPS:
            return True
        return self.client_address[0] in ALLOWED_IPS

    def _denegar(self):
        body = json.dumps({
            'ok': False,
            'error': 'Este equipo no está autorizado a consultar el histórico. '
                     'Pide al administrador que agregue su IP a la lista de '
                     'equipos permitidos.',
        }, ensure_ascii=False).encode('utf-8')
        self.send_response(403)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if not self._ip_permitida():
            self._denegar()
            return

        if parsed.path == '/api/status':
            self._send_json(CATALOG.status())
            return

        if parsed.path == '/api/search':
            params = parse_qs(parsed.query)
            query = (params.get('q') or [''])[0]
            include_deleted = (params.get('eliminados') or ['1'])[0] not in ('0', 'false', 'no')
            try:
                limit = min(int((params.get('limit') or [MAX_RESULTS])[0]), 200)
            except ValueError:
                limit = MAX_RESULTS
            self._send_json(CATALOG.search(query, limit, include_deleted))
            return

        if parsed.path == '/api/terceros':
            params = parse_qs(parsed.query)
            query = (params.get('q') or [''])[0]
            tipo = (params.get('tipo') or ['todos'])[0]
            alias = {'personas': 'persona', 'clientes': 'cliente',
                     'proveedores': 'proveedor', 'fabricantes': 'fabricante',
                     'marcas': 'fabricante'}
            tipo = alias.get(tipo, tipo)
            if tipo not in ('todos',) + HistoricoCatalog.TIPOS_TERCEROS:
                tipo = 'todos'
            include_deleted = (params.get('eliminados') or ['1'])[0] not in ('0', 'false', 'no')
            try:
                limit = min(int((params.get('limit') or [MAX_RESULTS])[0]), 200)
            except ValueError:
                limit = MAX_RESULTS
            self._send_json(CATALOG.search_thirds(query, tipo, limit, include_deleted))
            return

        if parsed.path == '/api/tercero':
            params = parse_qs(parsed.query)
            key = (params.get('key') or [''])[0].strip()
            third = CATALOG.third_detail(key)
            if third is None:
                self._send_json({'ok': False, 'error': 'Registro no encontrado.'}, 404)
            else:
                self._send_json({'ok': True, 'tercero': third})
            return

        if parsed.path == '/api/item':
            params = parse_qs(parsed.query)
            try:
                item_id = int((params.get('id') or ['0'])[0])
            except ValueError:
                item_id = 0
            item = CATALOG.detail(item_id)
            if item is None:
                self._send_json({'ok': False, 'error': 'Producto no encontrado.'}, 404)
            else:
                self._send_json({'ok': True, 'item': item})
            return

        if parsed.path == '/':
            self.path = '/index.html'
        return super().do_GET()

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)


def build_server(host, base_port=None):
    handler = partial(Handler, directory=UI_DIR)
    last_error = None
    base = base_port or PORT_BASE
    for offset in range(10):
        port = base + offset
        try:
            server = ThreadingHTTPServer((host, port), handler)
            server.daemon_threads = True
            return server, port
        except OSError as exc:
            last_error = exc
    raise SystemExit(
        f'No se pudo abrir el puerto {base} (ni los 9 siguientes): {last_error}'
    )


def existing_instance(base_port=None):
    """Si ya hay una consulta abierta, se reutiliza en vez de abrir otra."""
    import urllib.request
    base = base_port or PORT_BASE
    for offset in range(10):
        url = f'http://127.0.0.1:{base + offset}'
        try:
            with urllib.request.urlopen(f'{url}/api/status', timeout=0.8) as resp:
                body = resp.read(8192)
                if resp.status == 200 and b'historico_1box' in body:
                    try:
                        info = json.loads(body.decode('utf-8'))
                    except ValueError:
                        info = {}
                    return url, info
        except Exception:
            continue
    return None


def ip_local():
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return '127.0.0.1'


def main():
    global HOST, ALLOWED_IPS

    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    if not os.path.exists(DB_PATH):
        print('ERROR: no se encontró historico_1box.db')
        print(f'Se esperaba en: {DB_PATH}')
        return 1
    if not os.path.isdir(UI_DIR):
        print('ERROR: falta la carpeta Historico\\ui\\.')
        return 1

    config, config_error = load_config()
    if config_error:
        print(f'Aviso: {config_error}')

    puerto_arg = None
    if '--puerto' in sys.argv:
        try:
            puerto_arg = int(sys.argv[sys.argv.index('--puerto') + 1])
        except (IndexError, ValueError):
            print('ERROR: --puerto necesita un número (ejemplo: --puerto 8095).')
            return 1
        if not 1 <= puerto_arg <= 65535:
            print('ERROR: el puerto debe estar entre 1 y 65535.')
            return 1

    base_port = puerto_arg or config['puerto']

    # Prioridad: argumentos de la línea de comandos > historico_config.json.
    if '--red' in sys.argv or '--lan' in sys.argv:
        permitir_red = True
    elif '--local' in sys.argv:
        permitir_red = False
    else:
        permitir_red = bool(config['publicar_en_red'])

    HOST = '0.0.0.0' if permitir_red else '127.0.0.1'
    ALLOWED_IPS = list(config['ips_permitidas'])

    if '--ips' in sys.argv:
        try:
            valor = sys.argv[sys.argv.index('--ips') + 1]
        except IndexError:
            print('ERROR: --ips necesita una lista '
                  '(ejemplo: --ips 192.168.0.55,192.168.0.56).')
            return 1
        ALLOWED_IPS = [ip.strip() for ip in valor.split(',') if ip.strip()]

    running = None if puerto_arg else existing_instance(base_port)
    if running:
        url_actual, info = running
        # Si se pidió red y la instancia abierta es sólo local, no sirve.
        if permitir_red and not info.get('allow_lan'):
            running = None

    if running:
        print('=' * 64)
        print('  YA HAY UNA CONSULTA DEL HISTÓRICO ABIERTA')
        print('=' * 64)
        print(f'  Se abrirá: {running[0]}')
        if permitir_red:
            print(f'  Red local    : http://{ip_local()}:{running[0].rsplit(":", 1)[-1]}/')
        try:
            if '--no-browser' not in sys.argv:
                webbrowser.open(running[0])
        except Exception:
            pass
        return 0

    # El índice se carga en segundo plano: la interfaz aparece de inmediato
    # y muestra "cargando" hasta que termina.
    threading.Thread(target=CATALOG.load, daemon=True).start()

    server, port = build_server(HOST, base_port)
    local_url = f'http://127.0.0.1:{port}/'
    lan_url = f'http://{ip_local()}:{port}/'

    print('=' * 64)
    print('  LA COLONIAL - HISTÓRICO 1BOX (SOLO CONSULTA)')
    print('=' * 64)
    print(f'  Esta PC      : {local_url}')
    if permitir_red:
        print(f'  Red local    : {lan_url}')
        if ALLOWED_IPS:
            print(f'  Equipos OK   : {", ".join(ALLOWED_IPS)}')
        else:
            print('  Equipos OK   : cualquiera de la red local')
        print('  Firewall     : si otro equipo no entra, ejecuta como')
        print('                 administrador "abrir_puerto_historico.bat".')
    else:
        print('  Red local    : desactivada (sólo esta computadora)')
        print('                 Para publicarla:  consultar_historico.bat --red')
    print(f'  Puerto       : {port} (configurable en historico_config.json)')
    print(f'  Base         : {os.path.basename(DB_PATH)} '
          f'({os.path.getsize(DB_PATH) / (1024 * 1024):.0f} MB)')
    print('  Modo         : solo lectura (no se puede modificar nada)')
    print('  Cerrar       : cierra esta ventana o presiona Ctrl+C')
    print('=' * 64)

    try:
        # El navegador se abre sólo en esta computadora. Los demás equipos
        # entran con la dirección de red que se muestra arriba.
        if '--no-browser' not in sys.argv:
            webbrowser.open(local_url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServidor detenido.')
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
