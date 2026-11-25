"""
Cierre del inventario: migra el catálogo final de `1box` al POS, concilia
ambas bases y sella el inventario como congelado.

Pasos:
  1. (opcional) ejecuta el ETL final para tomar el estado de cierre de 1box.
  2. Importa el catálogo al inventario propio del POS reiniciando el stock.
  3. Concilia: mismo catálogo, mismas existencias, mismos precios.
  4. Escribe el acta de corte (Markdown + CSV + JSON) y el sello
     INVENTARIO_CONGELADO.txt con las huellas SHA-256.

Uso:
    python cerrar_inventario.py               # ETL + importación + conciliación
    python cerrar_inventario.py --sin-etl     # usa el catálogo ya generado
    python cerrar_inventario.py --verificar   # sólo concilia (no modifica nada)
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

CUR_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(CUR_DIR)) if os.path.basename(CUR_DIR).lower() == 'auxiliares' else CUR_DIR
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import pos_core

BASE_DIR = pos_core.BASE_DIR
CATALOG_DB = os.path.join(BASE_DIR, 'Historico', 'Auxiliares', 'inventario_moderno.db')
MIGRACION_DIR = os.path.join(BASE_DIR, 'Historico', 'Auxiliares', 'migracion')
FREEZE_MARKER = os.path.join(BASE_DIR, 'Historico', 'Auxiliares', 'INVENTARIO_CONGELADO.txt')


def log(msg=''):
    print(msg, flush=True)


def sha256_file(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def read_catalog():
    if not os.path.exists(CATALOG_DB):
        raise SystemExit(f'ERROR: no existe el catálogo {CATALOG_DB}. '
                         'Ejecuta primero la sincronización (o usa este script sin --sin-etl).')
    conn = sqlite3.connect(CATALOG_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT sku, barcode, item_number, alt_codes, name, category, location,
                      sale_price, cost_price, stock, legacy_item_id
               FROM products ORDER BY sku"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def read_pos():
    conn = pos_core.connect()
    try:
        rows = conn.execute(
            """SELECT sku, barcode, name, category, location, sale_price, cost_price, stock,
                      legacy_item_id, active, source
               FROM products ORDER BY sku"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def quality(rows):
    codes = {}
    for row in rows:
        code = (row.get('barcode') or '').strip().upper()
        if code:
            codes.setdefault(code, []).append(row.get('sku'))
    duplicated = {code: skus for code, skus in codes.items() if len(skus) > 1}
    return {
        'productos': len(rows),
        'sin_codigo': sum(1 for r in rows if not (r.get('barcode') or '').strip()),
        'sin_ubicacion': sum(1 for r in rows if not (r.get('location') or '').strip()),
        'precio_cero': sum(1 for r in rows if float(r.get('sale_price') or 0) <= 0),
        'stock_cero': sum(1 for r in rows if float(r.get('stock') or 0) == 0),
        'unidades': round(sum(float(r.get('stock') or 0) for r in rows), 3),
        'valor_costo': round(sum(float(r.get('stock') or 0) * float(r.get('cost_price') or 0)
                                  for r in rows), 2),
        'valor_venta': round(sum(float(r.get('stock') or 0) * float(r.get('sale_price') or 0)
                                  for r in rows), 2),
        'codigos_duplicados': len(duplicated),
        'detalle_duplicados': duplicated,
    }


def reconcile(catalog, pos):
    cat = {r['sku']: r for r in catalog}
    ps = {r['sku']: r for r in pos}
    differences = []
    for sku, row in cat.items():
        other = ps.get(sku)
        if not other:
            differences.append({'sku': sku, 'campo': 'existe', 'origen': 'sí',
                                'pos': 'no', 'detalle': row['name'], 'critica': 'sí'})
            continue
        for campo, tol in (('stock', 0.001), ('sale_price', 0.011), ('cost_price', 0.011)):
            a = float(row.get(campo) or 0)
            b = float(other.get(campo) or 0)
            if abs(a - b) > tol:
                differences.append({'sku': sku, 'campo': campo, 'origen': round(a, 4),
                                    'pos': round(b, 4), 'detalle': row['name'],
                                    'critica': 'sí' if campo in ('stock', 'sale_price') else 'no'})
        if (row.get('name') or '') != (other.get('name') or ''):
            differences.append({'sku': sku, 'campo': 'nombre', 'origen': row.get('name'),
                                'pos': other.get('name'), 'detalle': '', 'critica': 'no'})
    extra = [sku for sku in ps if sku not in cat]
    return differences, extra


def outliers(rows, limite=100000):
    """Existencias absurdas heredadas del sistema viejo (requieren revisión)."""
    return [r for r in rows if abs(float(r.get('stock') or 0)) > limite]


def export_snapshot(rows, path):
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(['SKU', 'Nombre', 'Código', 'Categoría', 'Ubicación',
                         'Precio', 'Costo', 'Existencias', 'ValorCosto', 'ValorVenta', 'Activo'])
        for row in rows:
            stock = float(row.get('stock') or 0)
            writer.writerow([
                row['sku'], row.get('name') or '', row.get('barcode') or '',
                row.get('category') or '', row.get('location') or '',
                f"{float(row.get('sale_price') or 0):.2f}",
                f"{float(row.get('cost_price') or 0):.2f}",
                f"{stock:.3f}",
                f"{stock * float(row.get('cost_price') or 0):.2f}",
                f"{stock * float(row.get('sale_price') or 0):.2f}",
                'sí' if row.get('active', 1) else 'no',
            ])


def main():
    parser = argparse.ArgumentParser(description='Cierre y migración del inventario.')
    parser.add_argument('--sin-etl', action='store_true',
                        help='No ejecutar la sincronización de 1box')
    parser.add_argument('--verificar', action='store_true',
                        help='Sólo conciliar, sin modificar nada')
    args = parser.parse_args()

    os.makedirs(MIGRACION_DIR, exist_ok=True)
    stamp = datetime.datetime.now()
    dia = stamp.strftime('%Y%m%d')

    log('=' * 66)
    log('  CIERRE DEL INVENTARIO Y MIGRACIÓN AL POS')
    log('=' * 66)
    log(f'  Fecha: {stamp.strftime("%Y-%m-%d %H:%M:%S")}')

    # 1. ETL final
    if not args.sin_etl and not args.verificar:
        log('\n[1/5] Sincronización final de 1box...')
        etl_script = os.path.join(CUR_DIR, 'etl_pipeline.py')
        if not os.path.exists(etl_script):
            etl_script = os.path.join(BASE_DIR, 'etl_pipeline.py')
        result = subprocess.run([sys.executable, etl_script],
                                cwd=BASE_DIR)
        if result.returncode not in (0, None):
            raise SystemExit('ERROR: la sincronización falló; el cierre se detiene.')
    else:
        log('\n[1/5] Sincronización omitida.')

    # 2. Importación al inventario propio
    catalog = read_catalog()
    log(f'\n[2/5] Catálogo de origen: {len(catalog):,} productos')
    if not args.verificar:
        result = pos_core.import_products(catalog, None, replace=False, reset_stock=True)
        log(f'      Importados al POS: {result["created"]:,} nuevos, '
            f'{result["updated"]:,} actualizados')
    else:
        log('      Modo verificar: no se modifica nada.')

    # 3. Conciliación
    log('\n[3/5] Conciliación catálogo de origen vs inventario del POS...')
    pos_rows = read_pos()
    differences, extra = reconcile(catalog, pos_rows)
    q_cat = quality(catalog)
    q_pos = quality(pos_rows)

    diff_path = os.path.join(MIGRACION_DIR, f'diferencias_{dia}.csv')
    with open(diff_path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=['sku', 'campo', 'origen', 'pos', 'detalle', 'critica'])
        writer.writeheader()
        for row in differences:
            writer.writerow(row)

    anomalos = outliers(pos_rows)
    anomalos_cat = outliers(catalog)

    # 4. Snapshot congelado
    snapshot = os.path.join(MIGRACION_DIR, f'inventario_congelado_{dia}.csv')
    export_snapshot(pos_rows, snapshot)
    log(f'      Snapshot: {os.path.basename(snapshot)}')

    # 5. Acta
    resumen = {
        'fecha': stamp.strftime('%Y-%m-%d %H:%M:%S'),
        'origen': 'MySQL 1box (última sincronización)',
        'productos_origen': q_cat,
        'productos_pos': q_pos,
        'diferencias': len(differences),
        'diferencias_criticas': len([d for d in differences if d.get('critica') == 'sí']),
        'productos_solo_en_pos': len(extra),
        'solo_en_pos': extra[:50],
        'existencias_anormales': [
            {'sku': r['sku'], 'nombre': r.get('name'), 'stock': float(r.get('stock') or 0)}
            for r in anomalos
        ],
        'snapshot': os.path.basename(snapshot),
        'sha256_snapshot': sha256_file(snapshot),
        'sha256_catalogo_csv': (sha256_file(os.path.join(
            BASE_DIR, 'Historico', 'Auxiliares', 'inventario_limpio.csv'))
            if os.path.exists(os.path.join(
                BASE_DIR, 'Historico', 'Auxiliares', 'inventario_limpio.csv')) else ''),
    }
    json_path = os.path.join(MIGRACION_DIR, f'corte_{dia}.json')
    with open(json_path, 'w', encoding='utf-8') as fh:
        json.dump(resumen, fh, ensure_ascii=False, indent=2)

    criticas = [d for d in differences if d.get('critica') == 'sí']
    ok = not criticas

    md = f"""# Acta de cierre y migración del inventario

**Fecha:** {resumen['fecha']}
**Origen:** {resumen['origen']}
**Destino:** inventario propio del POS (`pos.db`, tabla `products`)

## Resultado

| Concepto | Catálogo de origen | Inventario del POS |
| :--- | ---: | ---: |
| Productos | {q_cat['productos']:,} | {q_pos['productos']:,} |
| Unidades en existencia | {q_cat['unidades']:,.0f} | {q_pos['unidades']:,.0f} |
| Valor a costo | ${q_cat['valor_costo']:,.2f} | ${q_pos['valor_costo']:,.2f} |
| Valor a precio de venta | ${q_cat['valor_venta']:,.2f} | ${q_pos['valor_venta']:,.2f} |
| Sin código de barras | {q_cat['sin_codigo']:,} | {q_pos['sin_codigo']:,} |
| Sin ubicación | {q_cat['sin_ubicacion']:,} | {q_pos['sin_ubicacion']:,} |
| Precio de venta ≤ 0 | {q_cat['precio_cero']:,} | {q_pos['precio_cero']:,} |
| Sin existencias | {q_cat['stock_cero']:,} | {q_pos['stock_cero']:,} |
| Códigos de barras repetidos | {q_cat['codigos_duplicados']:,} | {q_pos['codigos_duplicados']:,} |

## Conciliación

- Diferencias detectadas: **{len(differences):,}** (críticas: {len(criticas):,})
- Productos sólo en el POS (altas manuales): **{len(extra):,}**
- Veredicto: **{'OK — el inventario del POS coincide con el origen' if ok else 'REVISAR: hay diferencias críticas'}**

Detalle de diferencias: `diferencias_{dia}.csv` (vacío si no hay).
Snapshot del inventario congelado: `inventario_congelado_{dia}.csv`
SHA-256 del snapshot: `{resumen['sha256_snapshot']}`

## Existencia anómalas (requieren revisión del gerente)

Estos valores vienen así del sistema viejo (`1box`): no son un error de la
migración, pero **distorcionan el valor del inventario**. Se corrigen en el POS
con **Inventario → − Baja → cantidad correcta → motivo**.

| SKU | Producto | Existencia | ¿También está mal en 1box? |
| :--- | :--- | ---: | :--- |
{chr(10).join(f"| {r['sku']} | {(r.get('name') or '')[:40]} | {float(r.get('stock') or 0):,.0f} | {'sí' if any(a['sku'] == r['sku'] for a in anomalos_cat) else 'no'} |" for r in anomalos) or '| — | Sin anomalías | — | — |'}

## Cómo revertir

1. Cerrar la caja.
2. Copiar `pos.db` a `pos.db.antes_del_cierre`.
3. Restaurar el respaldo más reciente de `backups/`.
4. Quitar `INVENTARIO_CONGELADO.txt` para volver a permitir sincronizaciones.

---
*Generado automáticamente por `cerrar_inventario.py`.*
"""
    md_path = os.path.join(MIGRACION_DIR, f'corte_inventario_{dia}.md')
    with open(md_path, 'w', encoding='utf-8') as fh:
        fh.write(md)

    # Sello de congelamiento
    if ok and not args.verificar:
        with open(FREEZE_MARKER, 'w', encoding='utf-8') as fh:
            fh.write(f"""INVENTARIO CONGELADO
======================
Fecha de cierre          : {resumen['fecha']}
Origen                   : {resumen['origen']}
Productos en el POS      : {q_pos['productos']:,}
Unidades en existencia    : {q_pos['unidades']:,.0f}
Valor a costo            : ${q_pos['valor_costo']:,.2f}
Valor a precio de venta  : ${q_pos['valor_venta']:,.2f}
Snapshot                 : {os.path.basename(snapshot)}
SHA-256 del snapshot     : {resumen['sha256_snapshot']}
Acta                     : corte_inventario_{dia}.md

El inventario de la tienda vive en pos.db (tabla products).
La sincronizacion con 1box queda BLOQUEADA (ver etl_pipeline.py).
Para rehacerla a proposito:  python etl_pipeline.py --descongelar
""")
        log(f'      Sello: {os.path.basename(FREEZE_MARKER)}')

    log('\n[4/5] Resultado')
    log(f'      Productos: origen {q_cat["productos"]:,} | POS {q_pos["productos"]:,}')
    log(f'      Unidades: {q_pos["unidades"]:,.0f} | valor costo ${q_pos["valor_costo"]:,.2f} '
        f'| valor venta ${q_pos["valor_venta"]:,.2f}')
    log(f'      Diferencias: {len(differences):,} | sólo en POS: {len(extra):,}')
    log(f'      Acta: {os.path.basename(md_path)}')
    log('\n[5/5] ' + ('Inventario CONGELADO.' if ok and not args.verificar
                       else ('Verificación: ' + ('sin diferencias críticas.'
                                                if ok else 'hay diferencias críticas.'))))
    log('=' * 66)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
