"""
Copia el catálogo limpio (Historico/Auxiliares/inventario_moderno.db) al
inventario propio del POS.

Uso:
    python importar_inventario.py                 # actualiza productos, conserva el stock local
    python importar_inventario.py --reiniciar-stock   # reinicia el stock con el del catálogo
    python importar_inventario.py --reemplazar        # desactiva lo que ya no exista en el origen

El importador deja registro de cada movimiento (importación) con fecha y hora.
"""

import argparse
import os
import sqlite3
import sys

CUR_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(CUR_DIR)) if os.path.basename(CUR_DIR).lower() == 'auxiliares' else CUR_DIR
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import pos_core

DEFAULT_SOURCE = os.path.join(pos_core.BASE_DIR, 'Historico', 'Auxiliares', 'inventario_moderno.db')
if not os.path.exists(DEFAULT_SOURCE):
    DEFAULT_SOURCE = os.path.join(pos_core.BASE_DIR, 'inventario_moderno.db')
COLUMNS = ['sku', 'barcode', 'item_number', 'alt_codes', 'name', 'category', 'location',
           'sale_price', 'cost_price', 'stock', 'units_sold_period', 'last_sold_at', 'legacy_item_id']


def read_source(path):
    if not os.path.exists(path):
        raise SystemExit(f'ERROR: no existe el catálogo de origen: {path}')
    conn = sqlite3.connect(path, timeout=15)
    try:
        rows = conn.execute(
            f"""SELECT {', '.join(COLUMNS)} FROM products"""
        ).fetchall()
    finally:
        conn.close()
    return [dict(zip(COLUMNS, row)) for row in rows]


def main():
    parser = argparse.ArgumentParser(description='Importa el inventario limpio al POS.')
    parser.add_argument('--origen', default=DEFAULT_SOURCE, help='Catálogo limpio a importar')
    parser.add_argument('--reiniciar-stock', action='store_true',
                        help='Pone el stock del catálogo (el stock local se reemplaza)')
    parser.add_argument('--reemplazar', action='store_true',
                        help='Desactiva los productos que ya no están en el origen')
    args = parser.parse_args()

    print('=' * 62)
    print('  IMPORTACIÓN DE INVENTARIO AL POS')
    print('=' * 62)
    print(f'  Origen           : {args.origen}')
    pos_core.init_db()
    rows = read_source(args.origen)
    print(f'  Productos leídos : {len(rows):,}')
    print(f'  Reiniciar stock  : {"sí" if args.reiniciar_stock else "no (se conserva el local)"}')
    print(f'  Reemplazar       : {"sí" if args.reemplazar else "no"}')

    result = pos_core.import_products(rows, None,
                                      replace=args.reemplazar,
                                      reset_stock=args.reiniciar_stock)
    counts = pos_core.products_count()
    print('-' * 62)
    print(f'  Nuevos           : {result["created"]:,}')
    print(f'  Actualizados     : {result["updated"]:,}')
    print(f'  Omitidos         : {result["skipped"]:,}')
    print(f'  En el POS ahora  : {counts["total"]:,} ({counts["active"]:,} activos)')
    print('  Listo. El POS ya usa su propio inventario.')
    print('=' * 62)
    return 0


if __name__ == '__main__':
    sys.exit(main())
