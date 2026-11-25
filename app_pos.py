"""
Servidor local del POS moderno de La Colonial.

- Buscador de productos en http://127.0.0.1:8090/
- Punto de venta en http://127.0.0.1:8090/pos
- Solo escucha en 127.0.0.1 (sin internet, sin red local).

Ejecutar con doble clic en buscar_productos.bat (buscador) o caja.bat (POS).
"""

import json
import os
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pos_core
import pos_print
from catalogo import MAX_RESULTS, StoreCatalog
from pos_api import APP_VERSION, PosAPI, ticket_html

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_DIR = os.path.join(BASE_DIR, 'Historico', 'Auxiliares')
LEGACY_DB = os.path.join(LEGACY_DIR, 'inventario_moderno.db')
if not os.path.exists(LEGACY_DB):
    LEGACY_DB = os.path.join(BASE_DIR, 'inventario_moderno.db')
UI_DIR = os.path.join(BASE_DIR, 'ui')
HOST = '127.0.0.1'          # por defecto: sólo esta computadora
PORT_BASE = 8090
ALLOWED_IPS = []            # vacío = cualquiera en la red local (si se habilita)

STORE = StoreCatalog(
    pos_core.POS_DB,
    LEGACY_DB,
    pos_core.catalog_version,
    pos_core.products_count,
)
POS = PosAPI(STORE)


class Handler(SimpleHTTPRequestHandler):
    server_version = f'POSLaColonial/{APP_VERSION}'

    def log_message(self, fmt, *args):
        # Silencio: el lector dispara muchas peticiones y no queremos llenar la consola.
        pass

    def _ip_permitida(self):
        """Si hay lista de IPs permitidas, sólo entran esas (para la caja en red)."""
        if not ALLOWED_IPS:
            return True
        return self.client_address[0] in ALLOWED_IPS

    def _denegar(self):
        body = json.dumps({
            'ok': False,
            'error': 'Este equipo no está autorizado a usar la caja. '
                     'Pide al administrador que lo agregue a la lista de equipos permitidos.',
        }, ensure_ascii=False).encode('utf-8')
        self.send_response(403)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # La interfaz es local: nunca queremos que el navegador conserve una
        # versión vieja del HTML, CSS o JS después de una actualización.
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    # ------------------------------------------------------------ GET
    def do_GET(self):
        parsed = urlparse(self.path)
        if not self._ip_permitida():
            self._denegar()
            return
        if parsed.path.startswith('/api/pos/'):
            POS.handle_get(self, parsed)
            return
        if parsed.path == '/api/search':
            params = parse_qs(parsed.query)
            query = (params.get('q') or [''])[0]
            limit = MAX_RESULTS
            try:
                limit = min(int((params.get('limit') or [MAX_RESULTS])[0]), 200)
            except ValueError:
                pass
            self._send_json(STORE.search(query, limit))
            return
        if parsed.path == '/api/status':
            self._send_json(STORE.status())
            return
        if parsed.path in ('/pos', '/pos/'):
            self.path = '/caja.html' + (f'?{parsed.query}' if parsed.query else '')
            return super().do_GET()
        if parsed.path == '/pos/logo.png':
            if os.path.exists(pos_print.LOGO_PATH):
                with open(pos_print.LOGO_PATH, 'rb') as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)
            return
        if parsed.path.startswith('/pos/ticket/'):
            self._ticket_page(parsed)
            return
        if parsed.path == '/':
            self.path = '/index.html'
        return super().do_GET()

    # ------------------------------------------------------------ POST
    def do_POST(self):
        parsed = urlparse(self.path)
        if not self._ip_permitida():
            self._denegar()
            return
        if parsed.path.startswith('/api/pos/'):
            POS.handle_post(self, parsed)
            return
        self._send_json({'ok': False, 'error': 'Ruta no encontrada.'}, 404)

    # ------------------------------------------------------------ auxiliares
    def _ticket_page(self, parsed):
        raw = parsed.path.rsplit('/', 1)[-1]
        try:
            sale_id = int(raw)
        except ValueError:
            self._send_json({'ok': False, 'error': 'Ticket inválido.'}, 404)
            return
        sale = pos_core.get_sale(sale_id)
        if not sale:
            self._send_json({'ok': False, 'error': 'Ticket no encontrado.'}, 404)
            return
        token = (parse_qs(parsed.query).get('token') or [''])[0].strip()
        if not pos_core.verify_ticket_token(sale_id, token):
            self._send_json({
                'ok': False,
                'error': 'Ticket no autorizado. Ábrelo desde la caja.',
            }, 403)
            return
        config = pos_core.load_config()
        auto_print = (parse_qs(parsed.query).get('print') or ['0'])[0] in ('1', 'true', 'si', 'sí')
        body = ticket_html(sale, config, auto_print=auto_print).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)


def build_server(base_port=None):
    handler = partial(Handler, directory=UI_DIR)
    last_error = None
    base = base_port or PORT_BASE
    for offset in range(10):
        port = base + offset
        try:
            return ThreadingHTTPServer((HOST, port), handler), port
        except OSError as exc:
            last_error = exc
    raise SystemExit(f'No se pudo abrir ningún puerto local: {last_error}')


def existing_instance():
    """Si ya hay una caja abierta, no se arranca otra: se reutiliza."""
    import urllib.request
    for offset in range(10):
        url = f'http://127.0.0.1:{PORT_BASE + offset}'
        try:
            with urllib.request.urlopen(f'{url}/api/pos/health', timeout=0.8) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            continue
    return None


def ip_local():
    """IP de esta computadora en la red local (para imprimir la URL)."""
    import socket
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return '127.0.0.1'


def start_backup_loop():
    """Respaldo automático configurable; sólo si hubo ventas nuevas."""
    config = pos_core.load_config()
    minutes = max(5, int(config.get('backup', {}).get('auto_minutes', 60) or 60))

    def loop():
        try:
            last = pos_core.count_sales_total()['count']
        except Exception:
            last = 0
        while True:
            time.sleep(minutes * 60)
            try:
                total = pos_core.count_sales_total()['count']
                if total != last:
                    pos_core.backup('auto')
                    last = total
            except Exception:
                # Un respaldo fallido no debe tumbar la caja.
                pass

    threading.Thread(target=loop, daemon=True).start()


def main():
    if not os.path.isdir(UI_DIR):
        print('ERROR: falta la carpeta ui/.')
        return 1

    pos_core.ensure_config()
    pos_core.init_db()

    # La caja trabaja con su propio inventario (pos.db). El catálogo limpio
    # del ETL sólo sirve de apoyo si el inventario propio aún no se ha cargado.
    try:
        conteo = pos_core.products_count()
    except Exception as exc:
        print(f'ERROR: no se pudo leer pos.db ({exc}).')
        return 1
    if not conteo.get('total') and not os.path.exists(LEGACY_DB):
        print('ERROR: el POS no tiene inventario.')
        print('Carga el catálogo con:  python importar_inventario.py')
        return 1

    # Puerto explícito (--puerto 8095): permite una segunda instancia (pruebas).
    puerto_arg = None
    if '--puerto' in sys.argv:
        try:
            puerto_arg = int(sys.argv[sys.argv.index('--puerto') + 1])
        except (IndexError, ValueError):
            print('ERROR: --puerto necesita un número (ejemplo: --puerto 8095).')
            return 1

    running = None if puerto_arg else existing_instance()
    if running:
        target = f'{running}/pos' if '--pos' in sys.argv else f'{running}/'
        print('=' * 64)
        print('  YA HAY UNA CAJA ABIERTA EN ESTA COMPUTADORA')
        print('=' * 64)
        print(f'  Se abrirá: {target}')
        print('  No se inicia un segundo servidor (evita duplicar la caja).')
        print('=' * 64)
        try:
            if '--no-browser' not in sys.argv:
                webbrowser.open(target)
        except Exception:
            pass
        return 0

    if os.path.exists(pos_core.POS_DB) and os.path.getsize(pos_core.POS_DB) > 0:
        try:
            pos_core.backup('arranque')
        except Exception as exc:  # el respaldo nunca debe impedir vender
            print(f'Aviso: no se pudo respaldar pos.db ({exc})')

    STORE.ensure_loaded()
    status = STORE.status()

    # Red local: por defecto la caja sólo responde en esta computadora.
    # Con --red (o network.allow_lan en pos_config.json) responde en la red.
    global HOST, ALLOWED_IPS
    config = pos_core.load_config()
    network = config.get('network', {}) or {}
    ALLOWED_IPS = [str(ip).strip() for ip in (network.get('allowed_ips') or []) if str(ip).strip()]
    allow_lan = bool(network.get('allow_lan')) or '--red' in sys.argv
    if allow_lan:
        HOST = '0.0.0.0'

    server, port = build_server(puerto_arg)
    url = f'http://127.0.0.1:{port}/'
    pos_url = f'{url}pos'
    print('=' * 64)
    print('  LA COLONIAL - PUNTO DE VENTA LOCAL')
    print('=' * 64)
    print(f'  Buscador         : {url}')
    print(f'  Caja (POS)       : {pos_url}')
    print(f'  Catálogo         : {status.get("products", 0):,} productos '
          f'({status.get("source", "catalogo")})')
    print(f'  Impresora        : {pos_print.resolve_printer(config) or "sin configurar"}')
    print('  Lector           : USB en modo teclado (escribe y da Enter)')
    if allow_lan:
        print('-' * 64)
        print('  ACCESO EN RED LOCAL HABILITADO')
        print(f'  Desde otro equipo:  http://{ip_local()}:{port}/pos')
        if ALLOWED_IPS:
            print(f'  Equipos permitidos : {", ".join(ALLOWED_IPS)}')
        else:
            print('  Equipos permitidos : cualquiera en la red local (con PIN de acceso)')
        print('  Si no entra: revisa el firewall de Windows (ver INSTALACION.md).')
    else:
        print('  Red local         : desactivada (sólo esta computadora).')
        print('                    Para habilitarla: python app_pos.py --pos --red')
    print('  Para cerrar      : cierra esta ventana o presiona Ctrl+C')
    print('=' * 64)
    try:
        if '--no-browser' not in sys.argv:
            webbrowser.open(pos_url if '--pos' in sys.argv else url)
    except Exception:
        pass
    start_backup_loop()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServidor detenido.')
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
