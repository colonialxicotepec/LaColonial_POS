import io
import json
import sqlite3
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import urlparse

import pos_api
from core import suppliers
from pos_api import PosAPI


SCHEMA = """
CREATE TABLE proveedores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_id INTEGER UNIQUE,
    empresa TEXT NOT NULL,
    nombre_contacto TEXT,
    apellido_contacto TEXT,
    rfc TEXT,
    telefono TEXT,
    email TEXT,
    direccion TEXT,
    ciudad TEXT,
    estado TEXT,
    cp TEXT,
    saldo REAL NOT NULL DEFAULT 0,
    notas TEXT,
    activo INTEGER NOT NULL DEFAULT 1,
    origen TEXT NOT NULL DEFAULT 'pos',
    creado_en TEXT,
    actualizado_en TEXT
);

CREATE TABLE audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    user_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT
);
"""


class FakeHandler:
    def __init__(self, body):
        payload = json.dumps(body).encode('utf-8')
        self.headers = {'Content-Length': str(len(payload))}
        self.rfile = io.BytesIO(payload)


class SupplierBackendTests(unittest.TestCase):
    def setUp(self):
        # Una conexión mantiene viva la base compartida; las conexiones del
        # módulo son independientes, como en producción. Nunca toca pos.db.
        name = f'suppliers_backend_{uuid.uuid4().hex}'
        self.uri = f'file:{name}?mode=memory&cache=shared'
        self.keeper = sqlite3.connect(self.uri, uri=True)
        self.keeper.row_factory = sqlite3.Row
        self.keeper.executescript(SCHEMA)
        self.addCleanup(self.keeper.close)

        connect_patch = patch.object(suppliers, 'connect', side_effect=self._connect)
        connect_patch.start()
        self.addCleanup(connect_patch.stop)

    def _connect(self):
        conn = sqlite3.connect(self.uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        return conn

    def _audit_entries(self):
        rows = self.keeper.execute('SELECT * FROM audit ORDER BY id').fetchall()
        return [
            {**dict(row), 'detail': json.loads(row['detail'])}
            for row in rows
        ]

    def _api_call(self, path, body, role, user_id):
        api = PosAPI(catalog=None)
        responses = []
        session = {
            'token': 'test-token',
            'user_id': user_id,
            'name': f'Usuario {role}',
            'role': role,
        }
        with patch.object(api, '_auth', return_value=(session, {})), \
                patch.object(api, '_json', side_effect=lambda handler, payload, status=200: responses.append((status, payload))), \
                patch.object(pos_api, '_log_error'):
            api.handle_post(FakeHandler(body), urlparse(path))
        self.assertEqual(len(responses), 1)
        return responses[0]

    def test_crud_create_read_search_update_and_soft_delete(self):
        created = suppliers.create_proveedor({
            'empresa': '  Distribuidora   La Colonial  ',
            'nombre_contacto': ' Ana ',
            'apellido_contacto': ' Ruiz ',
            'rfc': ' abc123 ',
            'telefono': 0,
            'email': 'ana@example.com',
            'direccion': 'Calle 1',
            'ciudad': 'Puebla',
            'estado': 'Puebla',
            'cp': 72000,
            'saldo': '12.50',
            'notas': '  Proveedor   inicial  ',
        }, user_id=7)

        self.assertIsInstance(created['id'], int)
        self.assertEqual(created['empresa'], 'Distribuidora La Colonial')
        self.assertEqual(created['rfc'], 'ABC123')
        self.assertEqual(created['telefono'], '0')
        self.assertEqual(created['cp'], '72000')
        self.assertEqual(created['saldo'], 12.5)
        self.assertEqual(created['notas'], 'Proveedor inicial')
        self.assertTrue(created['activo'])
        self.assertEqual(suppliers.get_proveedor(created['id']), created)
        self.assertEqual(
            suppliers.search_proveedores('la colonial')['proveedores'][0]['id'],
            created['id'],
        )

        updated = suppliers.update_proveedor(created['id'], {'empresa': 'Nueva Empresa'})
        self.assertEqual(updated['empresa'], 'Nueva Empresa')
        self.assertEqual(updated['nombre_contacto'], 'Ana')
        self.assertEqual(updated['saldo'], 12.5)

        self.assertTrue(suppliers.delete_proveedor(created['id']))
        inactive = suppliers.get_proveedor(created['id'])
        self.assertFalse(inactive['activo'])
        self.assertEqual(suppliers.search_proveedores()['total'], 0)
        included = suppliers.search_proveedores(include_deleted=True)
        self.assertEqual(included['total'], 1)
        self.assertEqual(included['proveedores'][0]['id'], created['id'])

    def test_partial_update_preserves_fields_and_inactive_state(self):
        created = suppliers.create_proveedor({
            'empresa': 'Proveedor Inactivo',
            'nombre_contacto': 'Laura',
            'apellido_contacto': 'García',
            'rfc': 'rfc-1',
            'telefono': '111',
            'email': 'laura@example.com',
            'direccion': 'Dirección original',
            'ciudad': 'Puebla',
            'estado': 'Puebla',
            'cp': '72000',
            'saldo': 45.5,
            'notas': 'Nota original',
        })
        inactive = suppliers.update_proveedor(created['id'], {'activo': 'inactivo'})
        self.assertFalse(inactive['activo'])

        changed = suppliers.update_proveedor(created['id'], {'telefono': '222'})
        self.assertFalse(changed['activo'])
        self.assertEqual(changed['telefono'], '222')
        for field in (
            'empresa', 'nombre_contacto', 'apellido_contacto', 'rfc', 'email',
            'direccion', 'ciudad', 'estado', 'cp', 'saldo', 'notas',
        ):
            self.assertEqual(changed[field], created[field], field)
        self.assertEqual(suppliers.search_proveedores()['total'], 0)
        self.assertEqual(suppliers.search_proveedores(include_deleted=True)['total'], 1)

    def test_activo_accepts_boolean_numeric_and_common_text_values(self):
        created = suppliers.create_proveedor({'empresa': 'Normalización Activo'})
        cases = (
            (True, True), (False, False),
            (1, True), (0, False), (1.0, True), (0.0, False),
            ('true', True), ('FALSE', False),
            ('si', True), ('no', False), ('sí', True), ('SÍ', True),
            ('activo', True), (' inactivo ', False),
            ('1', True), ('0', False),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                updated = suppliers.update_proveedor(created['id'], {'activo': value})
                self.assertIs(updated['activo'], expected)
        created_inactive = suppliers.create_proveedor({
            'empresa': 'Crear Inactivo',
            'activo': 'no',
        })
        self.assertFalse(created_inactive['activo'])

    def test_activo_rejects_invalid_values_without_changes_or_audit(self):
        created = suppliers.create_proveedor({'empresa': 'Activo Inválido'})
        invalid_values = (None, '', 'quizás', 'on', 2, -1, 0.5, float('nan'), [], {})
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    suppliers.create_proveedor({'empresa': 'No crear', 'activo': value})
                with self.assertRaises(ValueError):
                    suppliers.update_proveedor(created['id'], {'activo': value})
        self.assertTrue(suppliers.get_proveedor(created['id'])['activo'])
        self.assertEqual(suppliers.search_proveedores(include_deleted=True)['total'], 1)
        self.assertEqual(len(self._audit_entries()), 1)

    def test_field_maximum_lengths_are_validated_on_create_and_update(self):
        created = suppliers.create_proveedor({'empresa': 'Longitudes'})
        for field, maximum in suppliers.FIELD_MAX_LENGTHS.items():
            with self.subTest(field=field):
                create_data = {'empresa': 'Demasiado largo'}
                create_data[field] = 'x' * (maximum + 1)
                with self.assertRaises(ValueError):
                    suppliers.create_proveedor(create_data)
                with self.assertRaises(ValueError):
                    suppliers.update_proveedor(created['id'], {field: 'x' * (maximum + 1)})
        self.assertEqual(suppliers.get_proveedor(created['id']), created)
        self.assertEqual(len(self._audit_entries()), 1)

        at_limit = {
            field: 'x' * maximum
            for field, maximum in suppliers.FIELD_MAX_LENGTHS.items()
        }
        accepted = suppliers.create_proveedor(at_limit)
        for field, maximum in suppliers.FIELD_MAX_LENGTHS.items():
            self.assertEqual(len(accepted[field]), maximum, field)

    def test_saldo_rejects_non_numeric_and_non_finite_values(self):
        created = suppliers.create_proveedor({'empresa': 'Saldo', 'saldo': 10})
        invalid_values = (
            'abc', True, float('nan'), float('inf'), float('-inf'),
            'NaN', 'Infinity', '1e999', [],
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    suppliers.create_proveedor({'empresa': 'Saldo inválido', 'saldo': value})
                with self.assertRaises(ValueError):
                    suppliers.update_proveedor(created['id'], {'saldo': value})
        self.assertEqual(suppliers.get_proveedor(created['id'])['saldo'], 10.0)
        self.assertEqual(len(self._audit_entries()), 1)
        self.assertEqual(
            suppliers.update_proveedor(created['id'], {'saldo': '25.75'})['saldo'],
            25.75,
        )

    def test_audit_records_detailed_before_after_and_session_user(self):
        created = suppliers.create_proveedor({
            'empresa': 'Auditoría',
            'saldo': 100,
        }, user_id=11)
        suppliers.update_proveedor(created['id'], {
            'saldo': 125.5,
            'activo': False,
            'notas': 'Cambio auditado',
        }, user_id=12)
        second = suppliers.create_proveedor({'empresa': 'Baja'}, user_id=11)
        suppliers.delete_proveedor(second['id'], user_id=13)

        entries = self._audit_entries()
        self.assertEqual([entry['action'] for entry in entries], [
            'proveedor_create', 'proveedor_update', 'proveedor_create', 'proveedor_delete',
        ])
        self.assertEqual([entry['user_id'] for entry in entries], [11, 12, 11, 13])
        self.assertEqual(entries[0]['detail']['despues']['saldo'], 100.0)
        self.assertTrue(entries[0]['detail']['despues']['activo'])

        changes = entries[1]['detail']['cambios']
        self.assertEqual(set(changes), {'saldo', 'activo', 'notas'})
        self.assertEqual(changes['saldo'], {'antes': 100.0, 'despues': 125.5})
        self.assertEqual(changes['activo'], {'antes': True, 'despues': False})
        self.assertEqual(changes['notas'], {'antes': '', 'despues': 'Cambio auditado'})
        self.assertEqual(
            entries[3]['detail']['cambios']['activo'],
            {'antes': True, 'despues': False},
        )

    def test_supplier_change_rolls_back_when_audit_fails(self):
        with patch.object(suppliers, '_insert_audit', side_effect=sqlite3.OperationalError('sin auditoría')):
            with self.assertRaises(sqlite3.OperationalError):
                suppliers.create_proveedor({'empresa': 'No persistir'})
        self.assertEqual(suppliers.search_proveedores(include_deleted=True)['total'], 0)
        self.assertEqual(self._audit_entries(), [])

        created = suppliers.create_proveedor({'empresa': 'Transaccional', 'saldo': 10})
        with patch.object(suppliers, '_insert_audit', side_effect=sqlite3.OperationalError('sin auditoría')):
            with self.assertRaises(sqlite3.OperationalError):
                suppliers.update_proveedor(created['id'], {'saldo': 999})
            with self.assertRaises(sqlite3.OperationalError):
                suppliers.delete_proveedor(created['id'])
        current = suppliers.get_proveedor(created['id'])
        self.assertEqual(current['saldo'], 10.0)
        self.assertTrue(current['activo'])
        self.assertEqual(len(self._audit_entries()), 1)

    def test_search_escapes_percent_underscore_and_escape_character(self):
        first = suppliers.create_proveedor({
            'empresa': 'Distribuidora 100% Real',
            'nombre_contacto': 'Ana',
            'apellido_contacto': 'Ruiz',
        })
        suppliers.create_proveedor({
            'empresa': 'Distribuidora 100X Real',
            'nombre_contacto': 'Ana',
            'apellido_contacto': 'Pérez',
        })
        underscore = suppliers.create_proveedor({
            'empresa': 'ACME_Supply',
            'nombre_contacto': 'Bob',
            'apellido_contacto': 'López',
        })
        suppliers.create_proveedor({
            'empresa': 'ACMEYSupply',
            'nombre_contacto': 'Bob',
            'apellido_contacto': 'Otro',
        })
        escaped = suppliers.create_proveedor({
            'empresa': 'ACME!Supply',
            'nombre_contacto': 'Carol',
            'apellido_contacto': 'Díaz',
        })

        def ids(query):
            return {row['id'] for row in suppliers.search_proveedores(query)['proveedores']}

        self.assertEqual(ids('%'), {first['id']})
        self.assertEqual(ids('_'), {underscore['id']})
        self.assertEqual(ids('!'), {escaped['id']})
        self.assertEqual(ids('Ana Ruiz'), {first['id']})
        self.assertEqual(ids('  ana   ruiz  '), {first['id']})

    def test_api_rejects_supplier_writes_for_cashier(self):
        calls = (
            ('/api/pos/proveedores/create', {'empresa': 'No permitido'}),
            ('/api/pos/proveedores/update', {'id': 1, 'empresa': 'No permitido'}),
            ('/api/pos/proveedores/delete', {'id': 1}),
        )
        for path, body in calls:
            with self.subTest(path=path):
                status, payload = self._api_call(path, body, 'cajero', 99)
                self.assertEqual(status, 403)
                self.assertFalse(payload['ok'])
        self.assertEqual(suppliers.search_proveedores(include_deleted=True)['total'], 0)
        self.assertEqual(self._audit_entries(), [])

    def test_api_allows_manager_and_admin_and_uses_session_user_id(self):
        for role, user_id in (('gerente', 21), ('administrador', 22)):
            with self.subTest(role=role):
                audit_start = len(self._audit_entries())
                status, payload = self._api_call(
                    '/api/pos/proveedores/create',
                    {'empresa': f'API {role}', 'user_id': 999},
                    role, user_id,
                )
                self.assertEqual(status, 200)
                supplier_id = payload['proveedor']['id']

                status, payload = self._api_call(
                    '/api/pos/proveedores/update',
                    {'id': supplier_id, 'saldo': 50, 'user_id': 999},
                    role, user_id,
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload['proveedor']['saldo'], 50.0)

                status, payload = self._api_call(
                    '/api/pos/proveedores/delete',
                    {'id': supplier_id, 'user_id': 999},
                    role, user_id,
                )
                self.assertEqual(status, 200)
                self.assertTrue(payload['ok'])

                entries = self._audit_entries()[audit_start:]
                self.assertEqual([entry['action'] for entry in entries], [
                    'proveedor_create', 'proveedor_update', 'proveedor_delete',
                ])
                self.assertEqual([entry['user_id'] for entry in entries], [user_id] * 3)

    def test_api_returns_validation_error_without_writing(self):
        status, payload = self._api_call(
            '/api/pos/proveedores/create',
            {'empresa': 'API inválida', 'activo': 'quizás'},
            'gerente', 31,
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload['ok'])
        self.assertEqual(suppliers.search_proveedores(include_deleted=True)['total'], 0)
        self.assertEqual(self._audit_entries(), [])


if __name__ == '__main__':
    unittest.main()
