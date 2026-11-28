"""Pruebas aisladas del importador; nunca abren pos.db ni respaldos reales."""

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import importar_proveedores as imp


SOURCE_SCHEMA = """
    CREATE TABLE people (
        person_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        full_name TEXT,
        phone_number TEXT,
        email TEXT,
        address_1 TEXT,
        city TEXT,
        state TEXT,
        zip TEXT
    );
    CREATE TABLE suppliers (
        id INTEGER PRIMARY KEY,
        person_id INTEGER,
        first_name TEXT,
        last_name TEXT,
        company_name TEXT,
        account_number TEXT,
        balance REAL,
        deleted INTEGER
    );
"""

TARGET_SCHEMA = """
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


def source_row(legacy_id=1, **changes):
    row = {
        'id': legacy_id,
        'person_id': 1000 + int(legacy_id),
        'first_name': '',
        'last_name': '',
        'company_name': f'Proveedor {legacy_id}',
        'account_number': 'GODE561231GR8',
        'balance': 100.0,
        'deleted': 0,
        'p_first_name': 'Ana',
        'p_last_name': 'Pérez',
        'phone_number': '+52 (238) 123-4567 ext. 009',
        'email': 'ana@example.com',
        'address_1': 'Calle Histórica 10',
        'city': 'Xicotepec',
        'state': 'Puebla',
        'zip': '01234',
    }
    row.update(changes)
    return row


def create_source(path, rows):
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SOURCE_SCHEMA)
        for row in rows:
            conn.execute(
                """INSERT INTO people(
                       person_id, first_name, last_name, full_name, phone_number,
                       email, address_1, city, state, zip
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    row['person_id'], row['p_first_name'], row['p_last_name'], '',
                    row['phone_number'], row['email'], row['address_1'],
                    row['city'], row['state'], row['zip'],
                ),
            )
            conn.execute(
                """INSERT INTO suppliers(
                       id, person_id, first_name, last_name, company_name,
                       account_number, balance, deleted
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    row['id'], row['person_id'], row['first_name'], row['last_name'],
                    row['company_name'], row['account_number'], row['balance'], row['deleted'],
                ),
            )
        conn.commit()
    finally:
        conn.close()


def memory_target():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(TARGET_SCHEMA)
    return conn


def existing_row(legacy_id=1, **changes):
    row = {
        'legacy_id': legacy_id,
        'empresa': f'Proveedor {legacy_id}',
        'nombre_contacto': 'Ana',
        'apellido_contacto': 'Pérez',
        'rfc': 'GODE561231GR8',
        'telefono': '2381234567 ext. 009',
        'email': 'ana@example.com',
        'direccion': 'Calle Histórica 10',
        'ciudad': 'Xicotepec',
        'estado': 'Puebla',
        'cp': '01234',
        'saldo': 100.0,
        'notas': None,
        'activo': 1,
        'origen': 'historico_1box',
        'creado_en': '2026-01-01 00:00:00',
        'actualizado_en': '2026-01-01 00:00:00',
    }
    row.update(changes)
    return row


def insert_existing(conn, row):
    columns = imp.RECORD_COLUMNS
    conn.execute(
        f'INSERT INTO proveedores({",".join(columns)}) '
        f'VALUES({",".join("?" for _ in columns)})',
        tuple(row[column] for column in columns),
    )
    conn.commit()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ValidationTests(unittest.TestCase):
    def test_phone_preserves_extensions_and_separate_numbers(self):
        raw = 'Tel. +52 (238) 123-4567 ext. 009 / 238 765-4321 x45'
        expected = '2381234567 ext. 009; 2387654321 ext. 45'
        self.assertEqual(imp.normalize_phone(raw), expected)
        self.assertEqual(imp.normalize_phone(expected), expected)
        self.assertEqual(imp.normalize_phone('2381234567, 2387654321'), '2381234567; 2387654321')

    def test_ambiguous_or_concatenated_phone_is_not_guessed(self):
        for raw in ('23812345672387654321', '2381234567 ext. 2 2387654321', 'ext. 23', 'llamar al 238'):
            with self.subTest(raw=raw):
                phone, error = imp.parse_phone(raw)
                self.assertIsNone(phone)
                self.assertIn('inválido', error.lower())

    def test_rfc_and_email_validation_does_not_invent_or_repair(self):
        self.assertEqual(imp.validate_rfc('gode 561231-gr8'), ('GODE561231GR8', None))
        for raw in ('123', 'GODE561231GR', 'ABCDEF'):
            with self.subTest(rfc=raw):
                self.assertIsNone(imp.validate_rfc(raw)[0])
                self.assertIsNotNone(imp.validate_rfc(raw)[1])

        self.assertEqual(
            imp.validate_email(' Proveedor+ventas@example.com '),
            ('Proveedor+ventas@example.com', None),
        )
        for raw in ('sin-arroba', 'a..b@example.com', 'a b@example.com', 'a@example', 'a@hotmail:com'):
            with self.subTest(email=raw):
                self.assertIsNone(imp.validate_email(raw)[0])
                self.assertIsNotNone(imp.validate_email(raw)[1])

    def test_optional_invalid_values_are_null_and_reported(self):
        record = imp.supplier_values(source_row(
            account_number='NO-RFC', email='correo-invalido', phone_number='12345678901234567890',
        ), '2026-11-27 10:00:00')
        self.assertIsNone(record.rfc)
        self.assertIsNone(record.email)
        self.assertIsNone(record.telefono)
        self.assertEqual({issue.field for issue in record.issues}, {'rfc', 'email', 'telefono'})
        self.assertEqual(record.empresa, 'Proveedor 1')
        self.assertEqual(record.saldo, 100.0)

    def test_fatal_rows_are_rejected_without_placeholder_data(self):
        with self.assertRaises(imp.InvalidSupplier) as caught:
            imp.supplier_values(source_row(company_name='', p_first_name='', p_last_name=''), 'stamp')
        self.assertEqual(caught.exception.issue.field, 'empresa')
        self.assertTrue(caught.exception.issue.fatal)

        for changes in ({'balance': 'NaN'}, {'balance': 'no-numero'}, {'id': '1.5'}, {'deleted': 2}):
            with self.subTest(changes=changes), self.assertRaises(imp.InvalidSupplier):
                imp.supplier_values(source_row(**changes), 'stamp')

    def test_duplicate_source_ids_are_reported(self):
        row = source_row()
        prepared = imp.prepare_records([row, row], 'stamp')
        self.assertEqual(len(prepared.records), 1)
        self.assertEqual(len(prepared.rejected), 1)
        self.assertEqual(prepared.rejected[0].issue.field, 'id')


class PlanningTests(unittest.TestCase):
    def test_default_plan_preserves_all_existing_local_data(self):
        prepared = imp.prepare_records([source_row(
            company_name='Nombre nuevo', balance=50.0, deleted=0, address_1='',
            phone_number='2381234567 ext. 009; 2387654321',
        )], 'stamp')
        current = existing_row(
            empresa='Nombre local', saldo=999.0, notas='Crédito aprobado', activo=0,
            direccion='Dirección local',
        )
        plan = imp.build_plan(prepared, {1: current})
        action = plan.actions[0]
        self.assertEqual(action.kind, 'unchanged')
        self.assertEqual(action.updates, ())
        self.assertTrue({
            'empresa', 'telefono', 'direccion', 'saldo', 'notas', 'activo',
        }.issubset({change.field for change in action.local_changes}))
        self.assertTrue(all(not change.will_overwrite for change in action.local_changes))
        self.assertEqual(plan.summary['existentes'], 1)
        self.assertEqual(plan.summary['cambios_locales'], 1)
        self.assertGreaterEqual(plan.summary['campos_locales_preservados'], 6)

    def test_default_plan_only_fills_empty_managed_fields(self):
        prepared = imp.prepare_records([source_row(
            company_name='Nombre histórico', phone_number='2381234567', email='ana@example.com',
        )], 'stamp')
        current = existing_row(empresa='Nombre local', telefono=None, email=None)
        plan = imp.build_plan(prepared, {1: current})
        action = plan.actions[0]
        self.assertEqual(action.kind, 'update')
        self.assertEqual(set(action.updates), {'telefono', 'email'})
        self.assertNotIn('empresa', action.updates)

    def test_explicit_overwrite_changes_balance_and_active_but_never_notes(self):
        prepared = imp.prepare_records([source_row(balance=50.0, deleted=0)], 'stamp')
        current = existing_row(saldo=999.0, notas='No borrar', activo=0)
        plan = imp.build_plan(prepared, {1: current}, sobrescribir=True)
        action = plan.actions[0]
        self.assertIn('saldo', action.updates)
        self.assertIn('activo', action.updates)
        self.assertNotIn('notas', action.updates)
        changes = {change.field: change for change in action.local_changes}
        self.assertTrue(changes['saldo'].will_overwrite)
        self.assertTrue(changes['activo'].will_overwrite)
        self.assertFalse(changes['notas'].will_overwrite)

    def test_invalid_source_never_erases_valid_local_contact_even_with_overwrite(self):
        prepared = imp.prepare_records([source_row(email='correo-invalido')], 'stamp')
        record = prepared.records[0]
        current = record.as_dict()
        current['id'] = 1
        current['email'] = 'valido@example.com'
        current['creado_en'] = 'antes'
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                plan = imp.build_plan(prepared, {1: current}, sobrescribir=overwrite)
                self.assertNotIn('email', plan.actions[0].updates)
                self.assertEqual(plan.summary['datos_invalidos'], 1)

    def test_absent_managed_value_is_preserved_unless_overwrite_is_explicit(self):
        prepared = imp.prepare_records([source_row(address_1='', phone_number='')], 'stamp')
        current = existing_row(direccion='Dirección local', telefono='2381112233')
        conservative = imp.build_plan(prepared, {1: current})
        self.assertNotIn('direccion', conservative.actions[0].updates)
        self.assertNotIn('telefono', conservative.actions[0].updates)
        overwrite = imp.build_plan(prepared, {1: current}, sobrescribir=True)
        self.assertIn('direccion', overwrite.actions[0].updates)
        self.assertIn('telefono', overwrite.actions[0].updates)

    def test_new_and_rejected_counts_are_separate_from_invalid_field_count(self):
        prepared = imp.prepare_records([
            source_row(1, email='mal'),
            source_row(2, company_name='', p_first_name='', p_last_name=''),
            source_row(3),
        ], 'stamp')
        plan = imp.build_plan(prepared, {})
        self.assertEqual(plan.summary['leidos'], 3)
        self.assertEqual(plan.summary['nuevos'], 2)
        self.assertEqual(plan.summary['omitidos'], 1)
        self.assertEqual(plan.summary['datos_invalidos'], 2)


class TransactionTests(unittest.TestCase):
    def test_import_is_data_idempotent_and_audits_each_summary(self):
        conn = memory_target()
        try:
            prepared = imp.prepare_records([source_row()], '2026-11-27 10:00:00')
            first = imp.apply_import(conn, prepared, '2026-11-27 10:00:00', source_path='historico.db')
            first_row = dict(conn.execute('SELECT * FROM proveedores').fetchone())
            second = imp.apply_import(conn, prepared, '2026-11-27 11:00:00', source_path='historico.db')
            second_row = dict(conn.execute('SELECT * FROM proveedores').fetchone())

            self.assertEqual(first.summary['nuevos'], 1)
            self.assertEqual(second.summary['existentes'], 1)
            self.assertEqual(second.summary['sin_cambios'], 1)
            self.assertEqual(second.summary['actualizaciones'], 0)
            self.assertEqual(second_row, first_row)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 2)
        finally:
            conn.close()

    def test_failure_rolls_back_suppliers_and_audit_together(self):
        conn = memory_target()
        try:
            insert_existing(conn, existing_row(1, empresa='Local 1'))
            insert_existing(conn, existing_row(2, empresa='Local 2'))
            conn.execute("""
                CREATE TRIGGER fail_supplier_2
                BEFORE UPDATE ON proveedores WHEN NEW.legacy_id = 2
                BEGIN
                    SELECT RAISE(ABORT, 'fallo de prueba');
                END
            """)
            conn.commit()
            prepared = imp.prepare_records([
                source_row(1, company_name='Histórico 1'),
                source_row(2, company_name='Histórico 2'),
            ], 'stamp')
            with self.assertRaises(sqlite3.IntegrityError):
                imp.apply_import(conn, prepared, 'stamp', sobrescribir=True)
            self.assertEqual(
                [row[0] for row in conn.execute('SELECT empresa FROM proveedores ORDER BY legacy_id')],
                ['Local 1', 'Local 2'],
            )
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 0)
        finally:
            conn.close()

    def test_audit_contains_counts_mode_backup_and_invalid_fields(self):
        conn = memory_target()
        try:
            prepared = imp.prepare_records([
                source_row(1, email='inválido'),
                source_row(2, company_name='', p_first_name='', p_last_name=''),
            ], 'stamp')
            result = imp.apply_import(
                conn, prepared, '2026-11-27 12:00:00', sobrescribir=False,
                source_path='fuente.db', backup_path='respaldo.db',
            )
            audit = conn.execute('SELECT action, detail FROM audit').fetchone()
            detail = json.loads(audit['detail'])
            self.assertEqual(audit['action'], 'proveedores_import')
            self.assertEqual(detail['modo'], 'no_destructivo')
            self.assertEqual(detail['respaldo'], os.path.abspath('respaldo.db'))
            self.assertEqual(detail['resumen']['nuevos'], 1)
            self.assertEqual(detail['resumen']['omitidos'], 1)
            self.assertEqual(detail['resumen']['datos_invalidos'], 2)
            self.assertEqual(detail['campos_invalidos'], ['email', 'empresa'])
            self.assertIsNotNone(result.audit_id)
            self.assertIsNone(conn.execute('SELECT email FROM proveedores WHERE legacy_id=1').fetchone()[0])
        finally:
            conn.close()

    def test_explicit_overwrite_updates_balance_and_active_but_preserves_notes(self):
        conn = memory_target()
        try:
            insert_existing(conn, existing_row(
                empresa='Empresa local', saldo=999.0, activo=0, notas='Nota local',
            ))
            prepared = imp.prepare_records([source_row(
                company_name='Empresa histórica', balance=25.0, deleted=0,
            )], 'stamp')
            result = imp.apply_import(conn, prepared, 'nuevo-stamp', sobrescribir=True)
            row = conn.execute('SELECT * FROM proveedores').fetchone()
            self.assertEqual(row['empresa'], 'Empresa histórica')
            self.assertEqual(row['saldo'], 25.0)
            self.assertEqual(row['activo'], 1)
            self.assertEqual(row['notas'], 'Nota local')
            self.assertEqual(result.summary['campos_locales_sobrescritos'], 3)
            detail = json.loads(conn.execute('SELECT detail FROM audit').fetchone()[0])
            self.assertEqual(detail['modo'], 'sobrescribir')
        finally:
            conn.close()

    def test_upsert_keeps_created_at_origin_and_local_notes(self):
        conn = memory_target()
        try:
            insert_existing(conn, existing_row(
                empresa='Local', saldo=800.0, activo=0, notas='Nota local',
                origen='pos', creado_en='fecha-local', actualizado_en='actualizado-local',
            ))
            prepared = imp.prepare_records([source_row(company_name='Histórico', balance=10.0)], 'stamp')
            imp.apply_import(conn, prepared, 'nuevo-stamp')
            row = conn.execute('SELECT * FROM proveedores').fetchone()
            self.assertEqual(row['empresa'], 'Local')
            self.assertEqual(row['saldo'], 800.0)
            self.assertEqual(row['activo'], 0)
            self.assertEqual(row['notas'], 'Nota local')
            self.assertEqual(row['origen'], 'pos')
            self.assertEqual(row['creado_en'], 'fecha-local')
            self.assertEqual(row['actualizado_en'], 'actualizado-local')
        finally:
            conn.close()


class OrchestrationTests(unittest.TestCase):
    def test_dry_run_reports_and_does_not_backup_init_or_modify_files(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'historico.db')
            target = os.path.join(temp, 'pos.db')
            create_source(source, [
                source_row(1, company_name='Histórico 1', balance=25.0, email='correo-invalido'),
                source_row(2),
            ])
            conn = sqlite3.connect(target)
            conn.executescript(TARGET_SCHEMA)
            conn.close()
            insert_conn = sqlite3.connect(target)
            insert_conn.row_factory = sqlite3.Row
            insert_existing(insert_conn, existing_row(
                1, empresa='Local 1', saldo=500.0, activo=0, notas='Nota local',
            ))
            insert_conn.close()
            source_before = file_hash(source)
            target_before = file_hash(target)

            def forbidden(*args, **kwargs):
                raise AssertionError('dry-run no debe respaldar ni inicializar')

            result = imp.run_import(
                source, target, dry_run=True, backup_func=forbidden, init_db_func=forbidden,
                now_func=lambda: 'stamp',
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                imp.print_report(result)

            self.assertEqual(result.summary['nuevos'], 1)
            self.assertEqual(result.summary['existentes'], 1)
            self.assertEqual(result.summary['cambios_locales'], 1)
            self.assertEqual(result.summary['datos_invalidos'], 1)
            self.assertIn('Nuevos: 1', output.getvalue())
            self.assertIn('Existentes: 1', output.getvalue())
            self.assertIn('Cambios locales: 1', output.getvalue())
            self.assertIn('Datos inválidos: 1', output.getvalue())
            self.assertIn('correo-invalido', output.getvalue())
            self.assertIn('no se modificó pos.db', output.getvalue())
            self.assertEqual(file_hash(source), source_before)
            self.assertEqual(file_hash(target), target_before)

    def test_dry_run_with_missing_target_does_not_create_it(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'historico.db')
            target = os.path.join(temp, 'no-existe.db')
            create_source(source, [source_row()])

            def forbidden(*args, **kwargs):
                raise AssertionError('no debe escribir')

            result = imp.run_import(
                source, target, dry_run=True, init_db_func=forbidden,
                now_func=lambda: 'stamp',
            )
            self.assertEqual(result.summary['nuevos'], 1)
            self.assertFalse(os.path.exists(target))

    def test_dry_run_counts_local_suppliers_and_projects_active_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'historico.db')
            target = os.path.join(temp, 'pos.db')
            create_source(source, [source_row(1, deleted=0)])
            conn = sqlite3.connect(target)
            conn.executescript(TARGET_SCHEMA)
            conn.close()
            insert_conn = sqlite3.connect(target)
            insert_conn.row_factory = sqlite3.Row
            insert_existing(insert_conn, existing_row(1, activo=0))
            insert_existing(insert_conn, existing_row(99, empresa='Proveedor local', activo=1))
            insert_conn.close()

            conservative = imp.run_import(source, target, dry_run=True, now_func=lambda: 'stamp')
            self.assertEqual(conservative.total_suppliers, 2)
            self.assertEqual(conservative.active_suppliers, 1)

            overwrite = imp.run_import(
                source, target, dry_run=True, sobrescribir=True, now_func=lambda: 'stamp',
            )
            self.assertEqual(overwrite.total_suppliers, 2)
            self.assertEqual(overwrite.active_suppliers, 2)

    def test_wal_backup_is_consistent_and_happens_before_init(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'historico.db')
            target = os.path.join(temp, 'pos.db')
            backup = os.path.join(temp, 'preimport.db')
            create_source(source, [source_row(1), source_row(2)])
            events = []
            keeper = sqlite3.connect(target)
            try:
                keeper.row_factory = sqlite3.Row
                keeper.execute('PRAGMA journal_mode=WAL')
                keeper.execute('PRAGMA wal_autocheckpoint=0')
                keeper.executescript(TARGET_SCHEMA)
                insert_existing(keeper, existing_row(1, empresa='Local con WAL', saldo=777.0))

                def fake_backup(tag):
                    events.append(('backup', tag))
                    src = sqlite3.connect(target)
                    out = sqlite3.connect(backup)
                    try:
                        src.backup(out)
                    finally:
                        out.close()
                        src.close()
                    return backup

                def fake_init():
                    events.append(('init', None))
                    conn = sqlite3.connect(target)
                    try:
                        conn.execute('CREATE TABLE init_marker(value TEXT)')
                        conn.commit()
                    finally:
                        conn.close()

                source_before = file_hash(source)
                result = imp.run_import(
                    source, target, backup_func=fake_backup, init_db_func=fake_init,
                    now_func=lambda: 'stamp',
                )
                self.assertEqual(events, [('backup', imp.BACKUP_TAG), ('init', None)])
                self.assertEqual(file_hash(source), source_before)
                self.assertEqual(result.summary['nuevos'], 1)
                self.assertEqual(result.summary['existentes'], 1)

                check = sqlite3.connect(backup)
                try:
                    self.assertEqual(check.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    self.assertEqual(check.execute('SELECT empresa FROM proveedores').fetchone()[0], 'Local con WAL')
                    self.assertIsNone(check.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='init_marker'"
                    ).fetchone())
                finally:
                    check.close()

                verify = sqlite3.connect(target)
                try:
                    self.assertIsNotNone(verify.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='init_marker'"
                    ).fetchone())
                    self.assertEqual(verify.execute(
                        'SELECT saldo FROM proveedores WHERE legacy_id=1'
                    ).fetchone()[0], 777.0)
                    self.assertEqual(verify.execute(
                        'SELECT COUNT(*) FROM proveedores'
                    ).fetchone()[0], 2)
                finally:
                    verify.close()
            finally:
                keeper.close()

    def test_backup_failure_prevents_init_and_import(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'historico.db')
            target = os.path.join(temp, 'pos.db')
            create_source(source, [source_row()])
            conn = sqlite3.connect(target)
            conn.executescript(TARGET_SCHEMA)
            conn.close()
            events = []

            def failing_backup(tag):
                events.append('backup')
                raise OSError('no hay espacio')

            def fake_init():
                events.append('init')

            with self.assertRaisesRegex(OSError, 'no hay espacio'):
                imp.run_import(
                    source, target, backup_func=failing_backup, init_db_func=fake_init,
                    now_func=lambda: 'stamp',
                )
            self.assertEqual(events, ['backup'])

    def test_invalid_backup_file_prevents_init(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'historico.db')
            target = os.path.join(temp, 'pos.db')
            bad_backup = os.path.join(temp, 'malo.db')
            create_source(source, [source_row()])
            conn = sqlite3.connect(target)
            conn.executescript(TARGET_SCHEMA)
            conn.close()
            Path(bad_backup).write_bytes(b'esto no es sqlite')
            events = []

            def fake_backup(tag):
                Path(bad_backup).write_bytes(b'esto no es sqlite')
                return bad_backup

            def fake_init():
                events.append('init')

            with self.assertRaises(imp.ImportFailure):
                imp.run_import(
                    source, target, backup_func=fake_backup, init_db_func=fake_init,
                    now_func=lambda: 'stamp',
                )
            self.assertEqual(events, [])

    def test_historical_file_cannot_be_returned_as_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'historico.db')
            target = os.path.join(temp, 'pos.db')
            create_source(source, [source_row()])
            conn = sqlite3.connect(target)
            conn.executescript(TARGET_SCHEMA)
            conn.close()
            events = []

            def wrong_backup(tag):
                return source

            def fake_init():
                events.append('init')

            with self.assertRaises(imp.ImportFailure):
                imp.run_import(
                    source, target, backup_func=wrong_backup, init_db_func=fake_init,
                    now_func=lambda: 'stamp',
                )
            self.assertEqual(events, [])

    def test_missing_target_is_not_initialized_without_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            source = os.path.join(temp, 'historico.db')
            target = os.path.join(temp, 'no-existe.db')
            create_source(source, [source_row()])
            events = []

            def fake_backup(tag):
                events.append('backup')

            def fake_init():
                events.append('init')

            with self.assertRaises(imp.ImportFailure):
                imp.run_import(
                    source, target, backup_func=fake_backup, init_db_func=fake_init,
                    now_func=lambda: 'stamp',
                )
            self.assertEqual(events, [])
            self.assertFalse(os.path.exists(target))

    def test_source_and_target_cannot_be_the_same_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'igual.db')
            create_source(path, [source_row()])
            with self.assertRaises(imp.ImportFailure):
                imp.run_import(path, path, dry_run=True)


if __name__ == '__main__':
    unittest.main()
