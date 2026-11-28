"""
Importa proveedores del histórico 1box a pos.db de forma no destructiva.

El histórico se abre en modo solo lectura. Antes de ejecutar ``init_db`` se
crea y verifica un respaldo consistente con la API de SQLite. La planeación y
el UPSERT se hacen dentro de una misma transacción ``BEGIN IMMEDIATE`` para no
sobrescribir cambios locales ocurridos entre el dry-run y la importación.

Por defecto sólo se completan campos vacíos: los datos existentes, incluidos
empresa, contacto, RFC, teléfono, correo, saldo, notas y estado activo, se
conservan. ``--sobrescribir`` permite reemplazar los campos administrados,
saldo y activo; las notas siempre se conservan porque el histórico no las
contiene. Los RFC, correos y teléfonos inválidos no se corrigen ni se inventan:
se omiten del registro y se reportan en el dry-run y en la auditoría.

Uso:
    runtime\\python.exe importar_proveedores.py --dry-run
    runtime\\python.exe importar_proveedores.py
    runtime\\python.exe importar_proveedores.py --sobrescribir
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import pos_core


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORICO_DB = os.path.join(BASE_DIR, 'Historico', 'historico_1box.db')
BACKUP_TAG = 'pre_import_proveedores'
AUDIT_ACTION = 'proveedores_import'

RECORD_COLUMNS = (
    'legacy_id', 'empresa', 'nombre_contacto', 'apellido_contacto', 'rfc',
    'telefono', 'email', 'direccion', 'ciudad', 'estado', 'cp', 'saldo',
    'notas', 'activo', 'origen', 'creado_en', 'actualizado_en',
)
MANAGED_FIELDS = (
    'empresa', 'nombre_contacto', 'apellido_contacto', 'rfc', 'telefono',
    'email', 'direccion', 'ciudad', 'estado', 'cp',
)
PROTECTED_FIELDS = ('saldo', 'notas', 'activo')
OVERWRITABLE_FIELDS = ('saldo', 'activo')  # El histórico no aporta notas.

SOURCE_SQL = """
    SELECT s.id, s.person_id, s.first_name, s.last_name, s.company_name,
           s.account_number, s.balance, s.deleted,
           p.first_name AS p_first_name, p.last_name AS p_last_name,
           p.phone_number, p.email, p.address_1, p.city, p.state, p.zip
    FROM suppliers s
    LEFT JOIN people p ON s.person_id = p.person_id
    ORDER BY s.id ASC
"""

_RFC_RE = re.compile(r'^[A-Z&Ñ]{3,4}[0-9]{6}[A-Z0-9]{3}$')
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_PHONE_SEPARATOR_RE = re.compile(
    r'(?:/|;|\||,|\r|\n|\s+(?:y|and|&)\s+)', re.IGNORECASE
)
_PHONE_LABEL_RE = re.compile(
    r'^(?:tel(?:éfono)?|cel(?:ular)?|casa|oficina)\.?\s*[:.\-]?\s*',
    re.IGNORECASE,
)
_EXTENSION_RE = re.compile(
    r'(?:\bext(?:ensi[oó]n)?\.?|\bx|#)\s*[:.\-]?\s*([0-9]{1,6})\s*$',
    re.IGNORECASE,
)


class ImportFailure(RuntimeError):
    """Error que impide iniciar o completar la importación de forma segura."""


class InvalidSupplier(ValueError):
    """Fila del histórico que no puede importarse sin inventar datos."""

    def __init__(self, issue):
        super().__init__(issue.reason)
        self.issue = issue


@dataclass(frozen=True)
class ValidationIssue:
    legacy_id: object
    field: str
    value: str
    reason: str
    fatal: bool = False


@dataclass(frozen=True)
class RejectedRecord:
    legacy_id: object
    issue: ValidationIssue


@dataclass(frozen=True)
class SupplierRecord:
    legacy_id: int
    empresa: str
    nombre_contacto: object
    apellido_contacto: object
    rfc: object
    telefono: object
    email: object
    direccion: object
    ciudad: object
    estado: object
    cp: object
    saldo: float
    notas: object
    activo: int
    origen: str
    creado_en: str
    actualizado_en: str
    issues: tuple = ()

    def values(self):
        return tuple(getattr(self, column) for column in RECORD_COLUMNS)

    def as_dict(self):
        return {column: getattr(self, column) for column in RECORD_COLUMNS}


@dataclass(frozen=True)
class PreparedImport:
    records: tuple
    rejected: tuple

    @property
    def issues(self):
        issues = []
        for rejected in self.rejected:
            issues.append(rejected.issue)
        for record in self.records:
            issues.extend(record.issues)
        return tuple(issues)

    @property
    def total(self):
        return len(self.records) + len(self.rejected)


@dataclass(frozen=True)
class LocalChange:
    field: str
    local_value: object
    source_value: object
    will_overwrite: bool


@dataclass(frozen=True)
class ImportAction:
    kind: str  # new, update, unchanged, skip
    record: object = None
    existing: object = None
    updates: tuple = ()
    local_changes: tuple = ()


@dataclass(frozen=True)
class ImportPlan:
    actions: tuple
    rejected: tuple

    @property
    def new(self):
        return tuple(action for action in self.actions if action.kind == 'new')

    @property
    def updates(self):
        return tuple(action for action in self.actions if action.kind == 'update')

    @property
    def unchanged(self):
        return tuple(action for action in self.actions if action.kind == 'unchanged')

    @property
    def existing(self):
        return self.updates + self.unchanged

    @property
    def local_changes(self):
        return tuple(action for action in self.existing if action.local_changes)

    @property
    def issues(self):
        issues = []
        for rejected in self.rejected:
            issues.append(rejected.issue)
        for action in self.actions:
            if action.record is not None:
                issues.extend(action.record.issues)
        return tuple(issues)

    @property
    def summary(self):
        changes = [change for action in self.local_changes for change in action.local_changes]
        return {
            'leidos': len(self.actions) + len(self.rejected),
            'nuevos': len(self.new),
            'existentes': len(self.existing),
            'actualizaciones': len(self.updates),
            'sin_cambios': len(self.unchanged),
            'omitidos': len(self.rejected),
            'cambios_locales': len(self.local_changes),
            'campos_locales_preservados': sum(not change.will_overwrite for change in changes),
            'campos_locales_sobrescritos': sum(change.will_overwrite for change in changes),
            'datos_invalidos': len(self.issues),
        }


@dataclass(frozen=True)
class ImportResult:
    plan: ImportPlan
    source_path: str
    dry_run: bool
    sobrescribir: bool
    stamp: str
    backup_path: object = None
    audit_id: object = None
    total_suppliers: object = None
    active_suppliers: object = None

    @property
    def summary(self):
        return self.plan.summary


# ------------------------------------------------------------------ validación

def clean(value):
    if value is None:
        return ''
    return ' '.join(str(value).split())


def _row_value(row, key):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        raise ImportFailure(f'El histórico no contiene la columna obligatoria {key}.')


def _issue(legacy_id, field, value, reason, fatal=False):
    text = clean(value)
    if len(text) > 240:
        text = text[:237] + '...'
    return ValidationIssue(legacy_id, field, text, reason, fatal)


def _parse_integer(value, field, legacy_id, minimum=None):
    if isinstance(value, bool):
        number = int(value)
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise InvalidSupplier(_issue(legacy_id, field, value, f'{field} debe ser un entero válido.', True))
        number = int(value)
    elif isinstance(value, str) and re.fullmatch(r'[+-]?[0-9]+', clean(value)):
        number = int(clean(value))
    else:
        raise InvalidSupplier(_issue(legacy_id, field, value, f'{field} debe ser un entero válido.', True))
    if minimum is not None and number < minimum:
        raise InvalidSupplier(_issue(legacy_id, field, value, f'{field} debe ser mayor o igual que {minimum}.', True))
    return number


def _parse_balance(value, legacy_id):
    if value is None or clean(value) == '':
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise InvalidSupplier(_issue(legacy_id, 'saldo', value, 'El saldo no es un número válido.', True))
    if not math.isfinite(number):
        raise InvalidSupplier(_issue(legacy_id, 'saldo', value, 'El saldo debe ser finito.', True))
    return number


def validate_rfc(value):
    """Valida la sintaxis RFC sin completar dígitos ni aplicar correcciones."""
    raw = clean(value)
    if not raw:
        return None, None
    normalized = re.sub(r'[\s-]+', '', raw).upper()
    if not _RFC_RE.fullmatch(normalized):
        return None, 'RFC con sintaxis inválida; no se utilizará el valor.'
    return normalized, None


def validate_email(value):
    """Valida un correo único; no divide, completa ni autocorrige direcciones."""
    raw = clean(value)
    if not raw:
        return None, None
    if len(raw) > 254 or any(ch.isspace() for ch in raw):
        return None, 'Correo electrónico inválido; no se utilizará el valor.'
    local, separator, domain = raw.rpartition('@')
    if not separator or not local or not domain or '@' in local:
        return None, 'Correo electrónico inválido; no se utilizará el valor.'
    if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local:
        return None, 'Correo electrónico inválido; no se utilizará el valor.'
    if not _EMAIL_RE.fullmatch(raw):
        return None, 'Correo electrónico inválido; no se utilizará el valor.'
    return raw, None


def parse_phone(value):
    """
    Conserva cada teléfono y su extensión como elementos separados.

    Devuelve ``(texto_normalizado, error)``. Varios números se separan con
    ``; `` y una extensión se conserva como ``ext. N``. Una cadena ambigua o
    concatenada se marca inválida en lugar de dividirla adivinando.
    """
    if value is None or clean(value) == '':
        return None, None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None, 'Teléfono inválido; no se utilizará el valor.'
        raw = str(int(value))
    else:
        raw = str(value)

    parts = [part.strip() for part in _PHONE_SEPARATOR_RE.split(raw) if part.strip()]
    if not parts:
        return None, 'Teléfono inválido; no se utilizará el valor.'

    normalized = []
    for part in parts:
        part = _PHONE_LABEL_RE.sub('', part).strip()
        extension = None
        match = _EXTENSION_RE.search(part)
        if match:
            extension = match.group(1)
            part = part[:match.start()].strip()
        part = re.sub(r'[\s.]+$', '', part)
        if not part or not re.fullmatch(r'[+()0-9\s.\-]+', part):
            return None, 'Teléfono ambiguo o con caracteres inválidos; no se utilizará el valor.'
        digits = re.sub(r'[^0-9]', '', part)
        if len(digits) == 12 and digits.startswith('52'):
            digits = digits[2:]
        if not digits or len(digits) > 15:
            return None, 'Teléfono inválido o concatenado; no se utilizará el valor.'
        phone = f'{digits} ext. {extension}' if extension else digits
        if phone not in normalized:
            normalized.append(phone)
    return '; '.join(normalized), None


def normalize_phone(value):
    """Compatible con el script anterior; ahora no concatena números."""
    return parse_phone(value)[0]


def supplier_values(row, stamp):
    """Convierte una fila del histórico en un registro validado."""
    raw_id = _row_value(row, 'id')
    legacy_id = _parse_integer(raw_id, 'id', raw_id, minimum=1)
    company = clean(_row_value(row, 'company_name'))
    first = clean(_row_value(row, 'first_name')) or clean(_row_value(row, 'p_first_name'))
    last = clean(_row_value(row, 'last_name')) or clean(_row_value(row, 'p_last_name'))
    company = company or clean(' '.join(part for part in (first, last) if part))
    if not company:
        raise InvalidSupplier(_issue(
            legacy_id, 'empresa', '', 'Falta el nombre de la empresa y del contacto; no se inventará un proveedor.', True,
        ))

    rfc, rfc_error = validate_rfc(_row_value(row, 'account_number'))
    phone, phone_error = parse_phone(_row_value(row, 'phone_number'))
    email, email_error = validate_email(_row_value(row, 'email'))
    issues = []
    for field, value, error in (
        ('rfc', _row_value(row, 'account_number'), rfc_error),
        ('telefono', _row_value(row, 'phone_number'), phone_error),
        ('email', _row_value(row, 'email'), email_error),
    ):
        if error:
            issues.append(_issue(legacy_id, field, value, error))

    balance = _parse_balance(_row_value(row, 'balance'), legacy_id)
    deleted = _parse_integer(_row_value(row, 'deleted') or 0, 'deleted', legacy_id, minimum=0)
    if deleted not in (0, 1):
        raise InvalidSupplier(_issue(legacy_id, 'deleted', deleted, 'deleted debe ser 0 o 1.', True))

    return SupplierRecord(
        legacy_id=legacy_id,
        empresa=company,
        nombre_contacto=first or None,
        apellido_contacto=last or None,
        rfc=rfc,
        telefono=phone,
        email=email,
        direccion=clean(_row_value(row, 'address_1')) or None,
        ciudad=clean(_row_value(row, 'city')) or None,
        estado=clean(_row_value(row, 'state')) or None,
        cp=clean(_row_value(row, 'zip')) or None,
        saldo=balance,
        notas=None,
        activo=0 if deleted else 1,
        origen='historico_1box',
        creado_en=stamp,
        actualizado_en=stamp,
        issues=tuple(issues),
    )


def prepare_records(rows, stamp):
    """Valida todas las filas sin detener la importación por una fila inválida."""
    records = []
    rejected = []
    seen = set()
    for row in rows:
        raw_id = _row_value(row, 'id')
        try:
            record = supplier_values(row, stamp)
        except InvalidSupplier as exc:
            rejected.append(RejectedRecord(clean(raw_id) or '?', exc.issue))
            continue
        if record.legacy_id in seen:
            issue = _issue(record.legacy_id, 'id', record.legacy_id, 'ID duplicado en el histórico.', True)
            rejected.append(RejectedRecord(record.legacy_id, issue))
            continue
        seen.add(record.legacy_id)
        records.append(record)
    return PreparedImport(tuple(records), tuple(rejected))


# -------------------------------------------------------------------- planeación

def _equivalent(field, left, right):
    if field == 'saldo':
        try:
            return math.isclose(float(left or 0), float(right or 0), rel_tol=0, abs_tol=1e-9)
        except (TypeError, ValueError):
            return left == right
    if field == 'activo':
        try:
            return int(left or 0) == int(right or 0)
        except (TypeError, ValueError):
            return left == right
    if field in ('rfc', 'email'):
        return clean(left).casefold() == clean(right).casefold()
    return clean(left) == clean(right)


def _invalid_source_fields(record):
    return {issue.field for issue in record.issues}


def build_plan(prepared, existing_by_id, sobrescribir=False):
    """Calcula cambios sin escribir; ``existing_by_id`` se indexa por legacy_id.

    El modo normal sólo completa campos vacíos. Cualquier reemplazo de datos
    existentes requiere ``sobrescribir=True`` y nunca sustituye un valor local
    válido por un dato inválido del histórico.
    """
    actions = []
    for record in prepared.records:
        existing = existing_by_id.get(record.legacy_id)
        if existing is None:
            actions.append(ImportAction('new', record=record))
            continue

        incoming = record.as_dict()
        invalid_fields = _invalid_source_fields(record)
        overwritable = set(MANAGED_FIELDS) | set(OVERWRITABLE_FIELDS)
        local_changes = []
        for field in MANAGED_FIELDS + PROTECTED_FIELDS:
            if _equivalent(field, existing.get(field), incoming.get(field)):
                continue
            source_is_invalid = field in invalid_fields and incoming.get(field) is None
            will_overwrite = bool(
                sobrescribir
                and field in overwritable
                and not source_is_invalid
                and not _equivalent(field, existing.get(field), incoming.get(field))
            )
            local_changes.append(LocalChange(
                field, existing.get(field), incoming.get(field), will_overwrite,
            ))

        updates = []
        for field in MANAGED_FIELDS:
            old_value = existing.get(field)
            new_value = incoming.get(field)
            if _equivalent(field, old_value, new_value):
                continue
            if new_value is None and (not sobrescribir or field in invalid_fields):
                continue
            # En modo conservador, un dato local no vacío siempre se preserva.
            if not sobrescribir and clean(old_value):
                continue
            updates.append(field)

        if sobrescribir:
            for field in OVERWRITABLE_FIELDS:
                if not _equivalent(field, existing.get(field), incoming.get(field)):
                    updates.append(field)

        actions.append(ImportAction(
            'update' if updates else 'unchanged',
            record=record,
            existing=existing,
            updates=tuple(updates),
            local_changes=tuple(local_changes),
        ))
    return ImportPlan(tuple(actions), prepared.rejected)


# --------------------------------------------------------------------- SQLite

def _sqlite_uri(path, mode):
    return Path(path).resolve().as_uri() + f'?mode={mode}'


def _same_path(left, right):
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def read_source(path):
    if not os.path.isfile(path):
        raise ImportFailure(f'No se encontró el histórico: {path}')
    conn = sqlite3.connect(_sqlite_uri(path, 'ro'), uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only = ON')
    try:
        return conn.execute(SOURCE_SQL).fetchall()
    except sqlite3.Error as exc:
        raise ImportFailure(f'No se pudo leer el histórico en modo solo lectura: {exc}') from exc
    finally:
        conn.close()


def connect_target(path, readonly=False):
    conn = sqlite3.connect(_sqlite_uri(path, 'ro' if readonly else 'rw'), uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 30000')
    if readonly:
        conn.execute('PRAGMA query_only = ON')
    return conn


def _table_columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def load_existing(conn, prepared, require_table=False):
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='proveedores'"
    ).fetchone()
    if not table:
        if require_table:
            raise ImportFailure('La base no contiene la tabla proveedores después de init_db.')
        return {}
    columns = _table_columns(conn, 'proveedores')
    required = set(RECORD_COLUMNS)
    missing = sorted(required - columns)
    if missing:
        raise ImportFailure('Faltan columnas en proveedores: ' + ', '.join(missing))

    existing = {}
    legacy_ids = [record.legacy_id for record in prepared.records]
    # Divide la consulta para respetar el límite de variables de SQLite.
    for start in range(0, len(legacy_ids), 800):
        chunk = legacy_ids[start:start + 800]
        placeholders = ','.join('?' for _ in chunk)
        rows = conn.execute(
            f'SELECT {", ".join(RECORD_COLUMNS)} FROM proveedores '
            f'WHERE legacy_id IN ({placeholders})',
            chunk,
        ).fetchall()
        for row in rows:
            item = dict(row) if hasattr(row, 'keys') else dict(zip(RECORD_COLUMNS, row))
            existing[int(item['legacy_id'])] = item
    return existing


def read_existing(path, prepared):
    if not os.path.isfile(path):
        return {}
    conn = connect_target(path, readonly=True)
    try:
        return load_existing(conn, prepared, require_table=False)
    except sqlite3.Error as exc:
        raise ImportFailure(f'No se pudo leer pos.db para el dry-run: {exc}') from exc
    finally:
        conn.close()


def read_target_counts(path):
    """Devuelve el total real, incluidos proveedores que no están en el histórico."""
    if not os.path.isfile(path):
        return 0, 0
    conn = connect_target(path, readonly=True)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='proveedores'"
        ).fetchone()
        if not table:
            return 0, 0
        total = conn.execute('SELECT COUNT(*) FROM proveedores').fetchone()[0]
        active = conn.execute('SELECT COUNT(*) FROM proveedores WHERE activo=1').fetchone()[0]
        return int(total), int(active)
    except sqlite3.Error as exc:
        raise ImportFailure(f'No se pudo contar proveedores para el dry-run: {exc}') from exc
    finally:
        conn.close()


def verify_backup_file(path, target_path, source_path=None):
    """Verifica integridad del archivo devuelto por la API de respaldo."""
    if not path or not os.path.isfile(path):
        raise ImportFailure('La API de respaldo no devolvió un archivo existente.')
    if _same_path(path, target_path):
        raise ImportFailure('El respaldo no puede ser la propia base de destino.')
    if source_path and _same_path(path, source_path):
        raise ImportFailure('El respaldo no puede ser el archivo del histórico.')
    conn = None
    try:
        conn = sqlite3.connect(_sqlite_uri(path, 'ro'), uri=True, timeout=15)
        rows = conn.execute('PRAGMA integrity_check').fetchall()
        if [row[0] for row in rows] != ['ok']:
            raise ImportFailure('El respaldo no pasó PRAGMA integrity_check.')
    except sqlite3.Error as exc:
        raise ImportFailure(f'No se pudo verificar el respaldo: {exc}') from exc
    finally:
        if conn is not None:
            conn.close()
    return os.path.abspath(path)


def _upsert(conn, action, stamp):
    record = action.record
    values = list(record.values())
    values[RECORD_COLUMNS.index('actualizado_en')] = stamp
    placeholders = ','.join('?' for _ in RECORD_COLUMNS)
    sql = (
        f'INSERT INTO proveedores({",".join(RECORD_COLUMNS)}) '
        f'VALUES({placeholders})'
    )
    if action.kind == 'update':
        update_fields = tuple(action.updates) + ('actualizado_en',)
        assignments = ','.join(f'{field}=excluded.{field}' for field in update_fields)
        sql += f' ON CONFLICT(legacy_id) DO UPDATE SET {assignments}'
    conn.execute(sql, values)


def apply_import(conn, prepared, stamp, sobrescribir=False, source_path='', backup_path=None):
    """
    Planea y aplica el UPSERT y la auditoría en una única transacción.

    La conexión debe estar sin transacción activa y no se cierra aquí, lo que
    permite probar el compromiso y el rollback con bases en memoria.
    """
    if conn.in_transaction:
        raise ImportFailure('La conexión de importación ya tiene una transacción activa.')
    conn.execute('BEGIN IMMEDIATE')
    try:
        existing = load_existing(conn, prepared, require_table=True)
        plan = build_plan(prepared, existing, sobrescribir=sobrescribir)
        for action in plan.actions:
            if action.kind in ('new', 'update'):
                _upsert(conn, action, stamp)

        detail = {
            'modo': 'sobrescribir' if sobrescribir else 'no_destructivo',
            'fuente': os.path.abspath(source_path) if source_path else None,
            'respaldo': os.path.abspath(backup_path) if backup_path else None,
            'resumen': plan.summary,
            'campos_invalidos': sorted({issue.field for issue in plan.issues}),
        }
        cur = conn.execute(
            'INSERT INTO audit(at, user_id, action, detail) VALUES(?,?,?,?)',
            (stamp, None, AUDIT_ACTION, json.dumps(detail, ensure_ascii=False, sort_keys=True)),
        )
        audit_id = cur.lastrowid
        total = conn.execute('SELECT COUNT(*) FROM proveedores').fetchone()[0]
        active = conn.execute('SELECT COUNT(*) FROM proveedores WHERE activo=1').fetchone()[0]
        result = ImportResult(
            plan=plan,
            source_path=source_path,
            dry_run=False,
            sobrescribir=sobrescribir,
            stamp=stamp,
            backup_path=backup_path,
            audit_id=audit_id,
            total_suppliers=total,
            active_suppliers=active,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def run_import(
    source_path=HISTORICO_DB,
    target_path=None,
    *,
    dry_run=False,
    sobrescribir=False,
    backup_func=None,
    init_db_func=None,
    connect_func=None,
    now_func=None,
):
    """Orquesta el importador; las dependencias son inyectables para pruebas."""
    target_path = target_path or pos_core.POS_DB
    source_path = os.path.abspath(source_path)
    target_path = os.path.abspath(target_path)
    if _same_path(source_path, target_path):
        raise ImportFailure('El histórico y la base de destino no pueden ser el mismo archivo.')
    if not os.path.isfile(source_path):
        raise ImportFailure(f'No se encontró el histórico: {source_path}')
    if not dry_run and not os.path.isfile(target_path):
        raise ImportFailure(
            f'No existe la base de destino: {target_path}. No se creará ni se inicializará '
            'sin un respaldo previo; ejecuta init_db por separado primero.'
        )
    if not dry_run and backup_func is None and not _same_path(target_path, pos_core.POS_DB):
        raise ImportFailure('Una base alternativa requiere una función de respaldo explícita.')

    rows = read_source(source_path)
    stamp = (now_func or pos_core.now_text)()
    prepared = prepare_records(rows, stamp)

    if dry_run:
        existing = read_existing(target_path, prepared)
        plan = build_plan(prepared, existing, sobrescribir=sobrescribir)
        total, active = read_target_counts(target_path)
        for action in plan.actions:
            if action.kind == 'new':
                total += 1
                active += int(action.record.activo)
            elif 'activo' in action.updates:
                active += int(action.record.activo) - int(action.existing.get('activo') or 0)
        return ImportResult(
            plan=plan,
            source_path=source_path,
            dry_run=True,
            sobrescribir=sobrescribir,
            stamp=stamp,
            total_suppliers=total,
            active_suppliers=active,
        )

    # El respaldo debe ocurrir antes de init_db: init_db puede migrar/escribir.
    backup_func = backup_func or pos_core.backup
    backup_path = backup_func(BACKUP_TAG)
    backup_path = verify_backup_file(backup_path, target_path, source_path)

    init_db_func = init_db_func or pos_core.init_db
    init_db_func()

    connect_func = connect_func or connect_target
    conn = connect_func(target_path)
    try:
        return apply_import(
            conn, prepared, stamp, sobrescribir=sobrescribir,
            source_path=source_path, backup_path=backup_path,
        )
    finally:
        conn.close()


# --------------------------------------------------------------------- reporte

def _format_value(value):
    if value is None:
        return 'NULL'
    if isinstance(value, float):
        return f'{value:.2f}'
    return json.dumps(value, ensure_ascii=False)


def _id_list(items):
    return ', '.join(str(item) for item in items) if items else '—'


def print_report(result, stream=None):
    stream = stream or sys.stdout
    summary = result.summary
    mode = 'DRY-RUN' if result.dry_run else 'IMPORTACIÓN'
    print(f'{mode} DE PROVEEDORES', file=stream)
    print(f'Fuente (solo lectura): {result.source_path}', file=stream)
    if result.sobrescribir:
        print('Modo explícito: --sobrescribir (saldo y activo pueden cambiar).', file=stream)
    else:
        print('Modo no destructivo: se conservan saldo, notas y activo locales.', file=stream)

    print(f'Nuevos: {summary["nuevos"]} ({_id_list(action.record.legacy_id for action in result.plan.new)})', file=stream)
    print(
        f'Existentes: {summary["existentes"]} '
        f'({_id_list(action.record.legacy_id for action in result.plan.existing)})',
        file=stream,
    )
    print(
        f'Cambios locales: {summary["cambios_locales"]} '
        f'(preservados: {summary["campos_locales_preservados"]}, '
        f'sobrescritos: {summary["campos_locales_sobrescritos"]})',
        file=stream,
    )
    update_label = 'Actualizaciones previstas' if result.dry_run else 'Actualizaciones aplicadas'
    print(f'{update_label}: {summary["actualizaciones"]}', file=stream)
    print(f'Sin cambios: {summary["sin_cambios"]}', file=stream)
    print(f'Datos inválidos: {summary["datos_invalidos"]}', file=stream)

    for action in result.plan.existing:
        for change in action.local_changes:
            decision = 'se sobrescribirá' if change.will_overwrite else 'se conserva el valor local'
            print(
                f'  Cambio local legacy {action.record.legacy_id} · {change.field}: '
                f'local={_format_value(change.local_value)}, '
                f'histórico={_format_value(change.source_value)} ({decision})',
                file=stream,
            )
    for action in result.plan.updates:
        print(
            f'  Actualizar legacy {action.record.legacy_id}: {", ".join(action.updates)}',
            file=stream,
        )
    for issue in result.plan.issues:
        value = f' valor={_format_value(issue.value)}' if issue.value else ''
        consequence = 'se omite el registro' if issue.fatal else 'el valor inválido no se utilizará'
        print(
            f'  Inválido legacy {issue.legacy_id} · {issue.field}: {issue.reason}'
            f'{value} ({consequence})',
            file=stream,
        )

    if result.dry_run:
        print('Dry-run completado: no se creó respaldo, no se llamó init_db y no se modificó pos.db.', file=stream)
    else:
        print(f'Respaldo consistente verificado: {result.backup_path}', file=stream)
        print(
            f'Importación completada: {summary["nuevos"]} nuevos, '
            f'{summary["actualizaciones"]} actualizados, {summary["omitidos"]} omitidos.',
            file=stream,
        )
        print(
            f'Total en proveedores: {result.total_suppliers} '
            f'(activos: {result.active_suppliers}).',
            file=stream,
        )
        print(f'Auditoría resumen: {AUDIT_ACTION} #{result.audit_id}.', file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Importa proveedores del histórico 1box a pos.db')
    parser.add_argument('--dry-run', action='store_true', help='Reporta cambios sin respaldar ni escribir pos.db')
    parser.add_argument(
        '--sobrescribir', action='store_true',
        help='Permite sobrescribir saldo y activo existentes; las notas siempre se conservan',
    )
    args = parser.parse_args(argv)
    try:
        result = run_import(dry_run=args.dry_run, sobrescribir=args.sobrescribir)
        print_report(result)
        return 0
    except (ImportFailure, sqlite3.Error, OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
