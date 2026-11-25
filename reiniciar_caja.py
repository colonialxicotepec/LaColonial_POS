"""
Deja la caja como de fábrica: inventario completo, sin usuarios, sin ventas
y sin turnos (para empezar limpio en la computadora nueva).

El inventario NO se toca: se reimporta desde el catálogo congelado
(`inventario_moderno.db`), de modo que las existencias quedan exactamente
como en el acta de corte.

Uso:
    python reiniciar_caja.py            # pide confirmación
    python reiniciar_caja.py --si       # sin preguntar
"""

import argparse
import datetime
import os
import shutil
import sqlite3
import sys

import pos_core

BASE_DIR = pos_core.BASE_DIR
CATALOG_DB = os.path.join(BASE_DIR, 'Historico', 'Auxiliares', 'inventario_moderno.db')
if not os.path.exists(CATALOG_DB):
    CATALOG_DB = os.path.join(BASE_DIR, 'inventario_moderno.db')


def main():
    parser = argparse.ArgumentParser(description='Reinicia la caja conservando el inventario.')
    parser.add_argument('--si', action='store_true', help='No pide confirmación')
    args = parser.parse_args()

    print('=' * 66)
    print('  REINICIO DE CAJA (copia de fábrica)')
    print('=' * 66)

    if not os.path.exists(CATALOG_DB):
        print(f'ERROR: no existe el catálogo congelado {CATALOG_DB}.')
        return 1

    conn = pos_core.connect()
    try:
        antes = {
            'productos': conn.execute('SELECT COUNT(*) FROM products').fetchone()[0],
            'usuarios': conn.execute('SELECT COUNT(*) FROM users').fetchone()[0],
            'ventas': conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0],
            'turnos': conn.execute('SELECT COUNT(*) FROM shifts').fetchone()[0],
        }
    finally:
        conn.close()
    print(f'  Estado actual : {antes["productos"]:,} productos · {antes["usuarios"]} usuarios · '
          f'{antes["ventas"]} ventas · {antes["turnos"]} turnos')

    if antes['ventas'] or antes['usuarios']:
        print('  AVISO: se borrarán los usuarios, ventas, turnos y movimientos de prueba.')
    if not args.si:
        respuesta = input('  ¿Continuar? (si/no): ').strip().lower()
        if respuesta not in ('si', 'sí', 's', 'yes'):
            print('  Cancelado. No se cambió nada.')
            return 0

    # Respaldo de la caja actual
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs(pos_core.BACKUP_DIR, exist_ok=True)
    respaldo = os.path.join(pos_core.BACKUP_DIR, f'pos_{stamp}_antes_del_reinicio.db')
    origen = sqlite3.connect(pos_core.POS_DB)
    destino = sqlite3.connect(respaldo)
    try:
        origen.backup(destino)
    finally:
        destino.close()
        origen.close()
    print(f'  Respaldo      : {os.path.basename(respaldo)}')

    # Base nueva
    for path in (pos_core.POS_DB, pos_core.POS_DB + '-wal', pos_core.POS_DB + '-shm'):
        if os.path.exists(path):
            os.remove(path)
    pos_core.init_db()

    # Importación del inventario congelado
    cat = sqlite3.connect(CATALOG_DB)
    cat.row_factory = sqlite3.Row
    try:
        filas = [dict(r) for r in cat.execute(
            """SELECT sku, barcode, item_number, alt_codes, name, category, location,
                      sale_price, cost_price, stock, units_sold_period, last_sold_at, legacy_item_id
               FROM products""")]
    finally:
        cat.close()
    resultado = pos_core.import_products(filas, None, replace=False, reset_stock=True)
    print(f'  Importación   : {resultado["created"]:,} productos, '
          f'{resultado["updated"]:,} actualizados')

    # Verificación
    conn = pos_core.connect()
    try:
        despues = {
            'productos': conn.execute('SELECT COUNT(*) FROM products').fetchone()[0],
            'activos': conn.execute('SELECT COUNT(*) FROM products WHERE active=1').fetchone()[0],
            'usuarios': conn.execute('SELECT COUNT(*) FROM users').fetchone()[0],
            'ventas': conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0],
            'turnos': conn.execute('SELECT COUNT(*) FROM shifts').fetchone()[0],
            'movimientos': conn.execute('SELECT COUNT(*) FROM stock_movements').fetchone()[0],
        }
        unidades = conn.execute('SELECT ROUND(SUM(stock),3) FROM products').fetchone()[0]
    finally:
        conn.close()

    cat2 = sqlite3.connect(CATALOG_DB)
    try:
        expected = cat2.execute('SELECT ROUND(SUM(stock),3) FROM products').fetchone()[0]
    finally:
        cat2.close()

    print('-' * 66)
    print(f'  Productos     : {despues["productos"]:,} ({despues["activos"]:,} activos)')
    print(f'  Unidades      : {unidades:,.0f} (catálogo congelado: {expected:,.0f})')
    print(f'  Usuarios      : {despues["usuarios"]}')
    print(f'  Ventas        : {despues["ventas"]}')
    print(f'  Turnos        : {despues["turnos"]}')
    print(f'  Movimientos   : {despues["movimientos"]:,} (registro de la carga inicial)')
    if abs(float(unidades) - float(expected)) > 0.01:
        print('  AVISO: las unidades NO coinciden con el catálogo congelado.')
    print('=' * 66)
    print('  Caja lista: al abrirla pide crear el PIN del gerente y del cajero.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
