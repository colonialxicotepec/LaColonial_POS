import os
import re
import sys
import datetime
import sqlite3
import pymysql
import csv
import json
import shutil
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Rutas (relativas al propio script: el proyecto se puede mover de equipo)
CUR_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(CUR_DIR).lower() == 'auxiliares':
    LEGACY_DIR = CUR_DIR
    WORK_DIR = os.path.dirname(os.path.dirname(CUR_DIR))
else:
    WORK_DIR = CUR_DIR
    LEGACY_DIR = os.path.join(WORK_DIR, 'Historico', 'Auxiliares')

CONFIG_FILE = os.path.join(LEGACY_DIR, 'config.json')
if not os.path.exists(CONFIG_FILE):
    CONFIG_FILE = os.path.join(WORK_DIR, 'config.json')

SKU_REGISTRY_FILE = os.path.join(LEGACY_DIR, 'sku_registry.json')
HISTORICO_DIR = os.path.join(LEGACY_DIR, 'historico')
FREEZE_MARKER = os.path.join(LEGACY_DIR, 'INVENTARIO_CONGELADO.txt')

# Modo prueba: escribe todo en test\ y NO toca sku_registry.json,
# ni los archivos de producción, ni el histórico.
TEST_MODE = '--test' in sys.argv
OUTPUT_DIR = os.path.join(WORK_DIR, 'test') if TEST_MODE else LEGACY_DIR

OUTPUT_DB = os.path.join(OUTPUT_DIR, 'inventario_test.db' if TEST_MODE else 'inventario_moderno.db')
OUTPUT_XLSX = os.path.join(OUTPUT_DIR, 'inventario_test.xlsx' if TEST_MODE else 'inventario_limpio.xlsx')
OUTPUT_CSV = os.path.join(OUTPUT_DIR, 'inventario_test.csv' if TEST_MODE else 'inventario_limpio.csv')
OUTPUT_REPORT = os.path.join(OUTPUT_DIR, 'reporte_test.md' if TEST_MODE else 'reporte_migracion.md')
OUTPUT_LOCATION_REPORT = os.path.join(OUTPUT_DIR, 'reporte_test_ubicaciones.md')

os.makedirs(OUTPUT_DIR, exist_ok=True)

# MySQL
MYSQL_HOST = '127.0.0.1'
MYSQL_USER = 'root'
MYSQL_PASS = ''
MYSQL_DB = '1box'

def load_config():
    default_config = {
        "barcode_prefix": "SBC",
        "inactivity_days": 365,
        "archive_old_exports": True,
        "category_normalization": {},
        "item_whitelist": [],
        "item_blacklist": [],
        "location_junk_values": [".", "-", "n/a", "s/u", "sin ubicacion", "sin ubicación"],
        "embed_alt_code_in_name": False,
        "name_max_length": 59
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
                loaded = json.load(f)
                default_config.update(loaded)
        except Exception as e:
            print(f"[AVISO] Error al leer config.json: {e}. Usando valores por defecto.")
    return default_config

def load_sku_registry():
    if os.path.exists(SKU_REGISTRY_FILE):
        try:
            with open(SKU_REGISTRY_FILE, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_sku_registry(registry):
    with open(SKU_REGISTRY_FILE, 'w', encoding='utf-8') as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)

def clean_text(text):
    if not text:
        return ''
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    text = text.replace('\ufffd', 'Ñ')
    return text

def clean_location(raw_location, junk_values):
    """
    Devuelve la ubicación tal cual, salvo los valores basura configurados
    (por ejemplo '.'), que se convierten en vacío. No recorta ni reformatea
    valores válidos: se respeta el texto idéntico de 1box.
    """
    if not raw_location:
        return ''
    junk = {str(v).strip().lower() for v in junk_values}
    if raw_location.strip().lower() in junk:
        return ''
    return raw_location

def unique_codes(values):
    """Devuelve identificadores únicos sin cambiar mayúsculas ni formato."""
    seen = set()
    result = []
    for value in values:
        code = clean_text(value)
        if not code:
            continue
        key = code.upper()
        if key in seen:
            continue
        seen.add(key)
        result.append(code)
    return result

def extra_codes_for_export(prod):
    """Códigos adicionales que no repiten el UPC/EAN/ISBN principal."""
    primary = clean_text(prod.get('raw_item_number')).upper()
    return ' | '.join(
        c for c in prod.get('alt_codes', [])
        if c.strip().upper() != primary
    )

def normalize_product_name(raw_name):
    """
    Normaliza y compacta nombres de producto reduciendo conjunciones y aplicando
    abreviaturas estándar, SIN eliminar marcas, códigos de modelo o especificaciones.
    """
    if not raw_name:
        return ''
    n = raw_name.strip()
    n = re.sub(r'\s+', ' ', n)

    # Caso especial conocido con notas de inventario internas
    if 'Kraft por Metro' in n:
        return '6. Papel Kraft Delgado 125cm x Metro 155g (80m)'

    # Proteger marcas compuestas con 'de la'
    n = re.sub(r'(?i)\bde\s+la\s+rosa\b', '__DELAROSA__', n)

    # Conectores con cantidades y empaque
    n = re.sub(r'(?i)\bcon\s+(\d+)\s*(piezas?|pzas?|pz)\b', r'c/\1 pzas', n)
    n = re.sub(r'(?i)\bcon\s+(\d+)\s*(colores)\b', r'c/\1 Colores', n)
    n = re.sub(r'(?i)\bcon\s+(\d+)\s*(hojas?|hjs)\b', r'c/\1 hjs', n)
    n = re.sub(r'(?i)\bde\s+(\d+)\s*(hojas?|hjs)\b', r'\1 hjs', n)
    n = re.sub(r'(?i)\b(caja|estuche|blíster|blister|paquete)\s+con\s+(\d+)\s*(piezas?|pzas?|pz)?\b', r'\1 c/\2', n)
    n = re.sub(r'(?i)\b(caja|estuche|blíster|blister|paquete)\s+con\s+(\d+)\s*(colores)\b', r'\1 c/\2 Colores', n)
    n = re.sub(r'(?i)\bcon\s+(\d+)\s+funciones\b', r'\1 Func', n)
    n = re.sub(r'(?i)\b(\d+)\s+l[ií]neas\s+en\s+pantalla\b', r'\1 Líneas', n)
    n = re.sub(r'(?i)\bcon\s+(\d+)\s+separadores\b', r'c/\1 Sep', n)
    n = re.sub(r'(?i)\bcon\s+costilla\s+grande\s+de\s+(\d+)\s*mm\b', r'Costilla \1mm', n)

    # Asegurar espacio si quedó 'c/8Colores' o similar
    n = re.sub(r'c/(\d+)([A-Za-zÁÉÍÓÚÑáéíóúñ])', r'c/\1 \2', n)

    # Compactar medidas 'X x Y mm' -> 'XxYmm'
    n = re.sub(r'(\d+)\s*[xX]\s*(\d+)\s*(mm|cm|m)', r'\1x\2\3', n)

    # Abreviaturas universales de papelería / retail
    abbrevs = [
        (r'(?i)\btamaño\s+carta\b', 'T/C'),
        (r'(?i)\btamaño\s+oficio\b', 'T/O'),
        (r'(?i)\btamaño\s+doble\s+carta\b', 'T/DC'),
        (r'(?i)\bt/carta\b', 'T/C'),
        (r'(?i)\bt/oficio\b', 'T/O'),
        (r'(?i)\bfluorescente\b', 'Fluor'),
        (r'(?i)\bsemiprofesional\b', 'Semiprof'),
        (r'(?i)\bprofesional\b', 'Prof'),
        (r'(?i)\bseparadores?\b', 'Sep'),
        (r'(?i)\bbl[ií]ster\b', 'Blist'),
        (r'(?i)\bestuche\b', 'Est'),
        (r'(?i)\bpaquete\b', 'Paq'),
        (r'(?i)\bcolores\s+surtidos\b', 'Surt'),
        (r'(?i)\bcolores\s+pastel\b', 'Pastel'),
        (r'(?i)\btransparente\b', 'Transp'),
        (r'(?i)\bcent[ií]metros?\b', 'cm'),
        (r'(?i)\bmil[ií]metros?\b', 'mm'),
        (r'(?i)\bmodelo\s+', ''),
        (r'(?i)\bn[uú]mero\s+(\d+)', r'#\1'),
        (r'(?i)\bpara\s+colorear\b', ''),
        (r'(?i)\bcon\s+graduaci[oó]n\b', 'Grad'),
        (r'(?i)\bcon\s+bisel\b', 'Bisel'),
        (r'(?i)\bcon\s+asa\b', 'Asa'),
        (r'(?i)\bl[aá]pices\s+de\s+colores\b', 'Colores'),
        (r'(?i)\betiqueta\s+adhesiva\b', 'Etiqueta'),
    ]
    for pattern, repl in abbrevs:
        n = re.sub(pattern, repl, n)

    # Reducción de preposiciones/conectores si el nombre supera los 48 caracteres
    if len(n) > 48:
        words = n.split(' ')
        filtered = []
        for w in words:
            low = w.lower()
            if low in ('de', 'con', 'en', 'para', 'por', 'del', 'el', 'la', 'los', 'las') and len(w) <= 4:
                continue
            filtered.append(w)
        n = ' '.join(filtered)

    # Restaurar marcas protegidas
    n = n.replace('__DELAROSA__', 'De La Rosa')
    n = re.sub(r'\s+', ' ', n).strip()
    return n

def write_location_test_report(start_time, source_locations, active_products, conflicts, stats, junk_values):
    """Reporte de aceptación del traslado de ubicaciones (modo --test)."""
    raw_values = {i: v for i, v in source_locations.items() if v}
    source_values = {i: clean_location(v, junk_values) for i, v in raw_values.items()}
    junk_in_source = {i: v for i, v in raw_values.items() if not source_values[i]}
    source_values = {i: v for i, v in source_values.items() if v}
    exported = [p for p in active_products if p['location']]

    compared = 0
    mismatches = []
    for p in exported:
        expected = clean_location(source_locations.get(p['location_origin_id'], ''), junk_values)
        compared += 1
        if p['location'] != expected:
            mismatches.append((p['item_id'], p['name'], p['location'], expected))

    origin_ids = {p['location_origin_id'] for p in exported}
    not_transferred = sorted(i for i in source_values if i not in origin_ids)
    distinct = {}
    for v in source_values.values():
        distinct[v] = distinct.get(v, 0) + 1
    distinct_sorted = sorted(distinct.items(), key=lambda kv: (-kv[1], kv[0]))

    lines = []
    lines.append("# Test de traslado de ubicaciones físicas (`1box` → catálogo moderno)\n")
    lines.append(f"Fecha del test: {start_time.strftime('%Y-%m-%d %H:%M:%S')}  ")
    lines.append("Modo: **--test** (no se modificaron archivos de producción ni `sku_registry.json`)\n")
    lines.append("## Resultado de aceptación\n")
    lines.append(f"- Valores comparados carácter por carácter: **{compared:,}**")
    lines.append(f"- Diferencias encontradas: **{len(mismatches):,}**")
    lines.append(f"- Veredicto: **{'APROBADO' if not mismatches else 'RECHAZADO'}**\n")
    lines.append("## Conteos\n")
    lines.append(f"- Valores no vacíos en `1box` (`phppos_location_items.location`): **{len(raw_values):,}**")
    lines.append(f"- Valores basura ignorados (`{', '.join(str(v) for v in junk_values)}`): **{len(junk_in_source):,}**")
    lines.append(f"- Valores distintos en `1box`: **{len(distinct):,}**")
    lines.append(f"- Productos activos con ubicación en el catálogo moderno: **{len(exported):,}**")
    lines.append(f"- Ubicaciones basura descartadas por producto activo: **{stats['ubicaciones_basura_descartadas']:,}**")
    lines.append(f"- Ubicaciones heredadas de duplicados fusionados: **{stats['ubicaciones_heredadas_fusion']:,}**")
    lines.append(f"- Ubicaciones no trasladadas (producto archivado/fusionado): **{len(not_transferred):,}**")
    lines.append(f"- Conflictos de fusión: **{len(conflicts):,}**\n")

    if mismatches:
        lines.append("## Diferencias detectadas\n")
        lines.append("| item_id | Producto | Valor en catálogo | Valor en 1box |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for mid, name, got, expected in mismatches[:50]:
            lines.append(f"| {mid} | {name} | `{got}` | `{expected}` |")
        lines.append("")

    if conflicts:
        lines.append("## Conflictos de ubicación por fusión de duplicados\n")
        lines.append("| Maestro | Producto | Ubicación maestro | Duplicados (item=ubicación) | Resolución |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for c in conflicts:
            lines.append(
                f"| {c['master_id']} | {c['master_name']} | `{c['master_location']}` | "
                f"{c['dupes']} | {c['resolution']} |"
            )
        lines.append("")

    lines.append("## Muestra de productos con ubicación\n")
    lines.append("| item_id | SKU | Producto | Ubicación |")
    lines.append("| :--- | :--- | :--- | :--- |")
    for p in exported[:20]:
        lines.append(f"| {p['item_id']} | {p['sku']} | {p['name'][:45]} | `{p['location']}` |")
    lines.append("")

    if not_transferred:
        lines.append("## Valores que no llegaron al catálogo (primeros 25)\n")
        lines.append("Corresponden a productos archivados o fusionados en un maestro con otra ubicación.\n")
        lines.append("| item_id | Ubicación en 1box |")
        lines.append("| :--- | :--- |")
        for i in not_transferred[:25]:
            lines.append(f"| {i} | `{source_values[i]}` |")
        lines.append("")

    lines.append("## Valores distintos encontrados en `1box`\n")
    lines.append("| Ubicación | Productos |")
    lines.append("| :--- | :--- |")
    for v, n in distinct_sorted:
        lines.append(f"| `{v}` | {n} |")
    lines.append("")

    with open(OUTPUT_LOCATION_REPORT, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))

    print("\n[TEST] Reporte de ubicaciones: " + OUTPUT_LOCATION_REPORT)
    print(f"[TEST] Comparados: {compared:,} | Diferencias: {len(mismatches):,} | "
          f"{'APROBADO' if not mismatches else 'RECHAZADO'}")
    return len(mismatches)


def check_congelado():
    """
    Tras el corte, el inventario ya es del POS. Sincronizar 1box de nuevo
    pisaría el inventario migrado, así que se bloquea salvo autorización
    expresa (--descongelar).
    """
    if not os.path.exists(FREEZE_MARKER):
        return True
    if '--descongelar' in sys.argv:
        print("[AVISO] Bloqueo de congelamiento omitido por --descongelar.")
        return True
    print("=" * 65)
    print("SINCRONIZACIÓN BLOQUEADA: EL INVENTARIO YA ESTÁ CONGELADO")
    print("=" * 65)
    try:
        with open(FREEZE_MARKER, encoding='utf-8') as fh:
            print(fh.read().strip())
    except OSError:
        pass
    print("-" * 65)
    print("El inventario que maneja la caja vive en pos.db (tabla products).")
    print("Sincronizar 1box ahora sobrescribiría existencias ya corregidas.")
    print("Si de verdad quieres rehacerlo, usa:")
    print("    python etl_pipeline.py --descongelar")
    print("=" * 65)
    return False


def run_etl():
    if not check_congelado():
        sys.exit(2)
    start_time = datetime.datetime.now()
    config = load_config()
    sku_registry = load_sku_registry()
    
    inactivity_days = config.get('inactivity_days', 365)
    cutoff_date = start_time - datetime.timedelta(days=inactivity_days)
    barcode_prefix = config.get('barcode_prefix', 'SBC')
    cat_norm_rules = config.get('category_normalization', {})
    whitelist = set(config.get('item_whitelist', []))
    blacklist = set(config.get('item_blacklist', []))
    junk_locations = config.get('location_junk_values', [])
    embed_alt_code = bool(config.get('embed_alt_code_in_name', False))
    name_max_length = int(config.get('name_max_length', 59))

    print("=" * 65)
    print("PROCESO RECURRENTE DE LIMPIEZA Y SANEAMIENTO DE INVENTARIO")
    print(f"Fecha de ejecución: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Corte de inactividad: Ventas desde {cutoff_date.strftime('%Y-%m-%d')} ({inactivity_days} días)")
    print("=" * 65)

    conn_mysql = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=MYSQL_DB,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    cur = conn_mysql.cursor()

    # 1. Ventas recientes
    print("[1/6] Consultando historial de ventas reciente...")
    cur.execute("""
        SELECT 
            si.item_id,
            MAX(s.sale_time) as last_sale_date,
            SUM(CASE WHEN s.sale_time >= %s THEN si.quantity_purchased ELSE 0 END) as units_sold_period,
            SUM(CASE WHEN s.sale_time >= %s THEN (si.item_unit_price * si.quantity_purchased) ELSE 0 END) as revenue_period,
            COUNT(DISTINCT s.sale_id) as total_sales_count
        FROM phppos_sales s
        JOIN phppos_sales_items si ON s.sale_id = si.sale_id
        GROUP BY si.item_id
    """, (cutoff_date, cutoff_date))
    
    sales_map = {}
    for row in cur.fetchall():
        sales_map[row['item_id']] = {
            'last_sale_date': row['last_sale_date'],
            'units_sold_period': float(row['units_sold_period'] or 0),
            'revenue_period': float(row['revenue_period'] or 0),
            'total_sales_count': row['total_sales_count']
        }

    # 2. Categorías
    print("[2/6] Cargando y normalizando taxonomía de categorías...")
    cur.execute("SELECT id, name FROM phppos_categories")
    category_name_map = {}
    for c in cur.fetchall():
        raw_name = clean_text(c['name'])
        norm_name = cat_norm_rules.get(raw_name, raw_name)
        category_name_map[c['id']] = norm_name

    # 3. Stock y ubicación física actual en tienda
    print("[3/6] Obteniendo existencias y ubicaciones físicas en tienda...")
    cur.execute("SELECT item_id, quantity, location FROM phppos_location_items WHERE location_id = 1")
    stock_map = {}
    source_locations = {}
    for row in cur.fetchall():
        stock_map[row['item_id']] = float(row['quantity'] or 0)
        # Se conserva el texto EXACTO del origen (sin trim, mayúsculas ni validación).
        source_locations[row['item_id']] = row['location'] if row['location'] is not None else ''

    # 4. Catálogo maestro de productos
    print("[4/6] Extrayendo productos desde base de datos operativa...")
    cur.execute("""
        SELECT 
            item_id,
            name,
            item_number,
            product_id,
            category_id,
            cost_price,
            unit_price,
            deleted,
            last_modified
        FROM phppos_items
        ORDER BY item_id ASC
    """)
    all_items = cur.fetchall()

    # 4b. Identificadores adicionales capturados en 1box
    #     (tabla "Números de artículo adicionales" del sistema legacy).
    cur.execute("""
        SELECT item_id, item_number
        FROM phppos_additional_item_numbers
        ORDER BY item_id ASC, item_number ASC
    """)
    additional_codes = {}
    for row in cur.fetchall():
        additional_codes.setdefault(row['item_id'], []).append(row['item_number'])

    conn_mysql.close()

    print(f"Total registros analizados: {len(all_items):,}")

    # 5. Deduplicación, normalización de nombres y reglas
    print("[5/6] Aplicando normalización masiva de nombres y deduplicación...")
    items_by_name = {}
    for item in all_items:
        item_id = item['item_id']
        raw_name = clean_text(item['name'])
        normalized_name = normalize_product_name(raw_name)

        raw_item_num = clean_text(item.get('item_number'))
        raw_prod_id = clean_text(item.get('product_id'))

        # Criterio de Código de Barras y SKU
        # 1. Código de barras: item_number si tiene >= 8 dígitos numéricos
        # 2. Si no, product_id si tiene >= 8 dígitos numéricos
        # 3. Si ninguno cumple, barcode = None
        is_item_num_barcode = bool(re.match(r'^\d{8,}$', raw_item_num))
        is_prod_id_barcode = bool(re.match(r'^\d{8,}$', raw_prod_id))

        if is_item_num_barcode:
            barcode_clean = raw_item_num
        elif is_prod_id_barcode:
            barcode_clean = raw_prod_id
        else:
            barcode_clean = None

        # Prioridad de SKU (Opción A):
        # Si no hay código de barras detectado o para texto interno:
        # Prioridad product_id si existe texto, luego item_number
        internal_sku = None
        if raw_prod_id and raw_prod_id != barcode_clean:
            internal_sku = raw_prod_id
        elif raw_item_num and raw_item_num != barcode_clean:
            internal_sku = raw_item_num
        elif raw_prod_id:
            internal_sku = raw_prod_id
        elif raw_item_num:
            internal_sku = raw_item_num

        cat_name = category_name_map.get(item['category_id'], 'General')
        cost = float(item['cost_price'] or 0)
        price = float(item['unit_price'] or 0)
        deleted = item['deleted']
        modified = item['last_modified']
        raw_stock = stock_map.get(item_id, 0.0)
        raw_location = source_locations.get(item_id, '')
        location = clean_location(raw_location, junk_locations)
        location_was_junk = bool(raw_location) and not location

        sale_info = sales_map.get(item_id, {
            'last_sale_date': None,
            'units_sold_period': 0.0,
            'revenue_period': 0.0,
            'total_sales_count': 0
        })

        norm_key = re.sub(r'[^a-zA-Z0-9áéíóúñÁÉÍÓÚÑ]', '', normalized_name.lower())
        if not norm_key:
            norm_key = f"item_{item_id}"

        item_data = {
            'item_id': item_id,
            'name': normalized_name,
            'original_name': raw_name,
            'barcode': barcode_clean,
            'internal_sku': internal_sku,
            'raw_item_number': raw_item_num,
            'raw_product_id': raw_prod_id,
            'alt_candidates': [raw_item_num] + additional_codes.get(item_id, []),
            'category': cat_name,
            'cost_price': cost,
            'unit_price': price,
            'deleted': deleted,
            'last_modified': modified,
            'stock': raw_stock,
            'location': location,
            'location_origin_id': item_id,
            'location_was_junk': location_was_junk,
            'last_sale_date': sale_info['last_sale_date'],
            'units_sold_period': sale_info['units_sold_period'],
            'revenue_period': sale_info['revenue_period'],
            'total_sales_count': sale_info['total_sales_count']
        }

        if norm_key not in items_by_name:
            items_by_name[norm_key] = []
        items_by_name[norm_key].append(item_data)

    unified_products = []
    archived_products = []
    stats = {
        'total_evaluados': len(all_items),
        'activos_saneados': 0,
        'descartados_inactivos': 0,
        'duplicados_fusionados': 0,
        'skus_reutilizados': 0,
        'skus_nuevos_generados': 0,
        'stock_ajustado_cero': 0,
        'nombres_optimizados': 0,
        'ubicaciones_exportadas': 0,
        'ubicaciones_heredadas_fusion': 0,
        'ubicaciones_conflicto': 0,
        'ubicaciones_basura_descartadas': 0,
        'productos_con_codigos_alt': 0,
        'codigos_alternativos_total': 0,
    }
    location_conflicts = []

    max_sbc_num = 0
    for assigned_sku in sku_registry.values():
        match = re.match(r'^' + re.escape(barcode_prefix) + r'-(\d+)$', assigned_sku)
        if match:
            max_sbc_num = max(max_sbc_num, int(match.group(1)))

    for norm_key, group in items_by_name.items():
        if len(group) == 1:
            unified_products.append(group[0])
        else:
            def rank_item(it):
                score = 0
                if it['deleted'] == 0:
                    score += 100
                if it['barcode'] and len(it['barcode']) >= 4:
                    score += 50
                if it['units_sold_period'] > 0:
                    score += 30
                if it['stock'] > 0:
                    score += 10
                return score

            sorted_group = sorted(group, key=rank_item, reverse=True)
            master = sorted_group[0]
            
            total_stock = master['stock']
            total_sold = master['units_sold_period']
            total_rev = master['revenue_period']
            latest_sale = master['last_sale_date']

            for dupe in sorted_group[1:]:
                stats['duplicados_fusionados'] += 1
                if dupe['stock'] > 0:
                    total_stock += dupe['stock']
                total_sold += dupe['units_sold_period']
                total_rev += dupe['revenue_period']
                if dupe['last_sale_date']:
                    if not latest_sale or dupe['last_sale_date'] > latest_sale:
                        latest_sale = dupe['last_sale_date']
                
                archived_products.append({
                    'item_id': dupe['item_id'],
                    'name': dupe['name'],
                    'barcode': dupe['barcode'],
                    'reason': f"Duplicado fusionado en producto maestro ID {master['item_id']} ({master['name']})",
                    'last_sale_date': dupe['last_sale_date'],
                    'stock_original': dupe['stock']
                })

            # Identificadores: se conservan los del maestro y los de sus duplicados.
            merged_candidates = list(master.get('alt_candidates', []))
            for dupe in sorted_group[1:]:
                merged_candidates.extend(dupe.get('alt_candidates', []))
            master['alt_candidates'] = merged_candidates

            # Ubicación física: se copia tal cual desde 1box; en fusión manda el maestro.
            master_loc = master['location']
            loc_dupes = [d for d in sorted_group[1:] if d['location']]
            if not master_loc and loc_dupes:
                distinct_locs = {d['location'] for d in loc_dupes}
                if len(distinct_locs) == 1:
                    master['location'] = loc_dupes[0]['location']
                    master['location_origin_id'] = loc_dupes[0]['item_id']
                    stats['ubicaciones_heredadas_fusion'] += 1
                else:
                    stats['ubicaciones_conflicto'] += 1
                    location_conflicts.append({
                        'master_id': master['item_id'],
                        'master_name': master['name'],
                        'master_location': '',
                        'dupes': ', '.join(f"{d['item_id']}={d['location']}" for d in loc_dupes),
                        'resolution': 'Sin ubicación: los duplicados tienen valores distintos',
                    })
            elif master_loc and loc_dupes:
                different = {d['location'] for d in loc_dupes} - {master_loc}
                if different:
                    stats['ubicaciones_conflicto'] += 1
                    location_conflicts.append({
                        'master_id': master['item_id'],
                        'master_name': master['name'],
                        'master_location': master_loc,
                        'dupes': ', '.join(f"{d['item_id']}={d['location']}" for d in loc_dupes),
                        'resolution': 'Se conservó la ubicación del maestro',
                    })

            master['stock'] = total_stock
            master['units_sold_period'] = total_sold
            master['revenue_period'] = total_rev
            master['last_sale_date'] = latest_sale
            unified_products.append(master)

    active_products = []
    seen_skus = set()

    for prod in unified_products:
        item_id = prod['item_id']
        stock = prod['stock']
        last_sale = prod['last_sale_date']
        has_recent_sale = last_sale and last_sale >= cutoff_date
        is_deleted = prod['deleted'] == 1

        if prod['name'] != prod['original_name']:
            stats['nombres_optimizados'] += 1

        # Check blacklist / whitelist
        if item_id in blacklist:
            stats['descartados_inactivos'] += 1
            archived_products.append({
                'item_id': item_id,
                'name': prod['name'],
                'barcode': prod['barcode'],
                'reason': 'Descartado por lista negra (blacklist) en config.json',
                'last_sale_date': last_sale,
                'stock_original': stock
            })
            continue

        force_keep = item_id in whitelist

        # Regla 4: Normalización de inventario negativo o micro-residuos
        if stock < 0:
            stats['stock_ajustado_cero'] += 1
            stock = 0.0
        elif 0 < stock < 1 and not has_recent_sale and not force_keep:
            stats['stock_ajustado_cero'] += 1
            stock = 0.0

        prod['stock'] = round(stock, 2)

        # Regla 2: Filtro de inactividad
        if not has_recent_sale and not force_keep:
            if is_deleted:
                stats['descartados_inactivos'] += 1
                archived_products.append({
                    'item_id': item_id,
                    'name': prod['name'],
                    'barcode': prod['barcode'],
                    'reason': 'Eliminado históricamente y sin ventas en el período',
                    'last_sale_date': last_sale,
                    'stock_original': prod['stock']
                })
                continue
            elif prod['stock'] <= 0:
                stats['descartados_inactivos'] += 1
                archived_products.append({
                    'item_id': item_id,
                    'name': prod['name'],
                    'barcode': prod['barcode'],
                    'reason': 'Sin stock físico y sin ventas en el período',
                    'last_sale_date': last_sale,
                    'stock_original': prod['stock']
                })
                continue

        # Regla 1: Asignación de Código de Barras y SKU
        barcode = prod['barcode']  # Ya validado: sólo si es numérico >= 8 dígitos
        item_id_str = str(item_id)

        if barcode:
            barcode_val = barcode
            # Si tiene código de barras, revisamos si tiene un SKU interno diferente
            if prod['internal_sku'] and prod['internal_sku'] != barcode:
                sku = prod['internal_sku']
            else:
                sku = barcode
        else:
            # Producto sin código de barras: barcode es None
            barcode_val = None
            # Para el SKU: si existe internal_sku lo usamos
            if prod['internal_sku']:
                sku = prod['internal_sku']
            elif item_id_str in sku_registry:
                sku = sku_registry[item_id_str]
                stats['skus_reutilizados'] += 1
            else:
                max_sbc_num += 1
                sku = f"{barcode_prefix}-{max_sbc_num:05d}"
                sku_registry[item_id_str] = sku
                stats['skus_nuevos_generados'] += 1

        # Garantizar unicidad de SKU si hubo colisión de códigos internos legacy
        if sku in seen_skus:
            # Si el SKU ya fue tomado por otro producto, desambiguamos con el item_id
            sku = f"{sku}-{item_id}"
        seen_skus.add(sku)

        prod['sku'] = sku
        prod['barcode_clean'] = barcode_val

        # Identificadores adicionales (UPC/EAN/ISBN de 1box y números extra):
        # se conservan TODOS los que no sean ya el código de barras o el SKU.
        used_codes = set()
        if barcode_val:
            used_codes.add(str(barcode_val).strip().upper())
        used_codes.add(str(sku).strip().upper())
        alt_codes = [
            c for c in unique_codes(prod.get('alt_candidates', []))
            if c.strip().upper() not in used_codes
        ]
        prod['alt_codes'] = alt_codes

        if alt_codes:
            stats['productos_con_codigos_alt'] += 1
            stats['codigos_alternativos_total'] += len(alt_codes)

        # Opcional (config.json): mostrar el código dentro del nombre,
        # solo si el nombre resultante no excede name_max_length.
        if embed_alt_code and alt_codes:
            candidate = f"{prod['name']} {alt_codes[0]}"
            if len(candidate) <= name_max_length:
                prod['name'] = candidate

        if prod['location']:
            stats['ubicaciones_exportadas'] += 1
        elif prod.get('location_was_junk'):
            stats['ubicaciones_basura_descartadas'] += 1
        active_products.append(prod)

    stats['activos_saneados'] = len(active_products)

    if TEST_MODE:
        print("[TEST] sku_registry.json NO se modificó (modo prueba).")
    else:
        save_sku_registry(sku_registry)

    # 6. Guardar en SQLite moderno
    print("[6/6] Guardando base de datos SQLite y exportando formatos...")
    if os.path.exists(OUTPUT_DB):
        try:
            os.remove(OUTPUT_DB)
        except Exception:
            pass

    conn_lite = sqlite3.connect(OUTPUT_DB)
    lite_cur = conn_lite.cursor()

    lite_cur.execute("""
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    lite_cur.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT UNIQUE NOT NULL,
            barcode TEXT,
            item_number TEXT,
            alt_codes TEXT,
            name TEXT NOT NULL,
            original_name TEXT,
            category TEXT,
            location TEXT,
            cost_price REAL DEFAULT 0.0,
            sale_price REAL NOT NULL,
            stock REAL DEFAULT 0.0,
            units_sold_period REAL DEFAULT 0.0,
            last_sold_at TEXT,
            legacy_item_id INTEGER
        )
    """)

    lite_cur.execute("""
        CREATE TABLE archived_products (
            item_id INTEGER PRIMARY KEY,
            name TEXT,
            barcode TEXT,
            reason TEXT,
            last_sale_date TEXT,
            stock_original REAL
        )
    """)

    unique_cats = sorted(list(set(p['category'] for p in active_products if p['category'])))
    for c in unique_cats:
        lite_cur.execute("INSERT INTO categories (name) VALUES (?)", (c,))

    for p in active_products:
        lite_cur.execute("""
            INSERT INTO products (
                sku, barcode, item_number, alt_codes, name, original_name, category, location,
                cost_price, sale_price, stock, units_sold_period, last_sold_at, legacy_item_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p['sku'],
            p['barcode_clean'],
            p.get('raw_item_number') or None,
            ' | '.join(p.get('alt_codes', [])) or None,
            p['name'],
            p['original_name'],
            p['category'],
            p['location'],
            p['cost_price'],
            p['unit_price'],
            p['stock'],
            p['units_sold_period'],
            str(p['last_sale_date']) if p['last_sale_date'] else None,
            p['item_id']
        ))

    for a in archived_products:
        lite_cur.execute("""
            INSERT INTO archived_products (item_id, name, barcode, reason, last_sale_date, stock_original)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            a['item_id'],
            a['name'],
            a['barcode'],
            a['reason'],
            str(a['last_sale_date']) if a['last_sale_date'] else None,
            a['stock_original']
        ))

    conn_lite.commit()
    conn_lite.close()

    # CSV
    try:
        with open(OUTPUT_CSV, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow([
                'SKU', 'Código de Barras', 'Nombre Optimizado', 'Nombre Original', 'Categoría', 'Ubicación',
                'Precio Costo', 'Precio Venta', 'Stock Actual',
                'Vendido en Período', 'Fecha Última Venta', 'ID Original',
                'UPC/EAN/ISBN (1box)', 'Códigos adicionales'
            ])
            for p in active_products:
                writer.writerow([
                    p['sku'],
                    p['barcode_clean'] if p['barcode_clean'] is not None else '',
                    p['name'],
                    p['original_name'],
                    p['category'],
                    p['location'],
                    f"{p['cost_price']:.2f}",
                    f"{p['unit_price']:.2f}",
                    f"{p['stock']:.2f}",
                    f"{p['units_sold_period']:.2f}",
                    str(p['last_sale_date']) if p['last_sale_date'] else '',
                    p['item_id'],
                    p['raw_item_number'],
                    extra_codes_for_export(p)
                ])
    except PermissionError:
        temp_csv = os.path.join(OUTPUT_DIR, 'inventario_test_temp.csv' if TEST_MODE else 'inventario_limpio_temp.csv')
        print(f"[AVISO] '{OUTPUT_CSV}' bloqueado. Guardando en '{temp_csv}'")
        with open(temp_csv, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow([
                'SKU', 'Código de Barras', 'Nombre Optimizado', 'Nombre Original', 'Categoría', 'Ubicación',
                'Precio Costo', 'Precio Venta', 'Stock Actual',
                'Vendido en Período', 'Fecha Última Venta', 'ID Original',
                'UPC/EAN/ISBN (1box)', 'Códigos adicionales'
            ])
            for p in active_products:
                writer.writerow([
                    p['sku'],
                    p['barcode_clean'] if p['barcode_clean'] is not None else '',
                    p['name'],
                    p['original_name'],
                    p['category'],
                    p['location'],
                    f"{p['cost_price']:.2f}",
                    f"{p['unit_price']:.2f}",
                    f"{p['stock']:.2f}",
                    f"{p['units_sold_period']:.2f}",
                    str(p['last_sale_date']) if p['last_sale_date'] else '',
                    p['item_id'],
                    p['raw_item_number'],
                    extra_codes_for_export(p)
                ])

    # Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario Saneado"

    headers = [
        'SKU', 'Código de Barras', 'Nombre Optimizado', 'Nombre Original', 'Categoría', 'Ubicación',
        'Precio Costo', 'Precio Venta', 'Margen %', 'Stock Actual',
        'Vendido en Período', 'Última Venta', 'ID Original',
        'UPC/EAN/ISBN (1box)', 'Códigos adicionales'
    ]
    ws.append(headers)

    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Segoe UI", size=10)
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    for col_num, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_idx, p in enumerate(active_products, 2):
        cost = p['cost_price']
        price = p['unit_price']
        margin = ((price - cost) / price * 100) if price > 0 else 0.0

        ws.append([
            p['sku'],
            p['barcode_clean'],
            p['name'],
            p['original_name'],
            p['category'],
            p['location'],
            cost,
            price,
            margin / 100.0,
            p['stock'],
            p['units_sold_period'],
            str(p['last_sale_date']) if p['last_sale_date'] else '',
            p['item_id'],
            p['raw_item_number'],
            extra_codes_for_export(p)
        ])

        ws.cell(row=row_idx, column=1).alignment = Alignment(horizontal="center")
        ws.cell(row=row_idx, column=2).alignment = Alignment(horizontal="center")
        ws.cell(row=row_idx, column=6).alignment = Alignment(horizontal="center")
        ws.cell(row=row_idx, column=7).number_format = '$#,##0.00'
        ws.cell(row=row_idx, column=8).number_format = '$#,##0.00'
        ws.cell(row=row_idx, column=9).number_format = '0.0%'
        ws.cell(row=row_idx, column=10).number_format = '#,##0.00'
        ws.cell(row=row_idx, column=11).number_format = '#,##0'
        ws.cell(row=row_idx, column=12).alignment = Alignment(horizontal="center")
        ws.cell(row=row_idx, column=13).alignment = Alignment(horizontal="center")

        for col_idx in range(1, len(headers) + 1):
            c = ws.cell(row=row_idx, column=col_idx)
            c.font = data_font
            c.border = thin_border

    # Pestaña Resumen
    ws_summary = wb.create_sheet(title="Resumen y Métricas")
    summary_data = [
        ["Métrica de Saneamiento", "Valor"],
        ["Fecha y hora de sincronización", start_time.strftime('%Y-%m-%d %H:%M:%S')],
        ["Total de registros analizados en base de datos operativa", stats['total_evaluados']],
        ["Productos activos y saneados en catálogo moderno", stats['activos_saneados']],
        ["Nombres de producto optimizados / acortados", stats['nombres_optimizados']],
        ["Productos descartados / archivados", stats['descartados_inactivos']],
        ["Duplicados fusionados (stock unificado)", stats['duplicados_fusionados']],
        ["Códigos SBC persistentes reutilizados", stats['skus_reutilizados']],
        ["Nuevos códigos SBC asignados", stats['skus_nuevos_generados']],
        ["Ajustes de inventario a cero (negativos/residuos)", stats['stock_ajustado_cero']],
        ["Productos activos con ubicación física (texto idéntico a 1box)", stats['ubicaciones_exportadas']],
        ["Ubicaciones heredadas de duplicados fusionados", stats['ubicaciones_heredadas_fusion']],
        ["Conflictos de ubicación por fusión (requieren revisión)", stats['ubicaciones_conflicto']],
        ["Ubicaciones basura descartadas (según config.json)", stats['ubicaciones_basura_descartadas']],
        ["Productos con identificadores alternativos (UPC/EAN/ISBN)", stats['productos_con_codigos_alt']],
        ["Identificadores alternativos conservados", stats['codigos_alternativos_total']],
        ["Ventana de ventas aplicada", f"Últimos {inactivity_days} días (desde {cutoff_date.strftime('%Y-%m-%d')})"],
    ]
    for r in summary_data:
        ws_summary.append(r)

    ws_summary.column_dimensions['A'].width = 55
    ws_summary.column_dimensions['B'].width = 30
    for c in range(1, 3):
        cell = ws_summary.cell(row=1, column=c)
        cell.fill = header_fill
        cell.font = header_font

    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col[:100]:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    temp_xlsx = os.path.join(OUTPUT_DIR, 'inventario_test_nuevo.xlsx' if TEST_MODE else 'inventario_limpio_nuevo.xlsx')
    try:
        wb.save(OUTPUT_XLSX)
        # Si antes quedó un archivo temporal por un bloqueo de Excel, ya no hace falta.
        if os.path.exists(temp_xlsx):
            try:
                os.remove(temp_xlsx)
            except OSError:
                pass
    except PermissionError:
        print(f"[AVISO] '{OUTPUT_XLSX}' está abierto en Excel. Se guardó como '{temp_xlsx}'")
        wb.save(temp_xlsx)

    if config.get('archive_old_exports', True) and not TEST_MODE:
        os.makedirs(HISTORICO_DIR, exist_ok=True)
        timestamp_str = start_time.strftime('%Y%m%d_%H%M%S')
        history_xlsx = os.path.join(HISTORICO_DIR, f'inventario_{timestamp_str}.xlsx')
        try:
            shutil.copy2(OUTPUT_XLSX, history_xlsx)
        except Exception:
            pass

    # Reporte Markdown
    with open(OUTPUT_REPORT, 'w', encoding='utf-8') as f:
        f.write(f"""# Reporte de Migración y Saneamiento Continuo de Inventario

Última sincronización: {start_time.strftime('%Y-%m-%d %H:%M:%S')}  
Ventana de actividad evaluada: **Últimos {inactivity_days} días** (Ventas desde: `{cutoff_date.strftime('%Y-%m-%d')}`)

## Métricas de la Sincronización

| Métrica | Resultado |
| :--- | :--- |
| **Total de productos analizados (MySQL 1box)** | **{stats['total_evaluados']:,}** |
| **Productos activos saneados en catálogo moderno** | **{stats['activos_saneados']:,}** |
| **Nombres de producto normalizados / optimizados** | **{stats['nombres_optimizados']:,}** |
| **Productos descartados / archivados** | **{stats['descartados_inactivos']:,}** |
| **Duplicados fusionados (stock sumado al maestro)** | **{stats['duplicados_fusionados']:,}** |
| **Códigos SBC persistentes reutilizados** | **{stats['skus_reutilizados']:,}** |
| **Nuevos códigos SBC generados en esta corrida** | **{stats['skus_nuevos_generados']:,}** |
| **Ajustes de inventario a cero (negativos/residuos)** | **{stats['stock_ajustado_cero']:,}** |
| **Productos activos con ubicación física (texto idéntico a 1box)** | **{stats['ubicaciones_exportadas']:,}** |
| **Ubicaciones heredadas de duplicados fusionados** | **{stats['ubicaciones_heredadas_fusion']:,}** |
| **Conflictos de ubicación por fusión (requieren revisión)** | **{stats['ubicaciones_conflicto']:,}** |
| **Ubicaciones basura descartadas (según config.json)** | **{stats['ubicaciones_basura_descartadas']:,}** |
| **Productos con identificadores alternativos (UPC/EAN/ISBN de 1box)** | **{stats['productos_con_codigos_alt']:,}** |
| **Identificadores alternativos conservados y buscables** | **{stats['codigos_alternativos_total']:,}** |

## Características del Proceso Continuo

1. **Normalización Masiva de Nombres:** Nombres compactos, sin conectores innecesarios y con abreviaturas comerciales estándar, preservando códigos, marcas y modelos.
2. **Historial Legacy Intacto:** Se mantiene la columna `original_name` en la base de datos y en Excel para cualquier auditoría comparativa.
3. **Idempotencia (SKUs Persistentes):** Cada producto sin código de barra conserva su mismo SKU asignado (`sku_registry.json`).
4. **Configuración Dinámica:** Reglas editables en `config.json`.
""")

    if TEST_MODE:
        write_location_test_report(start_time, source_locations, active_products,
                                   location_conflicts, stats, junk_locations)

    print("\nSINCRONIZACIÓN EXITOSA CON NORMALIZACIÓN DE NOMBRES.")
    print(f"Catálogo saneado: {stats['activos_saneados']:,} productos.")
    print(f"Nombres optimizados: {stats['nombres_optimizados']:,}")
    print(f"Identificadores alternativos: {stats['codigos_alternativos_total']:,} en {stats['productos_con_codigos_alt']:,} productos")
    print(f"Duración: {(datetime.datetime.now() - start_time).total_seconds():.1f}s")

if __name__ == '__main__':
    run_etl()
