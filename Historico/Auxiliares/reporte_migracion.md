# Reporte de Migración y Saneamiento Continuo de Inventario

Última sincronización: 2026-11-16 20:01:08  
Ventana de actividad evaluada: **Últimos 365 días** (Ventas desde: `2025-11-16`)

## Métricas de la Sincronización

| Métrica | Resultado |
| :--- | :--- |
| **Total de productos analizados (MySQL 1box)** | **13,491** |
| **Productos activos saneados en catálogo moderno** | **6,992** |
| **Nombres de producto normalizados / optimizados** | **1,134** |
| **Productos descartados / archivados** | **5,922** |
| **Duplicados fusionados (stock sumado al maestro)** | **577** |
| **Códigos SBC persistentes reutilizados** | **991** |
| **Nuevos códigos SBC generados en esta corrida** | **0** |
| **Ajustes de inventario a cero (negativos/residuos)** | **38** |
| **Productos activos con ubicación física (texto idéntico a 1box)** | **721** |
| **Ubicaciones heredadas de duplicados fusionados** | **2** |
| **Conflictos de ubicación por fusión (requieren revisión)** | **7** |
| **Ubicaciones basura descartadas (según config.json)** | **30** |
| **Productos con identificadores alternativos (UPC/EAN/ISBN de 1box)** | **2,006** |
| **Identificadores alternativos conservados y buscables** | **2,043** |

## Características del Proceso Continuo

1. **Normalización Masiva de Nombres:** Nombres compactos, sin conectores innecesarios y con abreviaturas comerciales estándar, preservando códigos, marcas y modelos.
2. **Historial Legacy Intacto:** Se mantiene la columna `original_name` en la base de datos y en Excel para cualquier auditoría comparativa.
3. **Idempotencia (SKUs Persistentes):** Cada producto sin código de barra conserva su mismo SKU asignado (`sku_registry.json`).
4. **Configuración Dinámica:** Reglas editables en `config.json`.
