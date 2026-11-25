"""
Catálogos del proyecto.

- `Catalog`     : catálogo limpio que genera el ETL
                  (`Historico/Auxiliares/inventario_moderno.db`).
- `OwnedCatalog`: inventario propio del POS (tabla `products` de pos.db).
- `StoreCatalog`: decide cuál usar. Si el inventario propio ya fue importado,
                  manda el del POS; si no, se usa el catálogo limpio mientras
                  1box siga siendo la fuente.
"""

import datetime
import os
import sqlite3
import threading
import unicodedata

MAX_RESULTS = 60

PRODUCT_COLUMNS = """
    sku, barcode, item_number, alt_codes, name, category, location,
    sale_price, cost_price, stock, units_sold_period, last_sold_at, legacy_item_id
"""


def strip_accents(text):
    return ''.join(
        c for c in unicodedata.normalize('NFKD', text)
        if not unicodedata.combining(c)
    )


def norm(text):
    """Normaliza para búsqueda: minúsculas, sin acentos, espacios simples."""
    if not text:
        return ''
    return ' '.join(strip_accents(str(text).lower()).split())


class Catalog:
    """Catálogo en memoria; se recarga solo cuando cambia el origen."""

    SQL = f"SELECT {PRODUCT_COLUMNS} FROM products"

    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._mtime = None
        self._updated_text = ''
        self._products = []
        self._by_code = {}
        self._code_all = {}
        self._by_key = {}
        self.loaded_at = None
        self.error = None
        self.source = 'catalogo'

    # ---------- versión (qué tan seguido se recarga) ----------
    def _version(self):
        try:
            return os.path.getmtime(self.db_path)
        except OSError:
            return None

    def ensure_loaded(self):
        version = self._version()
        if version is None:
            self.error = 'No se encontró ' + os.path.basename(self.db_path)
            return
        if version == self._mtime and self._products:
            return
        with self._lock:
            if version == self._mtime and self._products:
                return
            for _ in range(3):
                try:
                    self._load()
                    self._mtime = version
                    self.loaded_at = datetime.datetime.now()
                    self.error = None
                    return
                except sqlite3.Error as exc:
                    self.error = str(exc)

    def _load(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            rows = conn.execute(self.SQL).fetchall()
        finally:
            conn.close()
        self._build(rows)
        try:
            self._updated_text = datetime.datetime.fromtimestamp(
                os.path.getmtime(self.db_path)).strftime('%Y-%m-%d %H:%M:%S')
        except OSError:
            self._updated_text = ''

    # ---------- construcción del índice ----------
    def _build(self, rows):
        products = []
        by_code = {}
        code_all = {}
        by_key = {}
        for row in rows:
            (sku, barcode, item_number, alt_codes, name, category, location,
             sale_price, cost_price, stock, sold, last_sold, legacy_id) = row
            alt_list = [c.strip() for c in (alt_codes or '').split('|') if c.strip()]
            key = legacy_id if legacy_id is not None else sku
            item = {
                'key': key,
                'legacy_id': legacy_id,
                'sku': sku or '',
                'barcode': barcode or '',
                'item_number': item_number or '',
                'alt_codes': alt_list,
                'name': name or '',
                'category': category or '',
                'location': location or '',
                'unit': 'Pieza',
                'unit_symbol': 'pza',
                'price': round(float(sale_price or 0), 2),
                'cost': round(float(cost_price or 0), 2),
                'stock': float(stock or 0),
                'sold': float(sold or 0),
                'last_sold': last_sold or '',
                'search': norm(name),
                'search_cat': norm(category),
            }
            item['search_codes'] = norm(' '.join(
                [item['barcode'], item['sku'], item['item_number']] + alt_list
            ))
            products.append(item)
            by_key[str(key)] = item
            for code in (item['barcode'], item['sku'], item['item_number']) + tuple(alt_list):
                code = (code or '').strip().upper()
                if not code:
                    continue
                bucket = code_all.setdefault(code, [])
                if not any(existing is item for existing in bucket):
                    bucket.append(item)

        # Prioridad de resolución: código de barras > SKU > UPC/EAN/ISBN
        # > códigos alternativos.
        for item in products:
            for code in (item['barcode'], item['sku'], item['item_number']):
                if code:
                    by_code.setdefault(code.strip().upper(), item)
        for item in products:
            for code in item['alt_codes']:
                if code:
                    by_code.setdefault(code.strip().upper(), item)

        self._products = products
        self._by_code = by_code
        self._code_all = code_all
        self._by_key = by_key
        self._packs_by_base = {}

    # ---------- consulta ----------
    @staticmethod
    def _public(item):
        out = dict(item)
        out.pop('search', None)
        out.pop('search_cat', None)
        out.pop('search_codes', None)
        return out

    def get(self, key):
        """Producto por su clave estable (id del POS o id legado de 1box)."""
        self.ensure_loaded()
        item = self._by_key.get(str(key))
        return self._public(item) if item else None

    def get_by_code(self, code):
        self.ensure_loaded()
        item = self._by_code.get((code or '').strip().upper())
        return self._public(item) if item else None

    def products_for_code(self, code):
        self.ensure_loaded()
        return [self._public(i) for i in self._code_all.get((code or '').strip().upper(), [])]

    def duplicate_code_count(self):
        self.ensure_loaded()
        return sum(1 for items in self._code_all.values() if len(items) > 1)

    def duplicate_barcode_count(self):
        """Códigos de barras que pertenecen a más de un producto."""
        self.ensure_loaded()
        return sum(
            1 for code, items in self._code_all.items()
            if len(items) > 1 and any((i['barcode'] or '').upper() == code for i in items)
        )

    def price_map(self):
        self.ensure_loaded()
        return {str(i['key']): i['price'] for i in self._products}

    def _family_items(self, item):
        """Producto base y sus empaques vinculados (ordenados: base primero)."""
        if not item:
            return []
        packs_by_base = getattr(self, '_packs_by_base', None) or {}
        base_id = str(item['base_id']) if item.get('base_id') else str(item['key'])
        base = self._by_key.get(base_id)
        packs = packs_by_base.get(base_id, [])
        if not packs:
            return [item]
        out = []
        if base:
            out.append(base)
        for p in sorted(packs, key=lambda x: (float(x.get('base_qty') or 1), x['name'].lower())):
            if not any(existing is p for existing in out):
                out.append(p)
        if not any(existing is item for existing in out):
            out.insert(0, item)
        return out

    def search(self, query, limit=MAX_RESULTS):
        self.ensure_loaded()
        q = norm(query).strip()
        raw = (query or '').strip().upper()
        if not q:
            return {'query': query, 'total': 0, 'returned': 0, 'exact': None,
                    'exact_options': [], 'family_options': [], 'results': []}

        exact = self._by_code.get(raw)
        tokens = q.split()

        scored = []
        for item in self._products:
            name = item['search']
            code_hit = len(q) >= 3 and bool(item['search_codes']) and q in item['search_codes']
            if code_hit:
                rank = -1
            elif tokens and all(t in name for t in tokens):
                rank = 0 if name.startswith(q) else 1
            elif tokens and all(t in (name + ' ' + item['search_cat']) for t in tokens):
                rank = 2
            else:
                continue
            scored.append((rank, item['name'].lower(), item))

        scored.sort(key=lambda t: (t[0], t[1]))

        # Si coincide una pieza o su empaque, se incluyen juntas todas sus
        # presentaciones (pieza y paquete) para poder elegir cuál vender.
        expanded = []
        seen_keys = set()
        for _rank, _name, item in scored:
            family = self._family_items(item)
            if len(family) > 1:
                ordered = [item] + [f for f in family if f is not item]
            else:
                ordered = [item]
            for member in ordered:
                k = str(member['key'])
                if k not in seen_keys:
                    seen_keys.add(k)
                    expanded.append(member)

        total = len(expanded)
        results = [self._public(m) for m in expanded[:limit]]

        exact_out = self._public(exact) if exact else None
        exact_options = []
        family_options = []
        if exact:
            exact_options = self.products_for_code(exact['barcode'] or raw) if exact['barcode'] else []
            if len(exact_options) < 2:
                exact_options = self.products_for_code(raw)
            fam = self._family_items(exact)
            if len(fam) > 1:
                family_options = [self._public(m) for m in fam]
            top_family = [self._public(m) for m in ([exact] + [f for f in fam if f is not exact])]
            top_keys = {r['key'] for r in top_family}
            rest = [r for r in results if r['key'] not in top_keys]
            added = sum(1 for r in top_family if not any(x['key'] == r['key'] for x in results))
            total += added
            results = (top_family + rest)[:limit]

        return {
            'query': query,
            'total': total,
            'returned': len(results),
            'exact': exact_out,
            'exact_options': exact_options,
            'family_options': family_options,
            'results': results,
        }

    def status(self):
        self.ensure_loaded()
        return {
            'ok': self.error is None,
            'error': self.error,
            'products': len(self._products),
            'loaded_at': self.loaded_at.strftime('%Y-%m-%d %H:%M:%S') if self.loaded_at else None,
            'db_updated': self._updated_text or None,
            'db_file': os.path.basename(self.db_path),
        }


class OwnedCatalog(Catalog):
    """Inventario propio del POS: tabla `products` de pos.db."""

    _COLUMNS = ', '.join(f'p.{c.strip()}' for c in PRODUCT_COLUMNS.split(','))

    SQL = f"""SELECT p.id, {_COLUMNS}, p.unit, p.unit_symbol,
                     p.base_product_id AS base_id, p.base_qty AS base_qty,
                     b.stock AS base_stock, b.active AS base_active, b.name AS base_name
              FROM products p
              LEFT JOIN products b ON b.id = p.base_product_id
              WHERE p.active = 1"""

    def __init__(self, db_path, version_fn):
        super().__init__(db_path)
        self.source = 'propio'
        self._version_fn = version_fn

    def _version(self):
        try:
            return self._version_fn()
        except Exception:
            return None

    def _load(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            rows = conn.execute(self.SQL).fetchall()
        finally:
            conn.close()
        self._build(rows)
        self._updated_text = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _build(self, rows):
        products = []
        by_code = {}
        code_all = {}
        by_key = {}
        for row in rows:
            (pid, sku, barcode, item_number, alt_codes, name, category, location,
             sale_price, cost_price, stock, sold, last_sold, legacy_id,
             unit, unit_symbol, base_id, base_qty, base_stock, base_active,
             base_name) = row
            alt_list = [c.strip() for c in (alt_codes or '').split('|') if c.strip()]
            item = {
                'key': pid,
                'legacy_id': legacy_id,
                'sku': sku or '',
                'barcode': barcode or '',
                'item_number': item_number or '',
                'alt_codes': alt_list,
                'name': name or '',
                'category': category or '',
                'location': location or '',
                'unit': unit or 'Pieza',
                'unit_symbol': unit_symbol or 'pza',
                'price': round(float(sale_price or 0), 2),
                'cost': round(float(cost_price or 0), 2),
                'stock': float(stock or 0),
                'sold': float(sold or 0),
                'last_sold': last_sold or '',
                'search': norm(name),
                'search_cat': norm(category),
                'base_id': base_id,
                'base_qty': float(base_qty or 1),
                'base_active': base_active,
                'base_name': base_name,
            }
            if base_id and base_stock is not None:
                # Un empaque muestra y descuenta el stock del producto base:
                # existencias visibles = stock del base / unidades por empaque.
                item['stock'] = round(float(base_stock) / float(base_qty or 1), 3)
            item['search_codes'] = norm(' '.join(
                [item['barcode'], item['sku'], item['item_number']] + alt_list
            ))
            products.append(item)
            by_key[str(pid)] = item
            for code in (item['barcode'], item['sku'], item['item_number']) + tuple(alt_list):
                code = (code or '').strip().upper()
                if not code:
                    continue
                bucket = code_all.setdefault(code, [])
                if not any(existing is item for existing in bucket):
                    bucket.append(item)

        for item in products:
            for code in (item['barcode'], item['sku'], item['item_number']):
                if code:
                    by_code.setdefault(code.strip().upper(), item)
        for item in products:
            for code in item['alt_codes']:
                if code:
                    by_code.setdefault(code.strip().upper(), item)

        packs_by_base = {}
        for item in products:
            if item.get('base_id'):
                packs_by_base.setdefault(str(item['base_id']), []).append(item)
        for item in products:
            item['has_packs'] = str(item['key']) in packs_by_base

        self._products = products
        self._by_code = by_code
        self._code_all = code_all
        self._by_key = by_key
        self._packs_by_base = packs_by_base


class StoreCatalog:
    """
    Inventario que usa la caja.

    - Si `pos.db` ya tiene productos (inventario importado), manda el propio.
    - Mientras tanto usa el catálogo limpio de
      `Historico/Auxiliares/inventario_moderno.db`.
    """

    def __init__(self, owned_path, legacy_path, version_fn, owned_count_fn):
        self.owned = OwnedCatalog(owned_path, version_fn)
        self.legacy = Catalog(legacy_path)
        self._owned_count_fn = owned_count_fn
        self.source = 'catalogo'

    def _active(self):
        try:
            count = self._owned_count_fn()
        except Exception:
            count = 0
        if count and count.get('active', 0):
            self.source = 'propio'
            return self.owned
        self.source = 'catalogo'
        return self.legacy

    def ensure_loaded(self):
        self._active().ensure_loaded()

    def search(self, query, limit=MAX_RESULTS):
        return self._active().search(query, limit)

    def get(self, key):
        return self._active().get(key)

    def get_by_code(self, code):
        return self._active().get_by_code(code)

    def products_for_code(self, code):
        return self._active().products_for_code(code)

    def duplicate_barcode_count(self):
        return self._active().duplicate_barcode_count()

    def price_map(self):
        return self._active().price_map()

    def status(self):
        active = self._active()
        status = active.status()
        status['source'] = self.source
        return status
