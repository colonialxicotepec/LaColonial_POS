"""
Audita el paquete ZIP de distribución contra el estado actual del proyecto.

Comprueba, archivo por archivo:
  - que lo que está dentro del ZIP sea idéntico al archivo del proyecto (SHA-256)
  - que no falte ningún archivo importante
  - que las bases (pos.db, historico_1box.db) coincidan en contenido
  - que el MANIFIESTO.txt del ZIP sea correcto

Uso:
    python verificar_paquete.py                    # audita el ZIP más reciente
    python verificar_paquete.py dist\\LaColonial_POS_....zip
"""

import hashlib
import os
import sqlite3
import sys
import tempfile
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, 'dist')

# archivos que deben viajar en el paquete
ESPERADOS = [
    'app_pos.py', 'catalogo.py', 'pos_api.py', 'pos_core.py', 'pos_print.py',
    'logo.ico', 'ui/logo.png',
    'verificar_instalacion.py', 'empaquetar_proyecto.py',
    'core/suppliers.py', 'importar_proveedores.py', 'ui/js/suppliers.js',
    'reiniciar_caja.py', 'restaurar_respaldo.py',
    'pos_config.json',
    'caja.bat', 'caja_en_red.bat', 'abrir_puerto_firewall.bat',
    'buscar_productos.bat',
    'instalar_python_portable.bat', 'verificar_instalacion.bat',
    'empaquetar_proyecto.bat',
    'README.md', 'README_POS.md',
    'INSTALACION.md', 'MANUAL_RAPIDO.md', 'ARQUITECTURA.md',
    'pos.db',
]

# carpetas que deben viajar completas
CARPETAS = ['core', 'ui', 'runtime', 'vendor', 'Historico']

problemas = []


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            block = fh.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def resumen_db(path):
    """Huella con el contenido real de la base (no el archivo binario)."""
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        tablas = sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
        out = {'tablas': len(tablas)}
        for tabla, cols in (('products', 'id, sku, name, sale_price, stock'),
                            ('users', 'id, name, role'),
                            ('sales', 'id, folio, total'),
                            ('stock_movements', 'id, product_id, kind, qty'),
                            ('shifts', 'id, status'),
                            ('items', 'item_id, name'),
                            ('proveedores', 'id, legacy_id, empresa, nombre_contacto, '
                                            'apellido_contacto, rfc, telefono, email, '
                                            'direccion, ciudad, estado, cp, saldo, notas, '
                                            'activo, origen, creado_en, actualizado_en'),
                            ('sales', 'sale_id, total')):
            try:
                rows = conn.execute(f'SELECT {cols} FROM {tabla}').fetchall()
            except sqlite3.Error:
                continue
            huella = hashlib.sha256(repr(sorted(map(str, rows))).encode()).hexdigest()[:16]
            out[tabla] = (len(rows), huella)
        return out
    finally:
        conn.close()


def main():
    if len(sys.argv) > 1:
        zip_path = sys.argv[1]
    else:
        if not os.path.isdir(DIST_DIR):
            print('ERROR: no hay carpeta dist/ aquí.')
            print('Indica el paquete:  python verificar_paquete.py ruta\\al\\paquete.zip')
            return 1
        zips = sorted((os.path.join(DIST_DIR, f) for f in os.listdir(DIST_DIR)
                       if f.endswith('.zip')), key=os.path.getmtime, reverse=True)
        if not zips:
            print('ERROR: no hay ningún ZIP en dist/.')
            return 1
        zip_path = zips[0]
    if not os.path.exists(zip_path):
        print(f'ERROR: no existe el paquete {zip_path}.')
        return 1

    print('=' * 70)
    print('  AUDITORÍA DEL PAQUETE DE DISTRIBUCIÓN')
    print('=' * 70)
    print(f'  Paquete : {os.path.basename(zip_path)}')
    print(f'  Tamaño  : {os.path.getsize(zip_path) / (1024 ** 2):.1f} MB')
    print(f'  SHA-256 : {sha256_file(zip_path)}')
    print('-' * 70)

    with zipfile.ZipFile(zip_path) as zf:
        nombres = set(zf.namelist())
        entradas = {n: zf.read(n) for n in nombres if not n.endswith('/')}

        # 1. Archivos esperados
        faltan = [n for n in ESPERADOS if n not in nombres]
        if faltan:
            problemas.append(f'Faltan en el ZIP: {", ".join(faltan)}')
        print(f'  [OK   ] Archivos requeridos presentes' if not faltan
              else f'  [FALLA] Faltan: {", ".join(faltan)}')

        # 2. Carpetas completas
        for carpeta in CARPETAS:
            if not any(n.startswith(carpeta + '/') for n in nombres):
                problemas.append(f'Falta la carpeta {carpeta}/')
        print(f'  [OK   ] Carpetas: {", ".join(CARPETAS)}')

        # 3. Contenido idéntico al proyecto
        #    Las bases SQLite se copian con la API de respaldo: mismo contenido,
        #    bytes distintos (páginas y WAL reescritos). Por eso se comparan
        #    por contenido en el paso 5, no byte a byte.
        distintos = []
        for nombre, data in entradas.items():
            if nombre in ('MANIFIESTO.txt', 'LEEME-INSTALACION.txt'):
                continue
            if nombre.endswith('.db'):
                continue
            origen = os.path.join(BASE_DIR, nombre.replace('/', os.sep))
            if not os.path.exists(origen):
                problemas.append(f'El ZIP trae {nombre}, que no está en el proyecto')
                continue
            if sha256_file(origen) != sha256_bytes(data):
                distintos.append(nombre)
        if distintos:
            problemas.append(f'Contenido desactualizado en el ZIP: {", ".join(distintos)}')
        print(f'  [OK   ] Archivos idénticos al proyecto ({len(entradas) - 2} revisados)'
              if not distintos else f'  [FALLA] Desactualizados: {", ".join(distintos)}')

        # 4. MANIFIESTO
        if 'MANIFIESTO.txt' in entradas:
            lineas = entradas['MANIFIESTO.txt'].decode('utf-8', 'replace').splitlines()
            mal = 0
            declarados = set()
            for linea in lineas:
                if '  ' not in linea:
                    continue
                huella, nombre = linea.split('  ', 1)
                nombre = nombre.strip()
                declarados.add(nombre)
                if nombre not in entradas or sha256_bytes(entradas[nombre]) != huella:
                    mal += 1
            sin_manifiesto = set(entradas) - declarados - {
                'MANIFIESTO.txt', 'LEEME-INSTALACION.txt',
            }
            if sin_manifiesto:
                mal += len(sin_manifiesto)
                problemas.append('Faltan en el MANIFIESTO: ' +
                                 ', '.join(sorted(sin_manifiesto)))
            if mal:
                problemas.append(f'{mal} discrepancias en el MANIFIESTO')
            print(f'  [OK   ] MANIFIESTO.txt verificado' if not mal
                  else f'  [FALLA] MANIFIESTO con {mal} discrepancias')
        else:
            problemas.append('Falta MANIFIESTO.txt en el ZIP')

        # 5. Contenido de las bases
        tmpdir = tempfile.mkdtemp(prefix='auditoria_paq_')
        bases_a_revisar = ['pos.db']
        if 'Historico/historico_1box.db' in entradas:
            bases_a_revisar.append('Historico/historico_1box.db')
        elif 'historico_1box.db' in entradas:
            bases_a_revisar.append('historico_1box.db')

        for db in bases_a_revisar:
            tmp = os.path.join(tmpdir, os.path.basename(db))
            with open(tmp, 'wb') as fh:
                fh.write(entradas[db])
            try:
                a = resumen_db(tmp)
                real = os.path.join(BASE_DIR, db.replace('/', os.sep))
                if not os.path.exists(real):
                    real = os.path.join(BASE_DIR, os.path.basename(db))
                b = resumen_db(real)
                proveedores_ok = db != 'pos.db' or (
                    'proveedores' in a and 'proveedores' in b)
                if not proveedores_ok:
                    problemas.append(f'{db}: falta la tabla proveedores en el ZIP o en el proyecto')
                iguales = a == b and proveedores_ok
                if not iguales and proveedores_ok:
                    problemas.append(f'{db}: el contenido del ZIP difiere del proyecto')
                detalle = ', '.join(f'{t}={v[0]}' for t, v in a.items() if isinstance(v, tuple))
                print(f'  [{"OK   " if iguales else "FALLA"}] {os.path.basename(db)}: {detalle}')
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    print('-' * 70)
    if problemas:
        print('  EL PAQUETE NO ESTÁ AL DÍA:')
        for p in problemas:
            print(f'    - {p}')
        print('  Regenera con:  python empaquetar_proyecto.py --con-historico')
    else:
        print('  EL PAQUETE ESTÁ AL DÍA con el proyecto.')
    print('=' * 70)
    return 1 if problemas else 0


if __name__ == '__main__':
    sys.exit(main())
