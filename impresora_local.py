r"""
Agente de impresión local — UNO POR COMPUTADORA.

Cada PC que despacha o cobra en la tienda corre este proceso en 127.0.0.1:8099.
- Maneja la impresora USB conectada a ESTA computadora (aunque cada PC tenga
  una impresora con distinto nombre en Windows).
- Si se configura `server_url` en `impresora_pc.json` (ej. http://192.168.1.50:8090),
  también actúa como puente local en http://127.0.0.1:8099/pos para que Chrome/Edge
  no bloqueen la impresión local por reglas de red privada.
"""

import datetime
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import pos_print         # noqa: E402

HOST = '127.0.0.1'
PORT = int(os.environ.get('POS_AGENTE_PORT', '8099'))
CONFIG_PATH = os.path.join(BASE_DIR, 'pos_config.json')
LOCAL_PC_PATH = os.path.join(BASE_DIR, 'impresora_pc.json')
LOG_PATH = os.path.join(BASE_DIR, 'impresora_local.log')

DEFAULTS = {
    'name': '',
    'width': 42,
    'open_drawer': False,
    'print_sale': True,
    'print_shift': True,
    'total_style': 'escpos',
}


def read_local_pc():
    try:
        with open(LOCAL_PC_PATH, encoding='utf-8-sig') as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_local_pc(data):
    with open(LOCAL_PC_PATH, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def local_config():
    """Configuración efectiva de impresora para ESTA computadora."""
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding='utf-8-sig') as fh:
            data = json.load(fh)
        printer = data.get('printer') or {}
        for key in DEFAULTS:
            if key in printer:
                cfg[key] = printer[key]
    except (OSError, ValueError):
        pass
    pc = read_local_pc()
    if 'printer_name' in pc:
        cfg['name'] = (pc.get('printer_name') or '').strip()
    return {'printer': cfg, 'server_url': (pc.get('server_url') or '').strip().rstrip('/')}


def log(message):
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{stamp}] {message}'
    if sys.stdout:
        try:
            print(line, flush=True)
        except OSError:
            pass
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except OSError:
        pass


def allowed_origin(origin):
    """Acepta orígenes locales y de red privada: http://<ip>:puerto o localhost."""
    if not origin or not origin.startswith('http://'):
        return False
    host = origin[len('http://'):].split('/')[0].split(':')[0]
    if host in ('localhost', '127.0.0.1'):
        return True
    parts = host.split('.')
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


class Handler(BaseHTTPRequestHandler):
    server_version = 'PosImpresora/1.1'

    # ------------------------------------------------------------- utilidades
    def _cors(self):
        origin = self.headers.get('Origin', '')
        if allowed_origin(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Pos-Impresora, Authorization')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Max-Age', '600')

    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body_raw(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length else b''

    def _body(self):
        raw = self._body_raw()
        try:
            return json.loads(raw.decode('utf-8') or '{}')
        except (ValueError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------- proxy opcional al servidor POS
    def _proxy(self, method):
        server_url = local_config().get('server_url') or ''
        if not server_url:
            return False
        target = server_url + self.path
        body = self._body_raw() if method in ('POST', 'PUT', 'PATCH') else None
        headers = {}
        for h in ('Content-Type', 'Authorization', 'X-Pos-Token', 'Accept'):
            val = self.headers.get(h)
            if val:
                headers[h] = val
        req = urllib.request.Request(target, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() in ('content-type', 'cache-control'):
                        self.send_header(k, v)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as err:
            data = err.read()
            self.send_response(err.code)
            ctype = err.headers.get('Content-Type') or 'application/json; charset=utf-8'
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            self._json({'ok': False, 'error': f'Sin conexión con la Caja central ({server_url}): {exc}'}, 502)
        return True

    # ------------------------------------------------------------- endpoints
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/estado', '/impresoras'):
            cfg = local_config()
            printer = pos_print.resolve_printer(cfg)
            self._json({
                'ok': True,
                'impresora': printer,
                'impresoras': pos_print.list_printers(),
                'predeterminada': pos_print.default_printer(),
                'ancho': int(cfg.get('printer', {}).get('width', 42) or 42),
                'total_style': cfg.get('printer', {}).get('total_style', 'escpos'),
            })
            return
        if path == '/' and not local_config().get('server_url'):
            cfg = local_config()
            printer = pos_print.resolve_printer(cfg)
            self._json({
                'ok': True,
                'impresora': printer,
                'impresoras': pos_print.list_printers(),
            })
            return
        if self._proxy('GET'):
            return
        self._json({'ok': False, 'error': 'Ruta no encontrada.'}, 404)

    def do_POST(self):
        path = self.path.split('?')[0]
        if path == '/impresora':
            body = self._body()
            name = (body.get('impresora') or '').strip()
            pc = read_local_pc()
            pc['printer_name'] = name
            write_local_pc(pc)
            cfg = local_config()
            resolved = pos_print.resolve_printer(cfg)
            log(f'Impresora local seleccionada: {resolved or "(predeterminada)"}')
            self._json({
                'ok': True,
                'impresora': resolved,
                'impresoras': pos_print.list_printers(),
            })
            return
        if path == '/imprimir':
            body = self._body()
            cfg = local_config()
            printer = pos_print.resolve_printer(cfg)
            drawer = bool(body.get('drawer', False))
            try:
                lines = body.get('lineas') or []
                if lines:
                    typed = [(str(k), str(t)) for k, t in lines]
                    data = pos_print.escpos_lines(typed, cfg, cut=True, drawer=drawer)
                else:
                    texto = str(body.get('texto') or '')
                    data = pos_print.escpos_bytes(texto, cfg, cut=True, drawer=False)
                ok, error = pos_print.print_raw(data, printer)
            except Exception as exc:      # noqa: BLE001
                log('ERROR al imprimir: ' + traceback.format_exc())
                self._json({'ok': False, 'error': f'Error al imprimir: {exc}'}, 500)
                return
            if ok:
                log(f'Impreso en {printer} ({len(data)} bytes)')
            else:
                log(f'Fallo al imprimir en {printer}: {error}')
            self._json({'ok': ok, 'error': error, 'impresora': printer})
            return
        if self._proxy('POST'):
            return
        self._json({'ok': False, 'error': 'Ruta no encontrada.'}, 404)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Agente de impresora local POS.')
    parser.add_argument('--servidor', default='', help='URL de la Caja central (ej. http://192.168.1.50:8090)')
    args, _ = parser.parse_known_args()
    if args.servidor:
        pc = read_local_pc()
        pc['server_url'] = args.servidor.strip().rstrip('/')
        write_local_pc(pc)

    cfg = local_config()
    printer = pos_print.resolve_printer(cfg)
    srv = cfg.get('server_url') or ''
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        log(f'Agente ya activo o puerto {PORT} ocupado ({exc}).')
        return
    log(f'Agente de impresion local en http://{HOST}:{PORT}  |  impresora: {printer or "ninguna"}'
        + (f'  |  puente -> {srv}' if srv else ''))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('Agente detenido.')


if __name__ == '__main__':
    main()
