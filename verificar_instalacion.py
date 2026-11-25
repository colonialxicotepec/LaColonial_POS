"""
Auto-verificación de la instalación del POS.

Úsalo después de copiar el proyecto a una computadora nueva:

    python verificar_instalacion.py

Revisa Python, archivos, base de datos, permisos de escritura, puerto,
impresora y deja un reporte. Termina con código 0 si todo está bien.
"""

import datetime
import json
import os
import shutil
import sqlite3
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(BASE_DIR, 'Historico', 'Auxiliares', 'migracion',
                           'verificacion_instalacion.json')

REQUIRED_FILES = [
    'app_pos.py', 'pos_core.py', 'pos_api.py', 'pos_print.py', 'catalogo.py',
    'pos_config.json', 'pos.db', 'caja.bat',
    'importar_clientes.py',
    os.path.join('ui', 'caja.html'), os.path.join('ui', 'caja.js'),
    os.path.join('ui', 'caja.css'), os.path.join('ui', 'index.html'),
]

results = []


def check(name, ok, detail='', critico=True):
    results.append({'nombre': name, 'ok': bool(ok), 'detalle': str(detail), 'critico': critico})
    marca = 'OK   ' if ok else ('FALLA' if critico else 'AVISO')
    print(f'  [{marca}] {name}' + (f' — {detail}' if detail else ''))
    return ok


def main():
    print('=' * 68)
    print('  VERIFICACIÓN DE INSTALACIÓN — POS LA COLONIAL')
    print('=' * 68)
    print(f'  Carpeta      : {BASE_DIR}')
    print(f'  Fecha        : {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'  Python       : {sys.version.split()[0]} ({sys.executable})')
    print('-' * 68)

    # 1. Python
    check('Python 3.9 o superior', sys.version_info >= (3, 9),
          f'{sys.version.split()[0]} — {"portable" if "runtime" in sys.executable else "instalado en el sistema"}')

    # 2. Archivos del proyecto
    faltan = [f for f in REQUIRED_FILES if not os.path.exists(os.path.join(BASE_DIR, f))]
    check('Archivos del proyecto completos', not faltan,
          'faltan: ' + ', '.join(faltan) if faltan else f'{len(REQUIRED_FILES)} archivos')

    # 3. Base de datos
    db_path = os.path.join(BASE_DIR, 'pos.db')
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
            integridad = conn.execute('PRAGMA integrity_check').fetchone()[0]
            check('Base de datos íntegra (SQLite)', integridad == 'ok', integridad)
            tablas = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            faltan_tablas = {'users', 'products', 'sales', 'shifts', 'stock_movements'} - tablas
            check('Tablas completas', not faltan_tablas,
                  'faltan: ' + ', '.join(faltan_tablas) if faltan_tablas else f'{len(tablas)} tablas')
            customer_tables = {'customers', 'sale_customers'} - tablas
            customer_detail = 'faltan: ' + ', '.join(customer_tables) if customer_tables else (
                f'{conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]:,} clientes importados; '
                f'público general predeterminado: '
                f'{"sí" if conn.execute("SELECT COUNT(*) FROM customers WHERE es_publico=1 AND activo=1").fetchone()[0] else "no"}'
            )
            check('Tablas de clientes', not customer_tables,
                  customer_detail,
                  critico=False)
            productos = conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]
            activos = conn.execute('SELECT COUNT(*) FROM products WHERE active=1').fetchone()[0]
            check('Inventario cargado', productos > 0, f'{productos:,} productos ({activos:,} activos)')
            usuarios = conn.execute('SELECT COUNT(*) FROM users WHERE active=1').fetchone()[0]
            check('Usuarios configurados', usuarios > 0,
                  f'{usuarios} usuario(s) activo(s)', critico=False)
            ventas = conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0]
            check('Historial de ventas accesible', True, f'{ventas:,} ventas', critico=False)
            conn.close()
        except sqlite3.Error as exc:
            check('Base de datos íntegra (SQLite)', False, str(exc))
    else:
        check('Base de datos presente', False, 'no existe pos.db')

    # 4. Permisos de escritura
    try:
        prueba = os.path.join(BASE_DIR, '_prueba_escritura.tmp')
        with open(prueba, 'w', encoding='utf-8') as fh:
            fh.write('ok')
        os.remove(prueba)
        check('Permiso de escritura en la carpeta', True, BASE_DIR)
    except OSError as exc:
        check('Permiso de escritura en la carpeta', False, exc)

    # 5. Espacio en disco
    try:
        libre = shutil.disk_usage(BASE_DIR).free / (1024 ** 3)
        check('Espacio en disco', libre > 1, f'{libre:.1f} GB libres')
    except OSError as exc:
        check('Espacio en disco', False, exc, critico=False)

    # 6. Puerto y servidor
    servidor = False
    for offset in range(10):
        url = f'http://127.0.0.1:{8090 + offset}'
        try:
            with urllib.request.urlopen(f'{url}/api/pos/health', timeout=1) as resp:
                if resp.status == 200:
                    check('Servidor de caja activo', True, url)
                    servidor = True
                    break
        except Exception:
            continue
    if not servidor:
        check('Servidor de caja activo', False,
              'no está corriendo (es normal si aún no abriste caja.bat)', critico=False)

    # 7. Impresora (informativo)
    try:
        if os.name == 'nt':
            import pos_print
            impresoras = pos_print.list_printers()
            check('Impresoras detectadas', bool(impresoras),
                  ', '.join(impresoras) if impresoras else 'ninguna (la impresión aún está pendiente)',
                  critico=False)
    except Exception as exc:
        check('Impresoras detectadas', False, exc, critico=False)

    # 8. Estado del congelamiento (informativo)
    marcador = os.path.join(BASE_DIR, 'Historico', 'Auxiliares', 'INVENTARIO_CONGELADO.txt')
    check('Inventario congelado', os.path.exists(marcador),
          'congelado' if os.path.exists(marcador) else 'NO congelado (aún se puede sincronizar 1box)',
          critico=False)

    # 9. Histórico (informativo)
    hist = os.path.join(BASE_DIR, 'Historico', 'historico_1box.db')
    if not os.path.exists(hist):
        hist = os.path.join(BASE_DIR, 'historico_1box.db')
    if os.path.exists(hist):
        tam = os.path.getsize(hist) / (1024 ** 2)
        check('Histórico de 1box disponible', True, f'{tam:.0f} MB (solo consulta)', critico=False)
    else:
        check('Histórico de 1box disponible', False, 'no está (opcional)', critico=False)

    # Resumen
    fallos = [r for r in results if not r['ok'] and r['critico']]
    avisos = [r for r in results if not r['ok'] and not r['critico']]
    print('-' * 68)
    print(f'  Resultado: {len(results) - len(fallos) - len(avisos)} correctos, '
          f'{len(fallos)} fallos, {len(avisos)} avisos')
    if fallos:
        print('  Corrige los fallos antes de empezar a cobrar.')
    elif avisos:
        print('  Listo para cobrar. (Revisa los avisos, no son bloqueantes.)')
    else:
        print('  Todo en orden. La caja está lista para operar.')

    try:
        os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
        with open(REPORT_PATH, 'w', encoding='utf-8') as fh:
            json.dump({'fecha': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                       'carpeta': BASE_DIR, 'python': sys.version,
                       'resultados': results}, fh, ensure_ascii=False, indent=2)
        print(f'  Reporte: {os.path.relpath(REPORT_PATH, BASE_DIR)}')
    except OSError:
        pass
    print('=' * 68)
    return 1 if fallos else 0


if __name__ == '__main__':
    sys.exit(main())
