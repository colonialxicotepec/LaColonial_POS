r"""
Empaqueta el proyecto para llevarlo a otra computadora.

Genera un ZIP autocontenido con:
  - el runtime portable de Python (runtime\)
  - el instalador del runtime (vendor\)
  - el código, la interfaz y la documentación
  - la base del POS (pos.db) en copia consistente
  - el acta y el snapshot del inventario congelado
  - el histórico de 1box (opcional, con --con-historico)
  - MANIFIESTO.txt con el SHA-256 de cada archivo

Uso:
    python empaquetar_proyecto.py
    python empaquetar_proyecto.py --con-historico
"""

import argparse
import datetime
import hashlib
import os
import shutil
import sqlite3
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, 'dist')

# archivos y carpetas que se empaquetan
INCLUDE_FILES = [
    'app_pos.py', 'catalogo.py', 'pos_api.py', 'pos_core.py', 'pos_print.py',
    'impresora_local.py',
    'importar_clientes.py',
    'verificar_instalacion.py', 'empaquetar_proyecto.py', 'verificar_paquete.py',
    'reiniciar_caja.py', 'restaurar_respaldo.py',
    'pos_config.json',
    'caja.bat', 'buscar_productos.bat', 'impresora.bat', 'instalar_cajero.bat',
    'caja_en_red.bat', 'abrir_puerto_firewall.bat',
    'instalar_python_portable.bat', 'verificar_instalacion.bat',
    'empaquetar_proyecto.bat',
    'README.md', 'README_POS.md',
    'INSTALACION.md', 'MANUAL_RAPIDO.md', 'ARQUITECTURA.md',
]
INCLUDE_DIRS = ['ui', 'runtime', 'vendor', 'Historico']
SKIP_NAMES = {'__pycache__', 'scratch', 'test', 'dist', 'historico', 'tickets',
              'backups', 'exportes'}


def sha256_file(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def copy_db_consistent(source, dest):
    """Copia consistente de SQLite (sin depender del WAL)."""
    src = sqlite3.connect(source)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


def collect(con_historico=False):
    """Devuelve la lista de archivos a empaquetar (rutas relativas)."""
    items = []
    for name in INCLUDE_FILES:
        path = os.path.join(BASE_DIR, name)
        if os.path.exists(path):
            items.append(name)
    for folder in INCLUDE_DIRS:
        root = os.path.join(BASE_DIR, folder)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES]
            for filename in filenames:
                if filename.endswith(('.pyc', '.log')):
                    continue
                if filename == 'historico_1box.db' and not con_historico:
                    continue
                full = os.path.join(dirpath, filename)
                items.append(os.path.relpath(full, BASE_DIR).replace('\\', '/'))
    if con_historico:
        hist = os.path.join(BASE_DIR, 'historico_1box.db')
        if os.path.exists(hist) and 'historico_1box.db' not in items:
            items.append('historico_1box.db')
    return items


def main():
    parser = argparse.ArgumentParser(description='Empaqueta el proyecto para otra computadora.')
    parser.add_argument('--con-historico', action='store_true',
                        help='Incluir historico_1box.db (base de solo consulta, ~130 MB)')
    parser.add_argument('--salida', default=None, help='Ruta del ZIP a generar')
    args = parser.parse_args()

    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs(DIST_DIR, exist_ok=True)
    zip_path = args.salida or os.path.join(DIST_DIR, f'LaColonial_POS_{stamp}.zip')

    print('=' * 68)
    print('  EMPAQUETADO DEL PROYECTO PARA OTRA COMPUTADORA')
    print('=' * 68)

    items = collect(args.con_historico)

    # base consistente
    db_dest = os.path.join(DIST_DIR, '_pos_copia.db')
    if os.path.exists(os.path.join(BASE_DIR, 'pos.db')):
        copy_db_consistent(os.path.join(BASE_DIR, 'pos.db'), db_dest)
        items.append('_pos_copia.db')

    # manifiesto
    manifest_lines = ['MANIFIESTO DEL PAQUETE POS LA COLONIAL',
                      f'Generado: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
                      '', 'SHA-256 de cada archivo:']
    for item in items:
        full = os.path.join(BASE_DIR, item) if not item.startswith('_pos_copia') else db_dest
        if os.path.isfile(full):
            manifest_lines.append(f'{sha256_file(full)}  {item}')
    manifest_path = os.path.join(DIST_DIR, '_MANIFIESTO.txt')
    with open(manifest_path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(manifest_lines) + '\n')

    leeme = """POS LA COLONIAL - PAQUETE DE INSTALACION
=========================================
Este ZIP contiene todo lo necesario para operar la caja en una
computadora Windows 11 limpia, SIN internet y SIN instalar nada
en el sistema (trae Python portable dentro de la carpeta).

PASOS
-----
1. Descomprime este ZIP en una carpeta, por ejemplo:
      C:\\LaColonial
2. Doble clic en  instalar_python_portable.bat   (crea la carpeta runtime\\)
3. Doble clic en  verificar_instalacion.bat      (comprueba todo)
4. Doble clic en  caja.bat                       (abre la caja)

La base de datos con el inventario ya migrado va incluida (pos.db).
El acta del corte de inventario va en  Historico\\Auxiliares\\migracion\\.

Si Windows pregunta por la red: la caja sólo escucha en 127.0.0.1
(no se abre a la red local). Puedes_denegar el permiso de firewall.
"""
    leeme_path = os.path.join(DIST_DIR, '_LEEME.txt')
    with open(leeme_path, 'w', encoding='utf-8') as fh:
        fh.write(leeme)

    print(f'  Archivo destino : {zip_path}')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in items:
            full = os.path.join(BASE_DIR, item) if not item.startswith('_pos_copia') else db_dest
            if os.path.isfile(full):
                arcname = 'pos.db' if item == '_pos_copia.db' else item
                zf.write(full, arcname)
                print(f'    + {arcname}')
        zf.write(manifest_path, 'MANIFIESTO.txt')
        zf.write(leeme_path, 'LEEME-INSTALACION.txt')
        print('    + MANIFIESTO.txt')
        print('    + LEEME-INSTALACION.txt')

    os.remove(db_dest)
    os.remove(manifest_path)
    os.remove(leeme_path)

    size = os.path.getsize(zip_path) / (1024 ** 2)
    digest = sha256_file(zip_path)
    print('-' * 68)
    print(f'  Tamaño   : {size:.1f} MB')
    print(f'  SHA-256  : {digest}')
    print(f'  Archivo  : {os.path.basename(zip_path)}')
    print('=' * 68)
    if not args.con_historico:
        print('  (Para incluir el histórico de 1box en solo consulta, usa --con-historico.)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
