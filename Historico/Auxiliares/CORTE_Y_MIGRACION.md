# Corte y migración del inventario

Acta del cierre: el inventario de la tienda **ya no está en `1box`**, está en
la base del POS. Este documento explica qué se hizo, cómo se verifica y cómo
se revierte.

---

## 1. Qué se hizo (16/11/2026)

| Paso | Resultado |
|---|---|
| Sincronización final de `1box` | Catálogo corregido: **6,992 productos** (13,491 registros analizados, 1,134 nombres optimizados, 2,043 códigos alternativos) |
| Importación al inventario del POS | **6,992 productos** en `pos.db` (tabla `products`), con existencias, precio, costo, categoría, ubicación y códigos |
| Migración del histórico | **1,136,448 registros** de `1box` en `historico_1box.db` (solo consulta): 269,519 ventas · 552,708 renglones · 272,975 pagos · 403 clientes · 123 proveedores · 17 empleados |
| Conciliación | **0 diferencias** entre el catálogo de origen y el inventario del POS |
| Sello | `INVENTARIO_CONGELADO.txt` con fecha y SHA-256 del snapshot |

Archivos del acta en `migracion\`:

| Archivo | Contenido |
|---|---|
| `corte_inventario_AAAAMMDD.md` | El acta (este resumen, con cifras) |
| `corte_inventario_AAAAMMDD.json` | Las mismas cifras en JSON (para automatizar) |
| `inventario_congelado_AAAAMMDD.csv` | Snapshot del inventario final, listo para Excel |
| `diferencias_AAAAMMDD.csv` | Diferencias detectadas (vacío = todo cuadró) |
| `verificacion_instalacion.json` | Resultado de la verificación en la PC nueva |

---

## 2. Cifras del inventario congelado

| Concepto | Valor |
|---|---|
| Productos | 6,992 |
| Unidades en existencia | 10,000,000,559,639 (ver la anomalía de abajo) |
| Valor a costo | $172,800,015,640,793.56 |
| Valor a precio de venta | $280,000,025,325,966.47 |
| Sin código de barras | 3,627 |
| Sin ubicación | 6,271 |
| Precio de venta ≤ 0 | 3 |
| Sin existencias | 639 |
| Códigos de barras repetidos | 12 |

### ⚠️ Anomalía heredada de `1box`

El producto **`CORRECTOR EN CINTA PASCUA` (SKU 91930)** tiene
**9,999,999,999,988 unidades** en `1box` (está así en
`phppos_location_items`). **No es un error de la migración**: la migración
copió el valor tal cual, y por eso las cifras de unidades y de valor del
inventario son absurdas.

Sin ese producto, el inventario real es de **≈559,651 unidades**.

**Qué hacer:** el gerente revisa la existencia real en la tienda y la corrige
en el POS:

```
F10 (Inventario) → buscar "CORRECTOR EN CINTA PASCUA"
→ cantidad correcta (por ejemplo 24) → motivo "conteo físico, corrección de 1box"
→ botón "− Baja"
```

Queda registrado con usuario, fecha y hora, y el valor del inventario vuelve a
ser creíble. También sirve como ejemplo de por qué el inventario se lleva
ahora desde la caja.

---

## 3. El inventario quedó bloqueado

`1box` ya no es la fuente. Para que nadie sincronice por inercia y pise las
existencias ya corregidas, el ETL quedó **bloqueado**:

```
C:\LaColonial> python etl_pipeline.py
SINCRONIZACIÓN BLOQUEADA: EL INVENTARIO YA ESTÁ CONGELADO
```

Si de verdad hay que rehacerlo (por ejemplo, si apareciera un error grave en
la migración), se hace a propósito:

```
python etl_pipeline.py --descongelar
```

y luego se vuelve a cerrar con:

```
python cerrar_inventario.py
```

---

## 4. Reimportar sin perder los movimientos

Si más adelante se actualiza el catálogo (altas en `1box`, precios nuevos):

```
importar_inventario.bat
```

Actualiza nombre, precio, costo, categoría, ubicación y códigos **conservando
las existencias que maneja la caja** y **sin borrar el historial de
movimientos**. Cada cambio queda registrado como movimiento tipo
`importación`.

Para el **corte final** (cuando se reemplaza el inventario real):

```
python cerrar_inventario.py
```

que además reinicia existencias, concilia y vuelve a sellar.

---

## 5. Consultar el histórico de `1box`

`historico_1box.db` es de **solo consulta**: no participa en la venta ni se
toca. Es una **copia completa** de `1box` al momento del corte (16/11/2026
20:01), con **1,857,063 registros** en 38 tablas y 5 vistas de reporte.

### Qué contiene

| Grupo | Tablas | Registros |
| :--- | :--- | ---: |
| Ventas | `sales`, `sale_items`, `payments` | 1,095,202 |
| Catálogo | `items`, `categories`, `item_variations`, `items_quantity_units`, `manufacturers`, `tags` | 16,206 |
| Personas | `people`, `customers`, `employees`, `suppliers` | 1,087 |
| Compras | `receivings`, `receiving_items`, `location_items` | 40,537 |
| **Cortes de caja** | `register_log`, `register_log_payments`, `register_log_denoms`, `register_log_audit` | 57,632 |
| **Conteos de inventario** | `inventory_counts`, `inventory_counts_items`, `inventory_log` | 611,231 |
| **Precios y reglas** | `items_pricing_history`, `price_rules`, `price_rules_items`, `price_rules_price_breaks`, `price_tiers` | 34,460 |
| **Gastos** | `expenses`, `expenses_categories` | 118 |
| Otros | `store_accounts`, `sales_coupons`, `additional_item_numbers`, `damaged_items_log` | 295 |

Los renglones de venta traen el **SKU del POS** (`sku`), así que se puede
cruzar el histórico con el inventario actual sin joins.

### Vistas listas para reportes

```sql
-- Ventas por día
SELECT * FROM v_ventas_dia ORDER BY fecha DESC LIMIT 30;

-- Ventas por método de pago
SELECT * FROM v_ventas_metodo_pago ORDER BY importe DESC;

-- Ventas por cajero
SELECT * FROM v_ventas_cajero ORDER BY total DESC;

-- Los 20 productos que más vendieron
SELECT sku, name, unidades, venta, utilidad, margen_pct
FROM v_margen_producto ORDER BY venta DESC LIMIT 20;

-- Productos con margen bajo (revisión de precios)
SELECT sku, name, unidades, venta, margen_pct
FROM v_margen_producto WHERE unidades > 5 AND margen_pct < 20
ORDER BY venta DESC;

-- Un día en detalle
SELECT si.sku, si.description, si.qty, si.total, si.profit
FROM sale_items si JOIN sales s ON s.sale_id = si.sale_id
WHERE s.fecha = '2026-11-16' ORDER BY si.total DESC;
```

### Lo que también quedó guardado (no estaba en la primera migración)

- **Cortes de caja de 1box** (`register_log`): 1,936 cierres con su apertura,
  cierre y los pagos contados. El último: 16/11/2026 09:06 → 19:30.
- **Conteos de inventario** (`inventory_counts`): 527 conteos con su fecha y
  responsable, y la bitácora diaria de existencias (`inventory_log`, 608,858).
- **Historial de precios** (`items_pricing_history`): 34,128 cambios de
  precio/costo con autor y fecha (sirve para saber si un precio estaba bien).
- **Gastos de la tienda** (`expenses`): 16 registros (agua, servicios…).
- **Reglas de precio**,variaciones de artículo, créditos a favor, cupones y
  proveedores de manufacture.

### Lo que **no** está (y no se va a migrar)

Configuración interna del sistema viejo: usuarios y permisos de OSPOS,
módulos, archivos de la aplicación, imágenes de artículos y atributos
personalizados. No sirven para operar ni para reportar el negocio.

### ¿Y si necesito algo más adelante?

`historico_1box.db` cubre **todo lo que el negocio necesita**: ventas,
renglones, pagos, clientes, proveedores, compras, cortes de caja, conteos,
gastos e historial de precios. Si alguna vez hiciera falta un dato que no
está ahí, `1box` sigue en su servidor (no lo hemos borrado ni modificado):
basta con consultarlo por SQL, como siempre.

### Reiniciar la caja (copia de fábrica)

Si quieres dejar la caja como recién salida de fábrica (**con el inventario
intacto**, pero sin usuarios, ventas ni turnos):

```
python reiniciar_caja.py
```

- Respalda la caja actual en `backups\pos_..._antes_del_reinicio.db`.
- Recrea `pos.db` y reimporta el inventario congelado (verificado por SHA-256
  contra el snapshot del acta: **idéntico**).
- Deja la caja lista para crear los PIN del gerente y del cajero.

Es lo que se hizo para el paquete de instalación (la copia de fábrica que va
en la computadora nueva).

---

## 6. Cómo revertir

Si algo salió mal y hay que volver atrás:

1. Cierra la caja (ventana negra).
2. Copia `pos.db` a `pos.db.antes_del_cierre`.
3. Copia el respaldo más reciente de `backups\` como `pos.db`.
4. Borra `INVENTARIO_CONGELADO.txt` (así se vuelve a permitir sincronizar).
5. Revisa `migracion\corte_inventario_AAAAMMDD.md` para saber qué se migró.

El catálogo limpio (`inventario_moderno.db`) y el snapshot
(`migracion\inventario_congelado_AAAAMMDD.csv`) permiten rehacer la
importación completa cuando quieras.

---

## 7. Resumen para la gerencia

- El inventario de la tienda (6,992 productos, con existencias) **ya está en
  el POS** y se mueve desde ahí con registro de quién, cuándo y por qué.
- `1box` queda como archivo histórico consultable; **no se usa para vender**.
- Queda **una sola existencia** que revisar (el corrector de la PASCUA) y
  **12 códigos de barras repetidos** que hay que resolver con los proveedores
  de etiquetas.
- El sistema está listo para operar en la computadora nueva: copia la carpeta,
  ejecuta el instalador portable y el verificador.

---
*Acta generada automáticamente por `cerrar_inventario.py` el 16/11/2026.*
