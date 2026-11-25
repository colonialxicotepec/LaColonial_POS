# Pendientes — 17 de noviembre de 2026

Documento de trabajo del POS **La Colonial**. Resume el estado del proyecto y
el plan para cerrar lo que falta. No sustituye a `README_POS.md`,
`ARQUITECTURA.md` ni `CORTE_Y_MIGRACION.md`: los complementa.

---

## 1. Resumen del estado

| Área | Estado |
|---|---|
| Caja (ventas, devoluciones, cancelaciones) | Funcionando |
| Turnos, cortes y movimientos de efectivo | Funcionando |
| Usuarios, roles y PIN | Funcionando |
| Inventario propio en `pos.db` (6,992 productos) | Cargado y congelado |
| Movimientos de existencias auditados | Funcionando |
| Bitácora y respaldos automáticos | Funcionando |
| Histórico de `1box` (`historico_1box.db`) | Migrado, solo consulta |
| Impresión de tickets | **Pendiente** (código listo, sin activar) |
| Usuarios en la instalación nueva | **Pendiente** (0 usuarios) |
| Calidad de datos del inventario | **Pendiente** (revisión en tienda) |
| Módulo de compras / proveedores | No iniciado (fase posterior) |

---

## 2. Pendientes ordenados por prioridad

### P1 — Operación diaria (bloquea el uso normal del mostrador)

#### 2.1 Activar la impresión de tickets
- Hoy **no se imprime**: el ticket solo se ve en pantalla y se guarda en
  `tickets\`.
- El código ESC/POS (`pos_print.py`) ya está escrito y probado con una
  **EPSON TM-T20II**.
- Hardware ya visible en la red según `migracion\verificacion_instalacion.json`:
  - `\\PC-4\EPSON TM-T20II Receipt`
  - `\\Caja\CAJA`
- **Falta decidir:**
  1. Qué impresora usa la caja.
  2. Ancho de papel: **80 mm → `width: 42`** o **58 mm → `width: 32`**.
  3. Si se abre el cajón al cobrar (`open_drawer`).
  4. Si además del ticket se imprime el corte de turno (`print_shift`).
- **Dónde se configura** (`pos_config.json`):

  ```json
  "printer": {
    "name": "\\\\PC-4\\EPSON TM-T20II Receipt",
    "width": 42,
    "open_drawer": false,
    "copies": 1,
    "print_sale": true,
    "print_shift": false
  }
  ```

- **Cómo se prueba:** menú **→ Probar impresora**. Si el ticket sale cortado,
  ajustar `width`.

#### 2.2 Crear los usuarios de la caja
- `verificacion_instalacion.json` reporta **0 usuarios activos** y **0 ventas**
  (esperado tras `reiniciar_caja.py`).
- Antes de operar hay que dar de alta, como mínimo:
  - 1 **administrador** o **gerente** (autoriza cancelaciones y devoluciones).
  - 1 **cajero**.
- Límite: 8 usuarios activos.
- PIN con PBKDF2 + sal; longitud mínima 4 (`security.min_pin_length`).

### P2 — Datos que requieren acción en tienda

#### 2.3 Existencia anómala: CORRECTOR EN CINTA PASCUA
- **SKU 91930** con **9,999,999,999,988 unidades** (valor copiado tal cual de
  `1box`; no es error de la migración).
- Distorsiona el valor total del inventario congelado.
- **Acción:** conteo físico y corrección en el POS:

  ```
  F10 (Inventario) → buscar "CORRECTOR EN CINTA PASCUA"
  → cantidad correcta → motivo "conteo físico, corrección de 1box"
  → botón "− Baja"
  ```

#### 2.4 Códigos de barras repetidos (12 casos)
- El POS no cobra a ciegas: al escanear muestra los productos y el cajero elige.
- **Acción:** resolver con los proveedores de etiquetas. Casos conocidos:
  - `834810001458` → Plumón Crayola vs Encendedor Toyo.
  - `787257129356` → juego de geometría grande vs mediano.

#### 2.5 Conflictos de ubicación por fusión de duplicados (7 casos)
- Reportados en `reporte_test_ubicaciones.md` (modo `--test`).
- **Acción:** revisión manual y corrección de la ubicación física en el POS.

#### 2.6 Productos con precio ≤ 0 (3) y sin existencias (639)
- **Acción:** revisar si son reales o residuos del sistema anterior; corregir
  precio o dar de baja.

#### 2.7 Calidad de datos a mediano plazo
- **3,627 productos sin código de barras** (se buscan por nombre).
- **6,271 productos sin ubicación** (se muestran en ámbar en el buscador).
- **Acción:** plan de etiquetado y levantamiento de ubicaciones por pasillo.

### P3 — Verificación del corte

#### 2.8 Diferencia de $33.94 en el valor a costo
- El acta reporta **0 diferencias**, pero el valor a costo difiere entre origen
  y POS:
  - Origen: `$172,800,015,640,827.50`
  - POS: `$172,800,015,640,793.56`
  - Diferencia: **$33.94**
- Probable causa: redondeo o un producto con costo fraccionario.
- **Acción:** confirmar el origen del centavo antes de dar el corte por bueno;
  ajustar el acta si corresponde.

### P4 — Documentación y limpieza

#### 2.9 Rutas inconsistentes en la documentación
- `INSTALACION.md`, `caja_en_red.bat` y el README mencionan `C:\LaColonial` y
  `C:\pos_moderno`, pero el proyecto está en `D:\LaColonial_POS`.
- **Acción:** confirmar la ruta real de la PC de caja y unificar los documentos
  y lanzadores.

#### 2.10 Estado del ETL legacy
- `etl_pipeline.py` está **bloqueado** por `INVENTARIO_CONGELADO.txt`.
- **Acción:** documentarlo como legado y dejar claro que `1box` ya no es fuente;
  recargar catálogo solo con `importar_inventario.bat` / `cerrar_inventario.py`.

### P5 — Fuera de alcance (decisiones ya tomadas)

| Tema | Decisión |
|---|---|
| CFDI / facturación fiscal | No |
| Descuentos | No |
| Apertura de cajón | Por ahora no (se reevalúa con la impresión) |
| Impuestos desglosados | No (el precio se cobra tal como está) |
| Apartados y crédito a clientes | No contemplado |
| Segunda caja / sucursales | Diseño lo permite, sin probar |

### P6 — Fase posterior (sin fecha)

#### 2.11 Módulo de compras / entradas de mercancía
- Hoy el inventario solo se ajusta con altas/bajas manuales.
- Falta: proveedor, orden de compra, recepción, costo y actualización de
  existencias con movimiento auditado.

---

## 3. Plan de ejecución propuesto

| Paso | Tarea | Responsable | Depende de |
|---|---|---|---|
| 1 | Confirmar impresora, ancho de papel, cajón e impresión de corte | Gerencia | — |
| 2 | Configurar `pos_config.json` y probar con "Probar impresora" | Sistemas | Paso 1 |
| 3 | Crear usuarios administrador/gerente y cajero | Gerencia | — |
| 4 | Aclarar la diferencia de $33.94 en el valor a costo | Sistemas | — |
| 5 | Conteo físico de CORRECTOR EN CINTA PASCUA y corrección en POS | Tienda | — |
| 6 | Revisar precios ≤ 0 y productos sin existencias | Tienda | — |
| 7 | Resolver conflicto de ubicaciones (7 casos) | Tienda | — |
| 8 | Unificar rutas y estado de impresión en la documentación | Sistemas | Pasos 2 y 9 |
| 9 | Plan de etiquetado y ubicaciones (3,627 / 6,271) | Tienda + Sistemas | — |
| 10 | Evaluar módulo de compras | Gerencia | Operación estable |

**Criterio para considerar la caja "lista para operar":** impresión activa y
probada, usuarios creados, existencia de PASCUA corregida y respaldo reciente
verificado con `verificar_instalacion.bat`.

---

## 4. Decisiones pendientes del usuario

1. ¿Impresora `\\PC-4\EPSON TM-T20II Receipt` o `\\Caja\CAJA`?
2. ¿Papel de 80 mm (`42`) o 58 mm (`32`)?
3. ¿Abrir el cajón al cobrar?
4. ¿Imprimir también el corte de turno?
5. ¿Quién atiende la corrección de datos en tienda y en qué fecha?
6. ¿El módulo de compras entra en esta fase o queda para después?

---

## 5. Referencias

| Documento | Para qué |
|---|---|
| `README_POS.md` | Flujo de cobro, inventario, cortes y límites |
| `ARQUITECTURA.md` | Cómo está construido y por qué |
| `INSTALACION.md` | Instalación en la computadora nueva y respaldos |
| `CORTE_Y_MIGRACION.md` | Acta del cierre y cómo revertir |
| `migracion\corte_inventario_20261116.md` | Cifras del inventario congelado |
| `migracion\verificacion_instalacion.json` | Resultado del auto-chequeo |

---

*Pendientes · POS La Colonial · 17/11/2026*
