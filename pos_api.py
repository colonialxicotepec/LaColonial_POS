"""
Capa HTTP del punto de venta.

Expone /api/pos/* y la página imprimible del ticket. La usa app_pos.py,
que sigue sirviendo el buscador en la raíz y el POS en /pos.
"""

import base64
import datetime
import html
import json
import math
import os
import sqlite3
import traceback
from urllib.parse import parse_qs, urlparse

import pos_core
import pos_print
from catalogo import norm

APP_VERSION = '0.2.7'
MAX_BODY = 1_000_000
ERROR_LOG = os.path.join(pos_core.BASE_DIR, 'pos_errores.log')

ALLOWED_METHODS = ('efectivo', 'tarjeta', 'transferencia')

# Tope de piezas por denominación al cerrar turno (evita conteos absurdos).
MAX_DENOM_QTY = 100000


def _log_error(message):
    # Las desconexiones normales del navegador no son errores del POS.
    ruido = ('ConnectionAbortedError', 'ConnectionResetError', 'BrokenPipeError',
             'WinError 10053', 'WinError 10054')
    if any(token in message for token in ruido):
        return
    try:
        with open(ERROR_LOG, 'a', encoding='utf-8') as fh:
            fh.write(f'\n[{pos_core.now_text()}]\n{message}\n')
    except OSError:
        pass


def blocked_reason(product, config):
    """Motivo por el que un producto no se puede cobrar, o None si es vendible."""
    rules = config.get('rules', {})
    name = norm(product.get('name'))
    for blocked in rules.get('blocked_names', []):
        if name and name == norm(blocked):
            return 'Artículo de control interno: no se vende'
    sku = (product.get('sku') or '').strip().upper()
    blocked_skus = {str(s).strip().upper() for s in rules.get('blocked_skus', []) if str(s).strip()}
    if sku and sku in blocked_skus:
        return 'Producto bloqueado para venta'
    if float(product.get('price') or 0) < float(rules.get('min_price', 0.01) or 0):
        return 'Producto sin precio de venta'
    if product.get('base_id') and not product.get('base_active'):
        return 'Su producto base está dado de baja'
    return None


class PosAPI:
    def __init__(self, catalog):
        self.catalog = catalog

    # ------------------------------------------------------------ utilidades
    def _json(self, handler, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        handler.send_response(status)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('Content-Length', str(len(body)))
        handler.send_header('Cache-Control', 'no-store')
        handler.end_headers()
        try:
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _body(self, handler):
        try:
            length = int(handler.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError('La petición es demasiado grande.')
        raw = handler.rfile.read(length)
        try:
            data = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError('Datos inválidos.')
        if not isinstance(data, dict):
            raise ValueError('Datos inválidos.')
        return data

    def _token(self, handler, parsed):
        token = handler.headers.get('X-Pos-Token')
        if token:
            return token.strip()
        params = parse_qs(parsed.query)
        return (params.get('token') or [''])[0].strip()

    def _auth(self, handler, parsed):
        config = pos_core.load_config()
        hours = int(config.get('security', {}).get('session_hours', 12) or 12)
        session = pos_core.get_session(self._token(handler, parsed), hours)
        if not session:
            self._json(handler, {'ok': False, 'error': 'Sesión expirada. Vuelve a entrar con tu PIN.'}, 401)
            return None, config
        return session, config

    def _sale_or_404(self, handler, sale_id):
        sale = pos_core.get_sale(sale_id)
        if not sale:
            self._json(handler, {'ok': False, 'error': 'Venta no encontrada.'}, 404)
        return sale

    # ------------------------------------------------------------ decoración
    def _decorate(self, product, config, sold_map, overrides=None):
        out = dict(product)
        sold = float(sold_map.get(product.get('legacy_id'), 0) or 0) if sold_map else 0.0
        out['stock_catalog'] = float(product.get('stock') or 0)
        out['sold_local'] = sold
        out['stock'] = float(product.get('stock') or 0) - sold
        reason = blocked_reason(product, config)
        out['blocked_reason'] = reason
        out['sellable'] = reason is None
        price, is_override, original = self._effective_price(product, overrides or {})
        out['price'] = price
        out['price_override'] = is_override
        if is_override:
            out['original_price'] = original
        return out

    @staticmethod
    def _effective_price(product, overrides):
        """Precio de venta vigente: ajuste local de emergencia o el del catálogo."""
        override = overrides.get(str(product.get('key')))
        if override:
            return pos_core.money(override['price']), True, pos_core.money(override['original_price'])
        return pos_core.money(product.get('price') or 0), False, None

    def _require_manager(self, pin):
        found = pos_core.find_user_by_pin((pin or '').strip())
        if not found or found['role'] not in ('gerente', 'administrador'):
            raise ValueError('PIN de gerente o administrador incorrecto.')
        return found

    def _require_cashier_authority(self, session):
        role = session.get('role', '')
        if role not in ('gerente', 'administrador'):
            raise ValueError('Acceso restringido: esta función solo está disponible para Gerente o Administrador.')

    def _catalog_status(self):
        status = self.catalog.status()
        status['duplicate_barcodes'] = self.catalog.duplicate_barcode_count()
        return status

    # ------------------------------------------------------------ GET
    def handle_get(self, handler, parsed):
        path = parsed.path
        if path == '/api/pos/info':
            return self._info(handler)
        if path == '/api/pos/health':
            return self._json(handler, {'ok': True, 'time': pos_core.now_text(), 'version': APP_VERSION})
        if path == '/api/pos/state':
            return self._state(handler, parsed)
        if path == '/api/pos/search':
            return self._search(handler, parsed)
        if path == '/api/pos/clientes':
            return self._customers(handler, parsed)
        if path == '/api/pos/cliente':
            return self._customer(handler, parsed)
        if path == '/api/pos/today':
            return self._today(handler, parsed)
        if path == '/api/pos/printers':
            return self._printers(handler, parsed)
        if path == '/api/pos/audit':
            return self._audit(handler, parsed)
        if path == '/api/pos/users':
            return self._users(handler, parsed)
        if path == '/api/pos/prices':
            return self._prices(handler, parsed)
        if path == '/api/pos/inventory/movements':
            return self._inventory_movements(handler, parsed)
        if path == '/api/pos/inventory/products':
            return self._inventory_products(handler, parsed)
        if path == '/api/pos/inventory/next-sku':
            return self._inventory_next_sku(handler, parsed)
        if path == '/api/pos/units':
            return self._units(handler, parsed)
        if path == '/api/pos/categories':
            return self._categories(handler, parsed)
        if path == '/api/pos/locations':
            return self._locations(handler, parsed)
        if path == '/api/pos/reasons':
            return self._reasons(handler, parsed)
        if path == '/api/pos/cash/movements':
            return self._cash_movements(handler, parsed)
        if path == '/api/pos/sales/pending':
            return self._pending_sales(handler, parsed)
        if path == '/api/pos/export/sales':
            return self._export(handler, parsed)
        if path == '/api/pos/notifications':
            return self._notifications(handler, parsed)
        if path.startswith('/api/pos/sale/'):
            return self._sale_detail(handler, parsed)
        self._json(handler, {'ok': False, 'error': 'Ruta no encontrada.'}, 404)

    def _info(self, handler):
        config = pos_core.load_config()
        users = pos_core.users_exist()
        status = self._catalog_status()
        self._json(handler, {
            'ok': True,
            'version': APP_VERSION,
            'configured': users,
            'store': config.get('store', {}),
            'printer_name': pos_print.resolve_printer(config),
            'catalog': status,
        })

    def _state(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        try:
            try:
                pos_core.prune_price_overrides(self.catalog.price_map())
            except Exception:
                pass
            shift = pos_core.get_open_shift()
            pending = self._pending_local_sales()
            self._json(handler, {
                'ok': True,
                'version': APP_VERSION,
                'user': {'id': session['user_id'], 'name': session['name'], 'role': session['role']},
                'shift': pos_core.shift_summary(shift['id']) if shift else None,
                'today': pos_core.today_summary(),
                'store': config.get('store', {}),
                'printer': {
                    'name': pos_print.resolve_printer(config),
                    'direct': os.name == 'nt',
                    'open_drawer': config.get('printer', {}).get('open_drawer', False),
                    'print_sale': config.get('printer', {}).get('print_sale', True),
                    'print_shift': config.get('printer', {}).get('print_shift', True),
                },
                'catalog': self._catalog_status(),
                'inventory': {
                    'source': self.catalog.source,
                    'products': pos_core.products_count(),
                    'owner': self.catalog.source == 'propio',
                },
                'rules': {
                    'allow_zero_stock': config.get('rules', {}).get('allow_zero_stock', True),
                    'max_cash_input': config.get('rules', {}).get('max_cash_input', 50000),
                    'max_qty_per_line': config.get('rules', {}).get('max_qty_per_line', 999),
                    'allow_emergency_prices': config.get('rules', {}).get('allow_emergency_prices', True),
                    'fondo_maximo': config.get('rules', {}).get('fondo_maximo', 1400),
                },
                'security': {
                    'manager_required_for_cancel': config.get('security', {}).get(
                        'manager_required_for_cancel', True),
                    'manager_required_for_return': config.get('security', {}).get(
                        'manager_required_for_return', True),
                },
                'pending': pending,
                'pending_sales': pos_core.list_pending_sales(),
                'notifications': pos_core.notification_count(),
            })
        except Exception as exc:
            _log_error(traceback.format_exc())
            self._json(handler, {'ok': False, 'error': f'Error interno: {exc}'}, 500)

    def _pending_local_sales(self):
        """Ventas cobradas en esta caja que no existen en 1box (contingencia)."""
        try:
            return pos_core.count_sales_total()['count']
        except Exception:
            return 0

    def _search(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        query = (params.get('q') or [''])[0]
        try:
            limit = min(int((params.get('limit') or ['30'])[0]), 100)
        except ValueError:
            limit = 30
        try:
            data = self.catalog.search(query, limit)
            # Con inventario propio el stock ya descuenta las ventas: no se ajusta.
            sold = {} if self.catalog.source == 'propio' else pos_core.sales_since(
                self.catalog.status().get('db_updated'))
            overrides = pos_core.active_price_overrides()
            data['results'] = [self._decorate(p, config, sold, overrides) for p in data['results']]
            data['exact'] = self._decorate(data['exact'], config, sold, overrides) if data.get('exact') else None
            data['exact_options'] = [self._decorate(p, config, sold, overrides) for p in data.get('exact_options', [])]
            data['family_options'] = [self._decorate(p, config, sold, overrides) for p in data.get('family_options', [])]
            duplicate = len(data['exact_options']) > 1
            has_presentations = len(data['family_options']) > 1
            if data['exact']:
                data['exact']['duplicate'] = duplicate
                data['exact']['has_presentations'] = has_presentations
            if duplicate:
                dup_ids = {p['legacy_id'] for p in data['exact_options']}
                for product in data['results']:
                    if product['legacy_id'] in dup_ids:
                        product['duplicate'] = True
            # Avisos de stock negativo: se crean al detectarlos en la búsqueda
            # y permanecen hasta que el inventario se corrija.
            if self.catalog.source == 'propio':
                for product in ([data['exact']] if data.get('exact') else []) + \
                        data.get('exact_options', []) + data.get('family_options', []) + \
                        data.get('results', []):
                    if product and float(product.get('stock') or 0) < 0:
                        pos_core.notify_negative_stock(int(product['key']), 'búsqueda')
            self._json(handler, {'ok': True, **data})
        except Exception as exc:
            _log_error(traceback.format_exc())
            self._json(handler, {'ok': False, 'error': f'Error al buscar: {exc}'}, 500)

    def _customers(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        query = (params.get('q') or [''])[0].strip()[:120]
        include_deleted = (params.get('eliminados') or ['0'])[0] in ('1', 'true', 'si', 'sí')
        try:
            limit = min(max(int((params.get('limit') or ['50'])[0]), 1), 100)
        except ValueError:
            limit = 50
        data = pos_core.search_customers(query, include_deleted, limit)
        self._json(handler, {'ok': True, **data})

    def _customer(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        try:
            customer_id = int((params.get('id') or ['0'])[0])
        except (TypeError, ValueError):
            customer_id = 0
        customer = pos_core.get_customer(customer_id)
        if not customer:
            self._json(handler, {'ok': False, 'error': 'Cliente no encontrado.'}, 404)
            return
        self._json(handler, {'ok': True, 'customer': customer})

    def _today(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._json(handler, {'ok': True, **pos_core.today_summary()})

    def _notifications(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        todas = (params.get('todas') or ['0'])[0] in ('1', 'true', 'si')
        self._json(handler, {
            'ok': True,
            'count': pos_core.notification_count(),
            'notifications': pos_core.open_notifications(include_resolved=todas),
        })

    def _printers(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._json(handler, {
            'ok': True,
            'printers': pos_print.list_printers(),
            'default': pos_print.default_printer(),
            'configured': (config.get('printer', {}).get('name') or '').strip(),
        })

    def _audit(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        if session['role'] not in ('gerente', 'administrador'):
            self._json(handler, {'ok': False, 'error': 'Solo el gerente o el administrador puede ver la bitácora.'}, 403)
            return
        self._json(handler, {'ok': True, 'entries': pos_core.audit_log(200)})

    def _users(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        if session['role'] != 'administrador':
            self._json(handler, {'ok': False, 'error': 'Solo el administrador puede administrar usuarios.'}, 403)
            return
        self._json(handler, {'ok': True, 'users': pos_core.list_users()})

    def _sale_detail(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        try:
            sale_id = int(parsed.path.rsplit('/', 1)[-1])
        except ValueError:
            self._json(handler, {'ok': False, 'error': 'Venta inválida.'}, 400)
            return
        sale = self._sale_or_404(handler, sale_id)
        if sale:
            self._json(handler, {'ok': True, 'sale': sale})

    # ------------------------------------------------------------ POST
    def handle_post(self, handler, parsed):
        path = parsed.path
        try:
            if path == '/api/pos/bootstrap':
                return self._bootstrap(handler)
            if path == '/api/pos/login':
                return self._login(handler)
            if path == '/api/pos/logout':
                return self._logout(handler, parsed)
            if path == '/api/pos/shift/open':
                return self._shift_open(handler, parsed)
            if path == '/api/pos/shift/close':
                return self._shift_close(handler, parsed)
            if path == '/api/pos/cash':
                return self._cash(handler, parsed)
            if path == '/api/pos/cash/edit':
                return self._cash_edit(handler, parsed)
            if path == '/api/pos/cash/delete':
                return self._cash_delete(handler, parsed)
            if path == '/api/pos/dispatch':
                return self._dispatch(handler, parsed)
            if path == '/api/pos/checkout':
                return self._checkout(handler, parsed)
            if path == '/api/pos/sale/pay':
                return self._pay_pending_sale(handler, parsed)
            if path == '/api/pos/sale/cancel':
                return self._cancel(handler, parsed)
            if path == '/api/pos/sale/print':
                return self._reprint(handler, parsed)
            if path == '/api/pos/return/lookup':
                return self._return_lookup(handler, parsed)
            if path == '/api/pos/return':
                return self._create_return(handler, parsed)
            if path == '/api/pos/return/cancel':
                return self._cancel_return(handler, parsed)
            if path == '/api/pos/price/override':
                return self._price_override(handler, parsed)
            if path == '/api/pos/price/revert':
                return self._price_revert(handler, parsed)
            if path == '/api/pos/inventory/stock':
                return self._inventory_stock(handler, parsed)
            if path == '/api/pos/inventory/product':
                return self._inventory_create(handler, parsed)
            if path == '/api/pos/inventory/edit':
                return self._inventory_edit(handler, parsed)
            if path == '/api/pos/inventory/active':
                return self._inventory_active(handler, parsed)
            if path == '/api/pos/inventory/import':
                return self._inventory_import(handler, parsed)
            if path == '/api/pos/unit':
                return self._unit_create(handler, parsed)
            if path == '/api/pos/unit/active':
                return self._unit_active(handler, parsed)
            if path == '/api/pos/category':
                return self._category_create(handler, parsed)
            if path == '/api/pos/category/active':
                return self._category_active(handler, parsed)
            if path == '/api/pos/location':
                return self._location_create(handler, parsed)
            if path == '/api/pos/location/active':
                return self._location_active(handler, parsed)
            if path == '/api/pos/reason':
                return self._reason_create(handler, parsed)
            if path == '/api/pos/reason/active':
                return self._reason_active(handler, parsed)
            if path == '/api/pos/user/active':
                return self._user_active(handler, parsed)
            if path == '/api/pos/user/pin':
                return self._user_pin(handler, parsed)
            if path == '/api/pos/print/test':
                return self._test_print(handler, parsed)
            if path == '/api/pos/backup':
                return self._backup(handler, parsed)
            if path == '/api/pos/pin':
                return self._change_pin(handler, parsed)
            if path == '/api/pos/user':
                return self._add_user(handler, parsed)
            self._json(handler, {'ok': False, 'error': 'Ruta no encontrada.'}, 404)
        except ValueError as exc:
            self._json(handler, {'ok': False, 'error': str(exc)}, 400)
        except Exception as exc:
            _log_error(traceback.format_exc())
            self._json(handler, {'ok': False, 'error': f'Error interno: {exc}'}, 500)

    def _bootstrap(self, handler):
        data = self._body(handler)
        if pos_core.users_exist():
            self._json(handler, {'ok': False, 'error': 'La caja ya está configurada.'}, 409)
            return
        config = pos_core.load_config()
        min_len = int(config.get('security', {}).get('min_pin_length', 4) or 4)
        admin_name = (data.get('manager_name') or 'Administrador').strip()[:60]
        cashier_name = (data.get('cashier_name') or 'Cajero').strip()[:60]
        admin_pin = str(data.get('manager_pin') or '').strip()
        cashier_pin = str(data.get('cashier_pin') or '').strip()
        for label, pin in (('administrador', admin_pin), ('cajero', cashier_pin)):
            if not pin.isdigit() or not (min_len <= len(pin) <= 8):
                raise ValueError(f'El PIN del {label} debe tener entre {min_len} y 8 dígitos.')
        if admin_pin == cashier_pin:
            raise ValueError('Los PIN del administrador y del cajero deben ser diferentes.')
        # El primer usuario es administrador: es el único que puede administrar
        # usuarios, así que la caja nunca queda sin quién los gestione.
        admin_id = pos_core.create_user(admin_name, 'administrador', admin_pin)
        pos_core.create_user(cashier_name, 'cajero', cashier_pin)
        pos_core.audit(admin_id, 'configuracion_inicial', 'Se crearon los usuarios iniciales')
        token = pos_core.create_session(admin_id)
        self._json(handler, {
            'ok': True,
            'token': token,
            'user': pos_core.get_user(admin_id),
            'message': 'Caja configurada. Ya puedes abrir el turno.',
        })

    def _login(self, handler):
        data = self._body(handler)
        pin = str(data.get('pin') or '').strip()
        user = pos_core.find_user_by_pin(pin)
        if not user:
            pos_core.audit(None, 'login_fallido', 'PIN incorrecto')
            self._json(handler, {'ok': False, 'error': 'PIN incorrecto.'}, 401)
            return
        token = pos_core.create_session(user['id'])
        pos_core.audit(user['id'], 'entrada', f'Inició sesión {user["name"]}')
        self._json(handler, {'ok': True, 'token': token, 'user': user})

    def _logout(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        pos_core.delete_session(session['token'])
        pos_core.audit(session['user_id'], 'salida', 'Cerró sesión')
        self._json(handler, {'ok': True})

    def _shift_open(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_cashier_authority(session)
        data = self._body(handler)
        try:
            opening = float(data.get('opening_cash') or 0)
        except (TypeError, ValueError):
            raise ValueError('El fondo inicial debe ser un número.')
        if opening < 0:
            raise ValueError('El fondo inicial no puede ser negativo.')
        shift = pos_core.open_shift(session['user_id'], opening, data.get('notes') or '')
        pos_core.audit(session['user_id'], 'turno_abierto', f'turno={shift["id"]} fondo={opening}')
        self._json(handler, {'ok': True, 'shift': shift})

    def _shift_close(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_cashier_authority(session)
        data = self._body(handler)
        try:
            counted = float(data.get('counted_cash'))
        except (TypeError, ValueError):
            raise ValueError('Escribe el efectivo contado.')
        if not math.isfinite(counted):
            raise ValueError('El efectivo contado no es válido.')
        if counted < 0:
            raise ValueError('El efectivo contado no puede ser negativo.')
        shift = pos_core.get_open_shift()
        if not shift:
            raise ValueError('No hay un turno abierto.')
        denoms_in = data.get('denominations') or {}
        if not isinstance(denoms_in, dict):
            raise ValueError('El conteo por denominación no es válido.')
        allowed = {0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000}
        denominations = {}
        for raw_value, raw_qty in denoms_in.items():
            try:
                value = round(float(raw_value), 2)
                qty = int(raw_qty or 0)
            except (TypeError, ValueError):
                raise ValueError('El conteo por denominación no es válido.')
            if value not in allowed:
                raise ValueError(f'Denominación no válida: {raw_value}.')
            if qty < 0:
                raise ValueError('La cantidad de piezas no puede ser negativa.')
            if qty > MAX_DENOM_QTY:
                raise ValueError(f'Cantidad de piezas demasiado grande (máximo {MAX_DENOM_QTY}).')
            if qty > 0:
                denominations[value] = qty
        notes = str(data.get('notes') or '').strip()[:300]
        closed = pos_core.close_shift(
            shift['id'], counted, session['user_id'], notes, denominations,
        )
        # La impresión es un extra: si falla, el turno ya está cerrado y hay
        # que avisar igual, sin perder el corte (queda el texto y la copia en disco).
        local_print = bool(data.get('impresion_local'))
        printed = {'ok': False, 'ticket_text': '', 'error': '', 'printer': ''}
        try:
            if local_print:
                # El corte se imprime en la impresora de ESTA PC (agente local).
                report = pos_print.build_shift_report(closed, config)
                pos_print.save_ticket_copy(report, f'turno-{closed["id"]}', 'corte')
                printed = {'ok': False, 'ticket_text': report, 'error': '',
                           'printer': '(impresora local)', 'local': True}
            else:
                printed = pos_print.print_shift(closed, config)
        except Exception as exc:
            printed['error'] = str(exc)
        backup_path = ''
        try:
            backup_path = pos_core.backup('corte')
        except Exception:
            pass
        self._json(handler, {'ok': True, 'shift': closed, 'print': printed,
                             'backup': os.path.basename(backup_path) if backup_path else ''})

    def _cash_movements(self, handler, parsed):
        session, _ = self._auth(handler, parsed)
        if not session:
            return
        self._require_cashier_authority(session)
        shift = pos_core.get_open_shift()
        if not shift:
            self._json(handler, {'ok': True, 'shift': None, 'movements': []})
            return
        summary = pos_core.shift_summary(shift['id'])
        movements = pos_core.list_cash_movements(shift['id'])
        self._json(handler, {'ok': True, 'shift': summary, 'movements': movements})

    def _cash(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_cashier_authority(session)
        data = self._body(handler)
        shift = pos_core.get_open_shift()
        if not shift:
            raise ValueError('No hay un turno abierto.')
        kind = (data.get('kind') or '').strip()
        if kind not in ('ingreso', 'retiro'):
            raise ValueError('El movimiento debe ser ingreso o retiro.')
        try:
            amount = abs(float(data.get('amount') or 0))
        except (TypeError, ValueError):
            raise ValueError('El monto debe ser un número.')
        if amount <= 0:
            raise ValueError('El monto debe ser mayor a cero.')
        max_cash = float(config.get('rules', {}).get('max_cash_input', 50000) or 50000)
        if amount > max_cash:
            raise ValueError(f'El monto excede el máximo permitido (${max_cash:,.2f}).')
        reason = (data.get('reason') or '').strip()[:200]
        if not reason:
            raise ValueError('Escribe el motivo del movimiento.')
        signed = amount if kind == 'ingreso' else -amount
        pos_core.cash_movement(shift['id'], session['user_id'], kind, signed, reason)
        self._json(handler, {
            'ok': True,
            'shift': pos_core.shift_summary(shift['id']),
            'movements': pos_core.list_cash_movements(shift['id']),
            'today': pos_core.today_summary(),
        })

    def _cash_edit(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_cashier_authority(session)
        data = self._body(handler)
        shift = pos_core.get_open_shift()
        if not shift:
            raise ValueError('No hay un turno abierto.')
        try:
            movement_id = int(data.get('id'))
        except (TypeError, ValueError):
            raise ValueError('Movimiento inválido.')
        kind = (data.get('kind') or '').strip()
        if kind not in ('ingreso', 'retiro'):
            raise ValueError('El movimiento debe ser ingreso o retiro.')
        try:
            amount = abs(float(data.get('amount') or 0))
        except (TypeError, ValueError):
            raise ValueError('El monto debe ser un número.')
        if amount <= 0:
            raise ValueError('El monto debe ser mayor a cero.')
        max_cash = float(config.get('rules', {}).get('max_cash_input', 50000) or 50000)
        if amount > max_cash:
            raise ValueError(f'El monto excede el máximo permitido (${max_cash:,.2f}).')
        reason = (data.get('reason') or '').strip()[:200]
        if not reason:
            raise ValueError('Escribe el motivo del movimiento.')
        signed = amount if kind == 'ingreso' else -amount
        pos_core.update_cash_movement(
            movement_id, shift['id'], session['user_id'], kind, signed, reason,
        )
        self._json(handler, {
            'ok': True,
            'shift': pos_core.shift_summary(shift['id']),
            'movements': pos_core.list_cash_movements(shift['id']),
            'today': pos_core.today_summary(),
        })

    def _cash_delete(self, handler, parsed):
        session, _ = self._auth(handler, parsed)
        if not session:
            return
        self._require_cashier_authority(session)
        data = self._body(handler)
        shift = pos_core.get_open_shift()
        if not shift:
            raise ValueError('No hay un turno abierto.')
        try:
            movement_id = int(data.get('id'))
        except (TypeError, ValueError):
            raise ValueError('Movimiento inválido.')
        pos_core.delete_cash_movement(movement_id, shift['id'], session['user_id'])
        self._json(handler, {
            'ok': True,
            'shift': pos_core.shift_summary(shift['id']),
            'movements': pos_core.list_cash_movements(shift['id']),
            'today': pos_core.today_summary(),
        })

    def _pending_sales(self, handler, parsed):
        session, _ = self._auth(handler, parsed)
        if not session:
            return
        self._json(handler, {
            'ok': True,
            'pending_sales': pos_core.list_pending_sales(),
        })

    def _validate_cart_items(self, items_in, config):
        if not isinstance(items_in, list) or not items_in:
            raise ValueError('El carrito está vacío.')

        rules = config.get('rules', {})
        max_qty = float(rules.get('max_qty_per_line', 999) or 999)
        allow_zero = bool(rules.get('allow_zero_stock', True))
        overrides = pos_core.active_price_overrides()

        merged = {}
        for raw in items_in:
            try:
                key = raw.get('product_key')
                if key is None:
                    key = raw.get('legacy_id')
                qty = float(raw.get('qty') or 0)
            except (TypeError, ValueError):
                raise ValueError('Hay una línea inválida en el carrito.')
            if not math.isfinite(qty):
                raise ValueError('Hay una cantidad inválida en el carrito.')
            qty = round(qty, 2)
            if qty < 0.01:
                raise ValueError('La cantidad mínima por producto es 0.01.')
            merged[str(key)] = merged.get(str(key), 0) + qty

        lines = []
        for key, qty in merged.items():
            if qty > max_qty:
                raise ValueError(f'La cantidad máxima por producto es {int(max_qty)}.')
            product = self.catalog.get(key)
            if not product:
                raise ValueError('Un producto del carrito ya no existe en el catálogo. Quítalo y vuelve a escanearlo.')
            reason = blocked_reason(product, config)
            if reason:
                raise ValueError(f'{product["name"]}: {reason}.')
            if not allow_zero and float(product.get('stock') or 0) <= 0:
                raise ValueError(f'{product["name"]}: sin existencias.')
            price, is_override, _original = self._effective_price(product, overrides)
            lines.append({
                'legacy_item_id': product.get('legacy_id'),
                'product_key': str(product.get('key')) if self.catalog.source == 'propio' else None,
                'sku': product.get('sku'),
                'barcode': product.get('barcode'),
                'name': product['name'],
                'unit': product.get('unit_symbol') or 'pza',
                'unit_price': price,
                'cost_price': float(product.get('cost') or 0),
                'qty': qty,
                'price_override': is_override,
            })
        return lines

    def _validate_payments(self, payments_in, subtotal, config):
        if not isinstance(payments_in, list):
            raise ValueError('Los pagos no son válidos.')
        payments = []
        for raw in payments_in:
            method = str(raw.get('method') or '').strip().lower()
            if method not in ALLOWED_METHODS:
                raise ValueError('Forma de pago no válida.')
            try:
                amount = pos_core.money(raw.get('amount') or 0)
            except (TypeError, ValueError):
                raise ValueError('El monto del pago no es válido.')
            if amount <= 0:
                continue
            payments.append({
                'method': method,
                'amount': amount,
                'reference': (str(raw.get('reference') or '').strip())[:60] or None,
            })
        if not payments:
            raise ValueError('Falta registrar el pago.')

        rules = config.get('rules', {})
        paid = pos_core.money(sum(p['amount'] for p in payments))
        if paid + 0.005 < subtotal:
            raise ValueError(f'El pago es insuficiente: faltan {pos_print.fmt_money(subtotal - paid)}.')
        change = pos_core.money(paid - subtotal)
        cash = pos_core.money(sum(p['amount'] for p in payments if p['method'] == 'efectivo'))
        other = pos_core.money(sum(p['amount'] for p in payments if p['method'] != 'efectivo'))
        if other > subtotal + 0.005:
            raise ValueError('El pago con tarjeta o transferencia no puede exceder el total.')
        if change > cash + 0.005:
            raise ValueError('El cambio no puede ser mayor al efectivo recibido.')
        max_cash = float(rules.get('max_cash_input', 50000) or 50000)
        if cash > max_cash:
            raise ValueError(f'El efectivo recibido excede el máximo permitido ({pos_print.fmt_money(max_cash)}).')
        return payments, change

    def _dispatch(self, handler, parsed):
        """Despacha un carrito como pendiente de cobro en Caja (no mueve efectivo)."""
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        shift = pos_core.get_open_shift()
        if not shift:
            raise ValueError('No hay un turno abierto en Caja.')

        lines = self._validate_cart_items(data.get('items') or [], config)
        customer = (str(data.get('customer_name') or '').strip())[:80]
        customer_id = data.get('customer_id')
        note = (str(data.get('note') or '').strip())[:200]
        sale = pos_core.create_pending_sale(
            session['user_id'], shift['id'], lines, customer, note, customer_id,
        )
        for line in lines:
            key = line.get('product_key')
            if key not in (None, ''):
                try:
                    pos_core.notify_negative_stock(int(key), 'venta')
                except (TypeError, ValueError):
                    pass
        auto_print = session.get('role') not in ('gerente', 'administrador')
        printed = self._ticket_print(sale, config, data, auto_print=auto_print)
        self._json(handler, {
            'ok': True,
            'sale': pos_core.get_sale(sale['id']),
            'ticket_token': pos_core.ticket_token(sale['id']),
            'ticket_text': printed['ticket_text'],
            'ticket_lines': printed['lines'],
            'printed': printed['ok'],
            'print_error': printed['error'],
            'printer': printed['printer'],
            'change': 0,
            'pending_sales': pos_core.list_pending_sales(),
        })

    def _pay_pending_sale(self, handler, parsed):
        """Cobra en Caja una venta previamente despachada ('pending')."""
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_cashier_authority(session)
        data = self._body(handler)
        shift = pos_core.get_open_shift()
        if not shift:
            raise ValueError('Abre el turno antes de cobrar.')
        try:
            sale_id = int(data.get('sale_id'))
        except (TypeError, ValueError):
            raise ValueError('Ticket inválido.')
        existing = pos_core.get_sale(sale_id)
        if not existing:
            raise ValueError('El ticket no existe.')
        if existing['status'] == 'completed':
            raise ValueError('Este ticket ya fue cobrado.')
        if existing['status'] == 'cancelled':
            raise ValueError('Este ticket fue cancelado.')
        if existing['status'] != 'pending':
            raise ValueError('Este ticket no está pendiente de cobro.')

        subtotal = pos_core.money(existing['total'])
        payments, change = self._validate_payments(data.get('payments') or [], subtotal, config)
        sale = pos_core.complete_pending_sale(
            sale_id, session['user_id'], shift['id'], payments,
        )
        auto_print = session.get('role') not in ('gerente', 'administrador')
        printed = self._ticket_print(sale, config, data, auto_print=auto_print)
        self._json(handler, {
            'ok': True,
            'sale': sale,
            'ticket_token': pos_core.ticket_token(sale['id']),
            'ticket_text': printed['ticket_text'],
            'ticket_lines': printed['lines'],
            'printed': printed['ok'],
            'print_error': printed['error'],
            'printer': printed['printer'],
            'change': change,
            'pending_sales': pos_core.list_pending_sales(),
        })

    def _checkout(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        # Si quien envía /checkout es un vendedor ('cajero'), el servidor fuerza
        # el flujo de despacho (pending) para que nunca sume efectivo sin pasar por Caja.
        if session.get('role') not in ('gerente', 'administrador'):
            return self._dispatch(handler, parsed)
        data = self._body(handler)
        shift = pos_core.get_open_shift()
        if not shift:
            raise ValueError('Abre el turno antes de cobrar.')

        lines = self._validate_cart_items(data.get('items') or [], config)
        subtotal = pos_core.money(sum(pos_core.money(l['unit_price'] * l['qty']) for l in lines))
        payments, change = self._validate_payments(data.get('payments') or [], subtotal, config)

        customer = (str(data.get('customer_name') or '').strip())[:80]
        customer_id = data.get('customer_id')
        note = (str(data.get('note') or '').strip())[:200]
        sale = pos_core.create_sale(
            session['user_id'], shift['id'], lines, payments, customer, note, customer_id,
        )
        # Revisa el stock resultante: si la venta dejó un producto en negativo,
        # queda el aviso pendiente hasta que se corrija el inventario.
        for line in lines:
            key = line.get('product_key')
            if key not in (None, ''):
                try:
                    pos_core.notify_negative_stock(int(key), 'venta')
                except (TypeError, ValueError):
                    pass
        auto_print = session.get('role') not in ('gerente', 'administrador')
        printed = self._ticket_print(sale, config, data, auto_print=auto_print)
        self._json(handler, {
            'ok': True,
            'sale': pos_core.get_sale(sale['id']),
            'ticket_token': pos_core.ticket_token(sale['id']),
            'ticket_text': printed['ticket_text'],
            'ticket_lines': printed['lines'],
            'printed': printed['ok'],
            'print_error': printed['error'],
            'printer': printed['printer'],
            'change': change,
            'pending_sales': pos_core.list_pending_sales(),
        })

    def _ticket_print(self, sale, config, data, auto_print=True):
        """Prepara el ticket de una venta.

        Si el navegador de esa PC tiene el agente de impresión local, el
        servidor genera los bytes ESC/POS completos (pre-renderizados) y los
        envía en base64 para que el agente local solo llame a print_raw().
        Así el diseño del ticket siempre viene del servidor, independientemente
        de la versión de pos_print.py instalada en la PC cliente.
        """
        lines = pos_print.build_ticket_lines(sale, config)
        width = int(config.get('printer', {}).get('width', 42) or 42)
        text = pos_print.render_ticket_text(lines, width)
        if not auto_print:
            pos_print.save_ticket_copy(text, str(sale['folio']).replace('/', '-'), 'ticket')
            return {
                'ok': False,
                'error': '',
                'printer': '',
                'ticket_text': text,
                'lines': [],
                'escpos_b64': None,
                'local': False,
            }
        if data.get('impresion_local'):
            pos_print.save_ticket_copy(text, str(sale['folio']).replace('/', '-'), 'ticket')
            # Pre-renderizar los bytes ESC/POS en el servidor
            raw = pos_print.escpos_lines(lines, config, cut=True,
                                         drawer=bool(data.get('drawer', False)))
            return {
                'ok': False,
                'error': '',
                'printer': '',
                'ticket_text': text,
                'lines': [[k, t] for k, t in lines],
                'escpos_b64': base64.b64encode(raw).decode('ascii'),
                'local': True,
            }
        # Los despachos ('pending') de cajeros en red nunca deben imprimirse en la
        # impresora de la Caja central si su agente USB local no está activo.
        if sale.get('status') == 'pending':
            pos_print.save_ticket_copy(text, str(sale['folio']).replace('/', '-'), 'ticket')
            raw = pos_print.escpos_lines(lines, config, cut=True, drawer=False)
            return {
                'ok': False,
                'error': 'Sin impresora USB local detectada en esta PC.',
                'printer': '',
                'ticket_text': text,
                'lines': [[k, t] for k, t in lines],
                'escpos_b64': base64.b64encode(raw).decode('ascii'),
                'local': True,
            }
        printed = pos_print.print_sale(sale, config)
        printed['lines'] = [[k, t] for k, t in lines]
        printed['escpos_b64'] = None
        printed['local'] = False
        return printed

    def _cancel(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        try:
            sale_id = int(data.get('sale_id'))
        except (TypeError, ValueError):
            raise ValueError('Venta inválida.')
        reason = (str(data.get('reason') or '').strip())[:300]
        if not reason:
            raise ValueError('Escribe el motivo de la cancelación.')

        authorizer = session
        if config.get('security', {}).get('manager_required_for_cancel', True):
            pin = str(data.get('manager_pin') or '').strip()
            if not pin:
                raise ValueError('Se requiere el PIN del gerente para cancelar.')
            found = pos_core.find_user_by_pin(pin)
            if not found or found['role'] not in ('gerente', 'administrador'):
                pos_core.audit(session['user_id'], 'cancelacion_denegada', f'venta={sale_id} PIN inválido')
                raise ValueError('PIN de gerente o administrador incorrecto.')
            authorizer = found

        sale = pos_core.cancel_sale(sale_id, authorizer['id'], reason)
        self._json(handler, {'ok': True, 'sale': sale})

    def _reprint(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        try:
            sale_id = int(data.get('sale_id'))
        except (TypeError, ValueError):
            raise ValueError('Venta inválida.')
        sale = pos_core.get_sale(sale_id)
        if not sale:
            raise ValueError('La venta no existe.')
        printed = self._ticket_print(sale, config, data, auto_print=True)
        self._json(handler, {
            'ok': True,
            'ticket_token': pos_core.ticket_token(sale_id),
            'printed': printed['ok'],
            'print_error': printed['error'],
            'ticket_text': printed['ticket_text'],
            'ticket_lines': printed['lines'],
        })

    def _test_print(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        if data.get('impresion_local'):
            # La prueba sale en la impresora de ESTA PC (agente local).
            lines = pos_print.build_test_lines(config)
            width = int(config.get('printer', {}).get('width', 42) or 42)
            self._json(handler, {
                'ok': True, 'error': '', 'printer': '(impresora local)',
                'ticket_text': pos_print.render_ticket_text(lines, width),
                'ticket_lines': [[k, t] for k, t in lines],
                'local': True,
            })
            return
        printed = pos_print.print_test(config)
        self._json(handler, {'ok': printed['ok'], 'error': printed['error'],
                             'printer': printed['printer'], 'ticket_text': printed['ticket_text']})

    def _backup(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        if session['role'] not in ('gerente', 'administrador'):
            self._json(handler, {'ok': False, 'error': 'Solo el gerente o el administrador puede respaldar.'}, 403)
            return
        path = pos_core.backup('manual')
        pos_core.audit(session['user_id'], 'respaldo', os.path.basename(path))
        self._json(handler, {'ok': True, 'path': path})

    def _change_pin(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        current = str(data.get('current_pin') or '').strip()
        new_pin = str(data.get('new_pin') or '').strip()
        user = pos_core.find_user_by_pin(current)
        if not user or user['id'] != session['user_id']:
            raise ValueError('El PIN actual es incorrecto.')
        min_len = int(config.get('security', {}).get('min_pin_length', 4) or 4)
        if not new_pin.isdigit() or not (min_len <= len(new_pin) <= 8):
            raise ValueError(f'El nuevo PIN debe tener entre {min_len} y 8 dígitos.')
        pos_core.change_pin(session['user_id'], new_pin)
        pos_core.audit(session['user_id'], 'pin_cambiado', '')
        token = pos_core.create_session(session['user_id'])
        self._json(handler, {'ok': True, 'token': token})

    # ------------------------------------------------------------ devoluciones
    def _return_lookup(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        sale = None
        try:
            if data.get('sale_id'):
                sale = pos_core.sale_with_remaining(int(data['sale_id']))
            else:
                found = pos_core.sale_by_folio(data.get('folio') or '')
                sale = pos_core.sale_with_remaining(found['id']) if found else None
        except (TypeError, ValueError):
            raise ValueError('Ticket inválido.')
        if not sale:
            self._json(handler, {'ok': False, 'error': 'No se encontró ese ticket.'}, 404)
            return
        self._json(handler, {'ok': True, 'sale': sale})

    def _create_return(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        try:
            sale_id = int(data.get('sale_id'))
        except (TypeError, ValueError):
            raise ValueError('Venta inválida.')
        sale = pos_core.get_sale(sale_id)
        if not sale:
            raise ValueError('La venta no existe.')

        authorizer = None
        if config.get('security', {}).get('manager_required_for_return', True):
            authorizer = self._require_manager(data.get('manager_pin'))

        method = (data.get('method') or '').strip().lower()
        if not method:
            method = sale['payments'][0]['method'] if sale.get('payments') else 'efectivo'
        if method not in ALLOWED_METHODS:
            raise ValueError('Forma de devolución no válida.')

        shift = pos_core.get_open_shift()
        shift_id = shift['id'] if shift else sale['shift_id']
        record = pos_core.create_return(
            session['user_id'], shift_id, sale_id, data.get('items') or [],
            method, (data.get('reason') or '').strip(), data.get('note') or '',
        )
        if authorizer:
            pos_core.audit(authorizer['id'], 'devolucion_autorizada',
                           f'{record["folio"]} autorizó {authorizer["name"]}')
        printed = pos_print.print_return(record, sale, config, do_print=not data.get('impresion_local'))
        self._json(handler, {
            'ok': True,
            'return': record,
            'ticket_text': printed['ticket_text'],
            'ticket_lines': printed['lines'],
            'printed': printed['ok'],
            'print_error': printed['error'],
        })

    def _cancel_return(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        try:
            return_id = int(data.get('return_id'))
        except (TypeError, ValueError):
            raise ValueError('Devolución inválida.')
        if config.get('security', {}).get('manager_required_for_return', True):
            self._require_manager(data.get('manager_pin'))
        record = pos_core.cancel_return(return_id, session['user_id'],
                                        (data.get('reason') or '').strip())
        self._json(handler, {'ok': True, 'return': record})

    # ------------------------------------------------------------ precios de emergencia
    def _price_override(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        manager = self._require_manager(data.get('manager_pin'))
        try:
            product_key = str(data.get('product_key') or data.get('legacy_id'))
            price = float(data.get('price'))
        except (TypeError, ValueError):
            raise ValueError('Datos de precio inválidos.')
        product = self.catalog.get(product_key)
        if not product:
            raise ValueError('El producto no existe en el catálogo.')
        hours = config.get('rules', {}).get('override_hours', 24)
        pos_core.set_price_override(product.get('key'), price, (data.get('reason') or '').strip(),
                                    manager['id'], product['price'], hours)
        self._json(handler, {
            'ok': True,
            'name': product['name'],
            'price': pos_core.money(price),
            'original_price': pos_core.money(product['price']),
        })

    def _price_revert(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        data = self._body(handler)
        manager = self._require_manager(data.get('manager_pin'))
        try:
            product_key = str(data.get('product_key') or data.get('legacy_id'))
        except (TypeError, ValueError):
            raise ValueError('Producto inválido.')
        pos_core.revert_price_override(product_key, manager['id'])
        self._json(handler, {'ok': True})

    def _prices(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        rows = pos_core.list_price_overrides()
        for row in rows:
            product = self.catalog.get(row['product_key'])
            row['name'] = product['name'] if product else 'Producto retirado del catálogo'
            row['catalog_price'] = product['price'] if product else None
            row['product_key'] = row['product_key']
        self._json(handler, {'ok': True, 'overrides': rows})

    def _export(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        day = (params.get('date') or [''])[0].strip() or pos_core.today_text()
        try:
            datetime.datetime.strptime(day, '%Y-%m-%d')
        except ValueError:
            raise ValueError('La fecha debe tener el formato AAAA-MM-DD.')
        path, sales, returns = pos_core.export_sales_csv(day)
        pos_core.audit(session['user_id'], 'exportacion',
                       f'{day} ventas={sales} devoluciones={returns}')
        self._json(handler, {'ok': True, 'path': path, 'sales': sales, 'returns': returns})

    # ------------------------------------------------------------ inventario
    def _require_inventory_role(self, session):
        if session['role'] not in ('administrador', 'gerente'):
            raise ValueError('Sólo el administrador o el gerente puede modificar el inventario.')

    def _inventory_movements(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        key = (params.get('product_key') or [''])[0].strip()
        product_id = None
        presentation = None
        if key:
            product = self.catalog.get(key)
            if not product and self.catalog.source == 'propio' and key.isdigit():
                # Los productos inactivos no están en el catálogo de ventas,
                # pero el administrador puede abrirlos desde Inventario.
                product = pos_core.get_product(int(key))
            if product:
                if self.catalog.source == 'propio':
                    product_id = int(product.get('key') or product['id'])
                    base_id = product.get('base_id') or product.get('base_product_id')
                    if base_id:
                        # El empaque mueve existencias del base: mostramos sus
                        # movimientos para que se vea el detalle real.
                        presentation = {
                            'base_id': base_id,
                            'base_name': product.get('base_name'),
                            'base_qty': product.get('base_qty'),
                        }
                        product_id = int(base_id)
                if product_id is None:
                    row = pos_core.product_by_legacy(int(key)) if str(key).isdigit() else None
                    product_id = row['id'] if row else None
        self._json(handler, {
            'ok': True,
            'presentation': presentation,
            'movements': pos_core.product_movements(product_id, 60),
        })

    def _inventory_stock(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        try:
            qty = float(data.get('qty'))
            key = str(data.get('product_key'))
        except (TypeError, ValueError):
            raise ValueError('Datos de movimiento inválidos.')
        product = self.catalog.get(key)
        if not product:
            raise ValueError('El producto no existe en el catálogo.')
        if self.catalog.source != 'propio':
            raise ValueError('Primero hay que importar el inventario del catálogo.')
        kind = 'alta' if qty > 0 else 'baja'
        product = pos_core.set_stock(session['user_id'], int(product['key']), qty,
                                     (data.get('reason') or '').strip(), kind)
        self._json(handler, {'ok': True, 'product': product})

    def _inventory_create(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        product = pos_core.create_product(session['user_id'], data)
        self._json(handler, {'ok': True, 'product': product})

    def _inventory_edit(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        key = str(data.get('product_key'))
        product = self.catalog.get(key)
        if not product:
            raise ValueError('El producto no existe en el catálogo.')
        if self.catalog.source != 'propio':
            raise ValueError('Primero hay que importar el inventario del catálogo.')
        changes = data.get('changes') or {}
        updated = pos_core.update_product(session['user_id'], int(product['key']), changes,
                                          (data.get('reason') or '').strip())
        self._json(handler, {'ok': True, 'product': updated})

    def _inventory_active(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        try:
            product_id = int(data.get('product_key'))
        except (TypeError, ValueError):
            self._json(handler, {'ok': False, 'error': 'Producto inválido.'}, 400)
            return
        if self.catalog.source != 'propio':
            self._json(handler, {
                'ok': False,
                'error': 'Primero hay que importar el inventario del catálogo.',
            }, 400)
            return
        # El catálogo de ventas sólo contiene productos activos. Para reactivar
        # una baja, leer el registro directamente del inventario propio.
        product = pos_core.get_product(product_id)
        if not product:
            self._json(handler, {'ok': False, 'error': 'El producto no existe.'}, 404)
            return
        updated = pos_core.set_product_active(session['user_id'], product_id,
                                              bool(data.get('active')),
                                              (data.get('reason') or '').strip())
        self._json(handler, {'ok': True, 'product': updated})

    def _inventory_import(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        catalog_path = data.get('path') or os.path.join(
            pos_core.BASE_DIR, 'Historico', 'Auxiliares', 'inventario_moderno.db'
        )
        if not os.path.exists(catalog_path):
            legacy = os.path.join(pos_core.BASE_DIR, 'inventario_moderno.db')
            catalog_path = legacy if os.path.exists(legacy) else catalog_path
        if not os.path.exists(catalog_path):
            raise ValueError('No se encontró inventario_moderno.db. Ejecuta la sincronización primero.')
        conn = sqlite3.connect(catalog_path, timeout=10)
        try:
            rows = conn.execute(
                """SELECT sku, barcode, item_number, alt_codes, name, category, location,
                          sale_price, cost_price, stock, units_sold_period, last_sold_at, legacy_item_id
                   FROM products"""
            ).fetchall()
        finally:
            conn.close()
        data_rows = [dict(zip(
            ['sku', 'barcode', 'item_number', 'alt_codes', 'name', 'category', 'location',
             'sale_price', 'cost_price', 'stock', 'units_sold_period', 'last_sold_at', 'legacy_item_id'],
            row)) for row in rows]
        result = pos_core.import_products(
            data_rows, session['user_id'],
            replace=bool(data.get('replace')),
            reset_stock=bool(data.get('reset_stock')),
        )
        self._json(handler, {'ok': True, **result})

    # ------------------------------------------------------------ unidades
    def _units(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        todas = (params.get('todas') or ['0'])[0] in ('1', 'true', 'si')
        self._json(handler, {'ok': True, 'units': pos_core.list_units(todas)})

    def _unit_create(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        unit = pos_core.create_unit(
            session['user_id'], data.get('name'), data.get('symbol')
        )
        self._json(handler, {
            'ok': True, 'unit': unit, 'units': pos_core.list_units(True),
        })

    def _unit_active(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        try:
            unit_id = int(data.get('unit_id'))
        except (TypeError, ValueError):
            raise ValueError('Unidad inválida.')
        unit = pos_core.set_unit_active(
            session['user_id'], unit_id, bool(data.get('active'))
        )
        self._json(handler, {
            'ok': True, 'unit': unit, 'units': pos_core.list_units(True),
        })

    def _inventory_next_sku(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        self._json(handler, {'ok': True, 'sku': pos_core.next_manual_sku()})

    # ------------------------------------------------------------ categorías y ubicaciones
    def _categories(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        todas = (params.get('todas') or ['0'])[0] in ('1', 'true', 'si')
        self._json(handler, {'ok': True, 'categories': pos_core.list_categories(todas)})

    def _category_create(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        cat = pos_core.create_category(session['user_id'], data.get('name'))
        self._json(handler, {
            'ok': True, 'category': cat, 'categories': pos_core.list_categories(True),
        })

    def _category_active(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        try:
            cat_id = int(data.get('category_id'))
        except (TypeError, ValueError):
            raise ValueError('Categoría inválida.')
        cat = pos_core.set_category_active(
            session['user_id'], cat_id, bool(data.get('active'))
        )
        self._json(handler, {
            'ok': True, 'category': cat, 'categories': pos_core.list_categories(True),
        })

    def _locations(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        todas = (params.get('todas') or ['0'])[0] in ('1', 'true', 'si')
        self._json(handler, {'ok': True, 'locations': pos_core.list_locations(todas)})

    def _location_create(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        loc = pos_core.create_location(session['user_id'], data.get('name'))
        self._json(handler, {
            'ok': True, 'location': loc, 'locations': pos_core.list_locations(True),
        })

    def _location_active(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        try:
            loc_id = int(data.get('location_id'))
        except (TypeError, ValueError):
            raise ValueError('Ubicación inválida.')
        loc = pos_core.set_location_active(
            session['user_id'], loc_id, bool(data.get('active'))
        )
        self._json(handler, {
            'ok': True, 'location': loc, 'locations': pos_core.list_locations(True),
        })

    def _reasons(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        todas = (params.get('todas') or ['0'])[0] in ('1', 'true', 'si')
        self._json(handler, {'ok': True, 'reasons': pos_core.list_reasons(todas)})

    def _reason_create(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        reason = pos_core.create_reason(session['user_id'], data.get('name'))
        self._json(handler, {
            'ok': True, 'reason': reason, 'reasons': pos_core.list_reasons(True),
        })

    def _reason_active(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        self._require_inventory_role(session)
        data = self._body(handler)
        try:
            reason_id = int(data.get('reason_id'))
        except (TypeError, ValueError):
            raise ValueError('Motivo inválido.')
        reason = pos_core.set_reason_active(
            session['user_id'], reason_id, bool(data.get('active'))
        )
        self._json(handler, {
            'ok': True, 'reason': reason, 'reasons': pos_core.list_reasons(True),
        })

    # ------------------------------------------------------------ usuarios
    def _user_active(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        if session['role'] != 'administrador':
            self._json(handler, {'ok': False, 'error': 'Sólo el administrador puede administrar usuarios.'}, 403)
            return
        data = self._body(handler)
        try:
            user_id = int(data.get('user_id'))
        except (TypeError, ValueError):
            raise ValueError('Usuario inválido.')
        if user_id == session['user_id']:
            raise ValueError('No puedes desactivar tu propia cuenta.')
        pos_core.set_user_active(user_id, bool(data.get('active')))
        self._json(handler, {'ok': True, 'users': pos_core.list_users()})

    def _user_pin(self, handler, parsed):
        """Sólo el administrador establece el PIN de otro usuario."""
        session, config = self._auth(handler, parsed)
        if not session:
            return
        if session['role'] != 'administrador':
            self._json(handler, {'ok': False, 'error': 'Sólo el administrador puede asignar PIN.'}, 403)
            return
        data = self._body(handler)
        try:
            user_id = int(data.get('user_id'))
            pin = str(data.get('pin') or '').strip()
        except (TypeError, ValueError):
            raise ValueError('Datos inválidos.')
        min_len = int(config.get('security', {}).get('min_pin_length', 4) or 4)
        if not pin.isdigit() or not (min_len <= len(pin) <= 8):
            raise ValueError(f'El PIN debe tener entre {min_len} y 8 dígitos.')
        if pos_core.find_user_by_pin(pin):
            raise ValueError('Ese PIN ya está en uso. Elige otro.')
        target = pos_core.get_user(user_id)
        if not target:
            raise ValueError('El usuario no existe.')
        pos_core.change_pin(user_id, pin)
        pos_core.audit(session['user_id'], 'pin_asignado', f'usuario={user_id} por {session["name"]}')
        self._json(handler, {'ok': True})

    def _inventory_products(self, handler, parsed):
        """Búsqueda en el inventario propio, incluidos los productos dados de baja."""
        session, config = self._auth(handler, parsed)
        if not session:
            return
        params = parse_qs(parsed.query)
        q = (params.get('q') or [''])[0].strip()
        wants_inactive = (params.get('inactive') or ['0'])[0] in ('1', 'true', 'si')
        include_inactive = wants_inactive and session['role'] == 'administrador'
        conn = pos_core.connect()
        try:
            columns = """p.id, p.legacy_item_id, p.sku, p.barcode, p.item_number,
                         p.alt_codes, p.name, p.category, p.location, p.unit,
                         p.unit_symbol, p.sale_price, p.cost_price, p.stock, p.active,
                         p.base_product_id AS base_id, p.base_qty,
                         b.stock AS base_stock, b.active AS base_active,
                         b.name AS base_name"""
            join = 'FROM products p LEFT JOIN products b ON b.id=p.base_product_id'
            if q:
                like = f"%{q}%"
                rows = conn.execute(
                    f"""SELECT {columns} {join}
                        WHERE (p.name LIKE ? OR p.sku LIKE ? OR p.barcode LIKE ?
                               OR p.item_number LIKE ? OR p.alt_codes LIKE ?)
                          AND (? OR p.active=1)
                        ORDER BY p.active DESC, p.name LIMIT 60""",
                    (like, like, like, like, like, 1 if include_inactive else 0),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT {columns} {join}
                        WHERE (? OR p.active=1)
                        ORDER BY p.active DESC, p.name LIMIT 60""",
                    (1 if include_inactive else 0,),
                ).fetchall()
        finally:
            conn.close()
        out = []
        for row in rows:
            item = dict(row)
            item['key'] = item['id']
            item['price'] = float(item.pop('sale_price') or 0)
            item['cost'] = float(item.pop('cost_price') or 0)
            item['stock'] = float(item.get('stock') or 0)
            if item.get('base_id') and item.get('base_stock') is not None:
                item['stock'] = round(
                    float(item['base_stock']) / float(item.get('base_qty') or 1), 3)
            item['legacy_id'] = None
            item['alt_codes'] = [
                code.strip() for code in (item.get('alt_codes') or '').split('|')
                if code.strip()
            ]
            out.append(item)
        self._json(handler, {'ok': True, 'results': out})

    def _add_user(self, handler, parsed):
        session, config = self._auth(handler, parsed)
        if not session:
            return
        if session['role'] != 'administrador':
            self._json(handler, {'ok': False, 'error': 'Solo el administrador puede crear usuarios.'}, 403)
            return
        data = self._body(handler)
        name = (str(data.get('name') or '').strip())[:60]
        role = (str(data.get('role') or 'cajero').strip().lower())
        pin = str(data.get('pin') or '').strip()
        if not name:
            raise ValueError('Escribe el nombre.')
        if role not in ('gerente', 'administrador', 'cajero'):
            raise ValueError('El rol debe ser gerente, administrador o cajero.')
        min_len = int(config.get('security', {}).get('min_pin_length', 4) or 4)
        if not pin.isdigit() or not (min_len <= len(pin) <= 8):
            raise ValueError(f'El PIN debe tener entre {min_len} y 8 dígitos.')
        if pos_core.find_user_by_pin(pin):
            raise ValueError('Ese PIN ya está en uso. Elige otro.')
        max_users = int(config.get('security', {}).get('max_users', 8) or 8)
        user_id = pos_core.create_user(name, role, pin, max_users=max_users)
        pos_core.audit(session['user_id'], 'usuario_creado', f'{name} ({role})')
        self._json(handler, {'ok': True, 'user': pos_core.get_user(user_id),
                             'users': pos_core.list_users()})


# ---------------------------------------------------------------- ticket HTML

def ticket_html(sale, config, auto_print=False):
    lines = pos_print.build_ticket_lines(sale, config)
    store = html.escape((config.get('store', {}).get('name') or 'La Colonial'))
    auto = '<script>window.addEventListener("load",function(){setTimeout(function(){window.print();},400);});</script>' if auto_print else ''
    body = []
    buffer = []

    def flush():
        if buffer:
            body.append('<pre class="tl">' + html.escape('\n'.join(buffer)) + '</pre>')
            buffer.clear()

    for kind, text in lines:
        if kind == 'logo':
            flush()
            logo_src = '/pos/logo.png'
            if os.path.exists(pos_print.LOGO_PATH):
                import base64
                with open(pos_print.LOGO_PATH, 'rb') as fh:
                    logo_src = 'data:image/png;base64,' + base64.b64encode(fh.read()).decode('ascii')
            body.append(f'<div class="tlogo"><img src="{logo_src}" alt="LA COLONIAL"></div>')
        elif kind == 'store_name':
            flush()
            body.append(f'<div class="tstore">{html.escape(text)}</div>')
        elif kind == 'text':
            buffer.append(text)
        elif kind == 'total_label':
            flush()
            body.append(f'<div class="tlabel">{html.escape(text)}</div>')
        elif kind == 'total_amount':
            # El importe se hace más grande, pero se reduce si es muy largo
            # para que siempre quepa dentro de los 80 mm.
            size = len(text.strip())
            if size <= 9:
                amount_class = 'tamount'
            elif size <= 13:
                amount_class = 'tamount mid'
            else:
                amount_class = 'tamount small'
            body.append(f'<div class="{amount_class}">{html.escape(text)}</div>')
    flush()
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket {html.escape(sale['folio'])} · {store}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 18px; background: #eef1f5; font-family: 'Segoe UI', system-ui, sans-serif; }}
  .bar {{ max-width: 420px; margin: 0 auto 12px; display: flex; gap: 8px; justify-content: space-between; align-items: center; }}
  .bar a, .bar button {{ font: inherit; border: 1px solid #c9d1dc; background: #fff; color: #1f2937;
    padding: 8px 14px; border-radius: 8px; cursor: pointer; text-decoration: none; }}
  .bar button.primary {{ background: #0f5132; border-color: #0f5132; color: #fff; }}
  .paper {{ width: 80mm; margin: 0 auto; background: #fff; padding: 8px 10px; box-shadow: 0 2px 14px rgba(0,0,0,.14);
    font-family: 'Consolas', 'Courier New', monospace; font-size: 12px; line-height: 1.3; color: #000; }}
  pre.tl {{ margin: 0; white-space: pre-wrap; word-break: break-word; font: inherit; }}
  .tlogo {{ text-align: center; margin: 4px 0 6px; }}
  .tlogo img {{ max-width: 44mm; height: auto; display: inline-block; }}
  .tstore {{ text-align: center; font-weight: 800; font-size: 16px; letter-spacing: .04em; margin: 2px 0 2px; }}
  .tlabel {{ text-align: center; font-weight: 800; font-size: 19px; letter-spacing: .30em;
    margin: 10px 0 4px; }}
  .tamount {{ text-align: center; font-weight: 800; font-size: 44px; line-height: 1.1;
    margin: 0 0 12px; letter-spacing: .01em; white-space: nowrap; }}
  .tamount.mid {{ font-size: 34px; }}
  .tamount.small {{ font-size: 26px; }}
  .note {{ max-width: 420px; margin: 12px auto 0; text-align: center; color: #64748b; font-size: 12px; }}
  @media print {{
    @page {{ size: 80mm auto; margin: 0; }}
    body {{ background: #fff; padding: 0; }}
    .bar, .note {{ display: none !important; }}
    .paper {{ box-shadow: none; margin: 0; padding: 2mm; width: 80mm; }}
    .tlabel {{ font-size: 16px; margin: 8px 0 3px; }}
    .tamount {{ font-size: 38px; margin-bottom: 10px; }}
    .tamount.mid {{ font-size: 30px; }}
    .tamount.small {{ font-size: 24px; }}
  }}
</style>
</head>
<body>
  <div class="bar">
    <span>{store} · Ticket {html.escape(sale['folio'])}</span>
    <span>
      <button class="primary" onclick="window.print()">Imprimir</button>
      <a href="/pos">Volver a caja</a>
    </span>
  </div>
  <div class="paper">{''.join(body)}</div>
  <p class="note">Vista previa de 80 mm. Usa “Imprimir” si la impresora directa falló.</p>
  {auto}
</body>
</html>"""
