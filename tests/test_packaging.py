"""Regresiones de distribución y tickets sin usar datos operativos reales."""

import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import empaquetar_proyecto as packaging
import verificar_instalacion
import verificar_paquete
from core import db


class PackagingCollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pos_collect_test_')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        patcher = mock.patch.object(packaging, 'BASE_DIR', str(self.base))
        patcher.start()
        self.addCleanup(patcher.stop)

    def create_file(self, relative, content=b'prueba'):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_supplier_and_logo_files_are_declared(self):
        self.assertIn('importar_proveedores.py', packaging.INCLUDE_FILES)
        self.assertIn('logo.ico', packaging.INCLUDE_FILES)
        self.assertIn('core', verificar_paquete.CARPETAS)
        for required in (
            'core/suppliers.py',
            'importar_proveedores.py',
            'ui/js/suppliers.js',
            'logo.ico',
            'ui/logo.png',
        ):
            self.assertIn(required, verificar_paquete.ESPERADOS)
        for required in (
            'importar_proveedores.py',
            os.path.join('core', 'suppliers.py'),
            os.path.join('ui', 'js', 'suppliers.js'),
            os.path.join('ui', 'logo.png'),
        ):
            self.assertIn(required, verificar_instalacion.REQUIRED_FILES)

    def test_collect_includes_versioned_logo_without_operational_tickets(self):
        for relative in (
            'importar_proveedores.py',
            'logo.ico',
            'core/suppliers.py',
            'ui/js/suppliers.js',
            'ui/logo.png',
            'tickets/2026-11-27/venta-ficticia.html',
            'tickets/2026-11-27/corte-ficticio.txt',
        ):
            self.create_file(relative)

        items = packaging.collect()

        self.assertIn('importar_proveedores.py', items)
        self.assertIn('logo.ico', items)
        self.assertIn('ui/logo.png', items)
        self.assertEqual(items.count('ui/logo.png'), 1)
        self.assertNotIn('tickets/logo.png', items)
        self.assertFalse(any('venta-ficticia' in item for item in items))
        self.assertFalse(any('corte-ficticio' in item for item in items))

    def test_collect_uses_legacy_logo_only_when_versioned_logo_is_missing(self):
        self.create_file('tickets/logo.png')
        self.create_file('tickets/2026-11-27/venta-ficticia.html')

        items = packaging.collect()

        self.assertIn('tickets/logo.png', items)
        self.assertNotIn('tickets/2026-11-27/venta-ficticia.html', items)

    def test_collect_omits_missing_logos_and_operational_tickets(self):
        self.create_file('tickets/2026-11-27/venta-ficticia.html')

        items = packaging.collect()

        self.assertNotIn('ui/logo.png', items)
        self.assertNotIn('tickets/logo.png', items)
        self.assertNotIn('tickets/2026-11-27/venta-ficticia.html', items)


class PackagingManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pos_manifest_test_')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.dist = self.base / 'dist'
        for name, value in (('BASE_DIR', str(self.base)), ('DIST_DIR', str(self.dist))):
            patcher = mock.patch.object(packaging, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def create_fake_db(self):
        path = self.base / 'pos.db'
        conn = sqlite3.connect(path)
        try:
            with conn:
                conn.execute(
                    'CREATE TABLE proveedores ('
                    'id INTEGER PRIMARY KEY, legacy_id INTEGER, empresa TEXT, activo INTEGER)'
                )
                conn.execute(
                    'INSERT INTO proveedores VALUES (1, 99, ?, 1)',
                    ('Proveedor ficticio de pruebas',),
                )
        finally:
            conn.close()
        return path

    def run_packager(self):
        output = self.dist / 'paquete-ficticio.zip'
        argv = ['empaquetar_proyecto.py', '--salida', str(output)]
        with mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            result = packaging.main()
        self.assertEqual(result, 0)
        return output

    def test_manifest_uses_final_pos_db_name(self):
        self.create_fake_db()
        (self.base / 'logo.ico').write_bytes(b'icono-ficticio')
        (self.base / 'tickets').mkdir()
        (self.base / 'tickets' / 'logo.png').write_bytes(b'logo-ficticio')

        output = self.run_packager()

        with zipfile.ZipFile(output) as package:
            names = package.namelist()
            self.assertIn('pos.db', names)
            self.assertNotIn('_pos_copia.db', names)
            self.assertIn('logo.ico', names)
            self.assertIn('tickets/logo.png', names)
            manifest = package.read('MANIFIESTO.txt').decode('utf-8')
            entries = {}
            for line in manifest.splitlines():
                if '  ' in line:
                    digest, name = line.split('  ', 1)
                    entries[name.strip()] = digest
            self.assertIn('pos.db', entries)
            self.assertNotIn('_pos_copia.db', entries)
            self.assertEqual(
                entries['pos.db'], hashlib.sha256(package.read('pos.db')).hexdigest()
            )
        self.assertFalse((self.dist / '_pos_copia.db').exists())

    def test_packaging_without_database_cleans_temporary_files(self):
        output = self.run_packager()

        self.assertTrue(output.exists())
        self.assertFalse((self.dist / '_pos_copia.db').exists())
        self.assertFalse((self.dist / '_MANIFIESTO.txt').exists())
        self.assertFalse((self.dist / '_LEEME.txt').exists())


class DatabaseVerificationTests(unittest.TestCase):
    SUPPLIER_COLUMNS = (
        'id INTEGER PRIMARY KEY, legacy_id INTEGER, empresa TEXT, '
        'nombre_contacto TEXT, apellido_contacto TEXT, rfc TEXT, telefono TEXT, '
        'email TEXT, direccion TEXT, ciudad TEXT, estado TEXT, cp TEXT, '
        'saldo REAL, notas TEXT, activo INTEGER, origen TEXT, '
        'creado_en TEXT, actualizado_en TEXT'
    )

    def test_schema_version_is_eleven(self):
        self.assertEqual(db.SCHEMA_VERSION, 11)

    def test_database_summary_compares_suppliers(self):
        with tempfile.TemporaryDirectory(prefix='pos_summary_test_') as tmp:
            path = Path(tmp) / 'base-ficticia.db'
            conn = sqlite3.connect(path)
            try:
                with conn:
                    conn.execute(f'CREATE TABLE proveedores ({self.SUPPLIER_COLUMNS})')
                    conn.execute(
                        'INSERT INTO proveedores '
                        '(id, legacy_id, empresa, rfc, saldo, activo, origen) '
                        'VALUES (1, 42, ?, ?, 10.5, 1, ?)',
                        ('Proveedor ficticio', 'PRUEBA01', 'pruebas'),
                    )
            finally:
                conn.close()
            before = verificar_paquete.resumen_db(str(path))
            self.assertEqual(before['proveedores'][0], 1)

            conn = sqlite3.connect(path)
            try:
                with conn:
                    conn.execute('UPDATE proveedores SET empresa = ? WHERE id = 1',
                                 ('Proveedor ficticio modificado',))
            finally:
                conn.close()
            after = verificar_paquete.resumen_db(str(path))

            self.assertNotEqual(before['proveedores'], after['proveedores'])


def load_app_pos_with_fakes():
    """Carga app_pos sin abrir pos.db ni iniciar el servidor real."""
    fake_core = types.ModuleType('pos_core')
    fake_core.POS_DB = 'base-inexistente-de-prueba.db'
    fake_core.catalog_version = mock.Mock(return_value=0)
    fake_core.products_count = mock.Mock(return_value={'total': 0})
    fake_core.get_sale = mock.Mock(return_value={'id': 17, 'folio': 'PRUEBA-17'})
    fake_core.verify_ticket_token = mock.Mock(return_value=True)
    fake_core.load_config = mock.Mock(return_value={'store': {'name': 'Pruebas'}})

    fake_print = types.ModuleType('pos_print')
    fake_print.LOGO_PATH = 'logo-inexistente-de-prueba.png'

    fake_catalog = types.ModuleType('catalogo')
    fake_catalog.MAX_RESULTS = 60
    fake_catalog.StoreCatalog = mock.Mock()

    fake_api = types.ModuleType('pos_api')
    fake_api.APP_VERSION = 'pruebas'
    fake_api.PosAPI = mock.Mock()
    fake_api.ticket_html = mock.Mock(return_value='<html>Ticket ficticio</html>')

    modules = {
        'pos_core': fake_core,
        'pos_print': fake_print,
        'catalogo': fake_catalog,
        'pos_api': fake_api,
    }
    spec = importlib.util.spec_from_file_location('_app_pos_packaging_test', ROOT / 'app_pos.py')
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class TicketRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_pos = load_app_pos_with_fakes()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pos_ticket_test_')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        for name, value in (
            ('BASE_DIR', str(self.base)),
            ('ALLOWED_IPS', []),
        ):
            patcher = mock.patch.object(self.app_pos, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            self.app_pos.pos_print, 'LOGO_PATH', str(self.base / 'logo.png')
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.app_pos.pos_core.get_sale.reset_mock()
        self.app_pos.pos_core.get_sale.return_value = {'id': 17, 'folio': 'PRUEBA-17'}
        self.app_pos.pos_core.verify_ticket_token.reset_mock()
        self.app_pos.pos_core.verify_ticket_token.side_effect = (
            lambda sale_id, token: bool(token) and token == 'ficticio'
        )
        self.app_pos.pos_core.load_config.reset_mock()
        self.app_pos.pos_core.load_config.return_value = {'store': {'name': 'Pruebas'}}
        self.app_pos.ticket_html.reset_mock()
        self.app_pos.ticket_html.return_value = '<html>Ticket ficticio</html>'

    def make_handler(self, path, mock_ticket=True):
        handler = object.__new__(self.app_pos.Handler)
        handler.path = path
        handler.client_address = ('127.0.0.1', 12345)
        handler.request_version = 'HTTP/1.1'
        handler.command = 'GET'
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.send_error = mock.Mock()
        handler._send_json = mock.Mock()
        if mock_ticket:
            handler._ticket_page = mock.Mock()
        return handler

    def test_ticket_route_dispatches_to_ticket_page(self):
        handler = self.make_handler('/pos/ticket/17?token=ficticio')

        handler.do_GET()

        handler._ticket_page.assert_called_once()
        parsed = handler._ticket_page.call_args.args[0]
        self.assertEqual(parsed.path, '/pos/ticket/17')
        self.assertEqual(parsed.query, 'token=ficticio')
        handler.send_error.assert_not_called()

    def test_ticket_route_renders_with_fake_sale_and_token(self):
        handler = self.make_handler('/pos/ticket/17?token=ficticio&print=1', mock_ticket=False)

        handler.do_GET()

        self.app_pos.pos_core.get_sale.assert_called_once_with(17)
        self.app_pos.pos_core.verify_ticket_token.assert_called_once_with(17, 'ficticio')
        self.app_pos.ticket_html.assert_called_once_with(
            {'id': 17, 'folio': 'PRUEBA-17'},
            {'store': {'name': 'Pruebas'}},
            auto_print=True,
        )
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call('Content-Type', 'text/html; charset=utf-8')
        self.assertEqual(handler.wfile.getvalue(), b'<html>Ticket ficticio</html>')

    def test_ticket_route_rejects_missing_or_invalid_token(self):
        missing = self.make_handler('/pos/ticket/17', mock_ticket=False)
        missing.do_GET()
        missing._send_json.assert_called_once()
        self.assertEqual(missing._send_json.call_args.args[1], 403)

        wrong = self.make_handler('/pos/ticket/17?token=incorrecto', mock_ticket=False)
        wrong.do_GET()
        wrong._send_json.assert_called_once()
        self.assertEqual(wrong._send_json.call_args.args[1], 403)

        self.app_pos.pos_core.get_sale.reset_mock()
        invalid = self.make_handler('/pos/ticket/no-es-numero?token=ficticio', mock_ticket=False)
        invalid.do_GET()
        invalid._send_json.assert_called_once()
        self.assertEqual(invalid._send_json.call_args.args[1], 404)
        self.app_pos.pos_core.get_sale.assert_not_called()

    def test_logo_ico_still_works_and_does_not_dispatch_ticket(self):
        (self.base / 'logo.ico').write_bytes(b'icono-ficticio')
        handler = self.make_handler('/pos/logo.ico')

        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call('Content-Type', 'image/x-icon')
        handler.send_header.assert_any_call('Content-Length', str(len(b'icono-ficticio')))
        self.assertEqual(handler.wfile.getvalue(), b'icono-ficticio')
        handler._ticket_page.assert_not_called()

    def test_missing_logo_ico_returns_404_without_dispatching_ticket(self):
        handler = self.make_handler('/pos/logo.ico')

        handler.do_GET()

        handler.send_error.assert_called_once_with(404)
        handler._ticket_page.assert_not_called()


if __name__ == '__main__':
    unittest.main()
