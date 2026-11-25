# Guía de Consultas SQL - Base de Datos Original (`1box`)

Este documento recopila las consultas SQL analíticas sobre la base de datos operativa original de **XAMPP (`1box`)**, con explicaciones técnicas, resultados y casos de uso.

---

## Índice de Consultas

1. [Consulta 1: Productos con nombres mayores a 59 caracteres](#consulta-1-productos-con-nombres-mayores-a-59-caracteres)
2. [Consulta 2: Ubicación física de los productos](#consulta-2-ubicación-física-de-los-productos)
3. [Consulta 3: Identificadores en UPC/EAN/ISBN y números adicionales](#consulta-3-identificadores-en-upceanisbn-y-números-adicionales)

---

## Consulta 1: Productos con nombres mayores a 59 caracteres

### Objetivo
Identificar productos cuyos nombres sean excesivamente largos (más de 59 caracteres incluyendo espacios), útiles para auditar tickets de venta, pantallas de visualización pequeña o etiquetas de código de barras.

### Código SQL

> [!NOTE]
> Se utiliza `CHAR_LENGTH(name)` en lugar de `LENGTH(name)` para contar **caracteres reales** (letras, espacios, tildes y caracteres especiales) y no el número de bytes que ocupan en UTF-8.

#### Opción A: Ver todos los productos (activos y eliminados)
```sql
SELECT 
    item_id,
    CHAR_LENGTH(name) AS longitud,
    name AS nombre_producto,
    item_number AS codigo_barras,
    cost_price AS costo,
    unit_price AS precio,
    deleted AS eliminado
FROM phppos_items
WHERE CHAR_LENGTH(name) > 59
ORDER BY longitud DESC;
```

#### Opción B: Filtrar solo productos activos (`deleted = 0`)
```sql
SELECT 
    item_id,
    CHAR_LENGTH(name) AS longitud,
    name AS nombre_producto,
    item_number AS codigo_barras,
    unit_price AS precio
FROM phppos_items
WHERE deleted = 0 
  AND CHAR_LENGTH(name) > 59
ORDER BY longitud DESC;
```

#### Opción C: Conteo y métricas de longitud
```sql
SELECT 
    COUNT(*) AS total_mas_de_59,
    SUM(deleted = 0) AS activos,
    SUM(deleted = 1) AS eliminados,
    MAX(CHAR_LENGTH(name)) AS longitud_maxima,
    MIN(CHAR_LENGTH(name)) AS longitud_minima
FROM phppos_items 
WHERE CHAR_LENGTH(name) > 59;
```

---

### Resumen de Resultados en `1box`

* **Total de productos que superan 59 caracteres:** **230 productos**
  * **Activos (`deleted = 0`):** 136 productos
  * **Eliminados / Descontinuados (`deleted = 1`):** 94 productos
* **Longitud máxima detectada:** **98 caracteres**

#### Muestra de los nombres más largos detectados:
| ID | Caracteres | Nombre del Producto | Estatus |
| :---: | :---: | :--- | :---: |
| `1404` | 98 | `Calculadora Científica con 401 Funciones 2 Líneas en Pantalla Solar + Batería Casio Modelo FX991MS` | Activo |
| `5668` | 98 | `  6.Kraft por Metro Delgado 125gr  Alto 125cm 155 GMS X METRO  6.50 POR KIL0 Y RINDE 80 METROS EL ` | Activo *(con notas internas en el nombre)* |
| `10143` | 86 | `Marcador Permanente Grueso Colores Surtidos Blíster con 8 Piezas Sharpie Chisel/Cincel` | Activo |
| `10602` | 85 | `Folder de Plástico Carta con Costilla Grande de 15 mm Naranja Neón Transparente Smart` | Eliminado |
| `10554` | 85 | `Marcador para Colorear Delgado Colores Pastel Estuche con 12 Piezas Supertips Crayola` | Eliminado |
| `3679` | 83 | `Escuadra Semiprofesional con Graduación con Bisel Número 28 Baco Modelo SP 28-45/60` | Activo |



---

## Consulta 2: Ubicación física de los productos

### Objetivo
Obtener la ubicación física (pasillo/estante/posición) que el personal capturó para cada producto. Ese dato **no** está en `phppos_items`: vive en la tabla puente por sucursal `phppos_location_items`, columna `location`.

> [!IMPORTANT]
> No confundir `location_id` (sucursal) con `location` (ubicación física dentro de la tienda).

```sql
SELECT
    li.item_id,
    i.name AS producto,
    i.item_number AS codigo_barras,
    li.location AS ubicacion,
    li.quantity AS stock,
    i.deleted AS eliminado
FROM phppos_location_items li
JOIN phppos_items i ON i.item_id = li.item_id
WHERE li.location_id = 1
  AND li.location IS NOT NULL
  AND li.location <> ''
ORDER BY li.location, i.name;
```

### Conteo y valores distintos

```sql
SELECT
    COUNT(*) AS productos_con_ubicacion,
    COUNT(DISTINCT location) AS ubicaciones_distintas
FROM phppos_location_items
WHERE location_id = 1
  AND location IS NOT NULL
  AND location <> '';

SELECT location, COUNT(*) AS productos
FROM phppos_location_items
WHERE location_id = 1
  AND location IS NOT NULL
  AND location <> ''
GROUP BY location
ORDER BY productos DESC;
```

### Notas de calidad detectadas
* Solo una sucursal activa: `location_id = 1` (**Centro – La Colonial**).
* Cobertura baja: **745** productos con ubicación de más de 13,000.
* Conviven formatos distintos: `A01-006`, `1A`, `406`, e incluso `.` (cuarenta registros, sin significado útil; el pipeline los descarta mediante `location_junk_values`).
* Los valores numéricos de las láminas escolares parecen ser el número de catálogo de la lámina, no una ubicación física.

---

## Consulta 3: Identificadores en UPC/EAN/ISBN y números adicionales

### Objetivo
El personal de captura usó el campo **UPC/EAN/ISBN** (`phppos_items.item_number`) y la tabla de **números de artículo adicionales** también para códigos internos, modelos o claves que no son el código de barras principal. Si no se consideran, el producto no se encuentra al teclearlos o escanearlos.

### Código SQL

```sql
SELECT
    i.item_id,
    i.name AS producto,
    i.item_number AS upc_ean_isbn,
    i.product_id AS sku_interno,
    GROUP_CONCAT(a.item_number ORDER BY a.item_number SEPARATOR ' | ') AS adicionales
FROM phppos_items i
LEFT JOIN phppos_additional_item_numbers a ON a.item_id = i.item_id
WHERE i.deleted = 0
GROUP BY i.item_id
HAVING i.item_number <> '' OR adicionales IS NOT NULL
ORDER BY i.name;
```

### Notas de calidad detectadas
* **2,006 productos activos** del catálogo moderno conservan **2,043 identificadores** que no son el código de barras principal (incluye `item_number` y los adicionales).
* Ejemplos: `Abaco Chico Baco` se capturó con `1234`; `Bolsa A Granel De Nylon` con `6432`, `BN1520`, `BN1825`, `BN2030` y `BN2535`; `Colores c/24 Prismacolor Junior` con `070530014860`.
* El pipeline los conserva en `products.item_number` y `products.alt_codes`, y el buscador los indexa para búsqueda exacta.
* En Excel/CSV se exportan al final como **“UPC/EAN/ISBN (1box)”** y **“Códigos adicionales”**.
* No se recomienda pegar estos códigos al nombre: rompería la deduplicación y ensuciaría tickets/etiquetas. Si se requiere, `config.json` tiene `embed_alt_code_in_name` (con `name_max_length: 59`).

---

## Normalización Masiva de Nombres Implementada

### Reglas Aplicadas en el Pipeline (`etl_pipeline.py`)

1. **Abreviaturas comerciales universales:**
   * `Tamaño Carta` $\rightarrow$ `T/C` \| `Tamaño Oficio` $\rightarrow$ `T/O`
   * `con X Piezas` $\rightarrow$ `c/X pzas` \| `con X Hojas` $\rightarrow$ `c/X hjs`
   * `Estuche` $\rightarrow$ `Est` \| `Blíster` $\rightarrow$ `Blist` \| `Paquete` $\rightarrow$ `Paq`
   * `Fluorescente` $\rightarrow$ `Fluor` \| `Semiprofesional` $\rightarrow$ `Semiprof`
   * `Transparente` $\rightarrow$ `Transp` \| `Separadores` $\rightarrow$ `Sep`
   * `Colores Surtidos` $\rightarrow$ `Surt` \| `Colores Pastel` $\rightarrow$ `Pastel`
   * Medidas: `32 x 64 mm` $\rightarrow$ `32x64mm`
2. **Supresión selectiva de conjunciones/conectores:**
   * Eliminación de `de`, `con`, `en`, `para`, `por`, `del`, `el`, `la` cuando el nombre excede 48 caracteres, preservando marcas compuestas (ej. `De La Rosa`).
3. **Preservación total de códigos y marcas:**
   * Modelos, calibres y códigos alfanuméricos (`FX991MS`, `SP 28-45/60`, `#24`, `15mm`, `155g`) se mantienen intactos.
4. **Trazabilidad (Columna `original_name`):**
   * El nombre original de MySQL se preserva en la base de datos SQLite y en la columna de Excel para auditoría comparativa.

### Métricas de la Normalización
* **Nombres optimizados en el catálogo activo:** **1,134 productos**
* **Nombres con más de 59 caracteres:** **20** (máximo 71). Se conservan completos por legibilidad y para no perder códigos o modelos; si se requiere el límite estricto de ticket, se puede recortar en la importación al POS.
