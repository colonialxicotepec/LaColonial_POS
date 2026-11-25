# Acta de cierre y migración del inventario

**Fecha:** 2026-11-16 20:01:08
**Origen:** MySQL 1box (última sincronización)
**Destino:** inventario propio del POS (`pos.db`, tabla `products`)

## Resultado

| Concepto | Catálogo de origen | Inventario del POS |
| :--- | ---: | ---: |
| Productos | 6,992 | 6,992 |
| Unidades en existencia | 10,000,000,559,639 | 10,000,000,559,639 |
| Valor a costo | $172,800,015,640,827.50 | $172,800,015,640,793.56 |
| Valor a precio de venta | $280,000,025,325,966.47 | $280,000,025,325,966.47 |
| Sin código de barras | 3,627 | 3,627 |
| Sin ubicación | 6,271 | 6,271 |
| Precio de venta ≤ 0 | 3 | 3 |
| Sin existencias | 639 | 639 |
| Códigos de barras repetidos | 12 | 12 |

## Conciliación

- Diferencias detectadas: **0** (críticas: 0)
- Productos sólo en el POS (altas manuales): **0**
- Veredicto: **OK — el inventario del POS coincide con el origen**

Detalle de diferencias: `diferencias_20261116.csv` (vacío si no hay).
Snapshot del inventario congelado: `inventario_congelado_20261116.csv`
SHA-256 del snapshot: `56a200cce4c98fdd086314eb384f359ed7f381bd29839a70e63403add451d595`

## Existencia anómalas (requieren revisión del gerente)

Estos valores vienen así del sistema viejo (`1box`): no son un error de la
migración, pero **distorcionan el valor del inventario**. Se corrigen en el POS
con **Inventario → − Baja → cantidad correcta → motivo**.

| SKU | Producto | Existencia | ¿También está mal en 1box? |
| :--- | :--- | ---: | :--- |
| 91930 | CORRECTOR EN CINTA PASCUA | 9,999,999,999,988 | sí |

## Cómo revertir

1. Cerrar la caja.
2. Copiar `pos.db` a `pos.db.antes_del_cierre`.
3. Restaurar el respaldo más reciente de `backups/`.
4. Quitar `INVENTARIO_CONGELADO.txt` para volver a permitir sincronizaciones.

---
*Generado automáticamente por `cerrar_inventario.py`.*
