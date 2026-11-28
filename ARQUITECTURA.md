# Arquitectura del POS La Colonial

Cómo está construido el sistema, dónde vive cada dato y por qué.

---

## 1. Panorama

```
   1box (MySQL, XAMPP)                 YA NO SE USA PARA VENDER
   sistema anterior                    (se conserva como archivo histórico)
        │
        │  sólo lectura, una vez
        ▼
   Historico/Auxiliares/etl_pipeline.py  ──►  Historico/Auxiliares/inventario_moderno.db
                                            (catálogo limpio de referencia, 7,001 productos)
                                                   │
                                                   │  Historico/Auxiliares/importar_inventario.py
                                                   │  Historico/Auxiliares/cerrar_inventario.py
                                                   ▼
                          pos.db  ◄────── la caja (POS)
                     ┌───────────┴───────────┐
                     │  products             │  inventario propio + unidad
                     │  units / categories   │  catálogos administrables
                     │  locations / reasons  │
                     │  sales / sale_items    │  ventas
                     │  payments             │  formas de pago
                     │  returns              │  devoluciones
                     │  shifts               │  turnos
                     │  stock_movements      │  historial de existencias
                     │  notifications        │  avisos de stock negativo
                     │  users / sessions     │  usuarios y sesiones
                      │  customers            │  clientes
                      │  sale_customers       │  cliente por venta
                      │  proveedores          │  catálogo de proveedores
                     │  price_overrides      │  precios de emergencia
                     │  cash_movements       │  efectivo del turno
                     │  audit                │  bitácora
                     └───────────┬───────────┘
                                │
                                ▼
                    app_pos.py + ui/caja.*   (caja en el navegador)

   1box ──► migrar_historico.py ──►  Historico/historico_1box.db   (solo consulta, reportes)
```

El POS **no depende de `1box` en ningún momento**: lee y escribe únicamente en
`pos.db`. El catálogo limpio y el histórico son de consulta.

---

## 2. Componentes

| Archivo | Responsabilidad |
|---|---|
| `app_pos.py` | Servidor HTTP local (`127.0.0.1:8090`). Sirve el buscador (`/`) y la caja (`/pos`). Reinicia el inventario en memoria cuando cambia. |
| `catalogo.py` | Búsqueda en memoria. `Catalog` (catálogo limpio), `OwnedCatalog` (inventario del POS) y `StoreCatalog` (elige cuál según si el inventario propio ya fue cargado). |
| `pos_core.py` | Base de datos: esquema, usuarios, turnos, ventas, pagos, devoluciones, inventario, movimientos, bitácora y respaldos. |
| `pos_api.py` | API HTTP del POS (`/api/pos/*`), permisos por rol y página imprimible del ticket. |
| `core/suppliers.py` | Consulta, alta, edición y baja lógica de proveedores, con auditoría. |
| `importar_proveedores.py` | Importación idempotente desde el histórico, con respaldo previo y `--dry-run`. |
| `pos_print.py` | Ticket ESC/POS y envío directo a Windows (RAW, por ctypes); impresión local activa. |
| `ui/caja.html/js/css` | Interfaz de caja (teclado primero: escáner + atajos). |
| `ui/index.html/js/css` | Buscador de productos (heredado). |
| `Historico/Auxiliares/etl_pipeline.py` | Sincronización legacy desde `1box`. **Bloqueada** por el sello de congelamiento. |
| `Historico/Auxiliares/importar_inventario.py` | Carga el catálogo limpio al inventario del POS (legado). |
| `Historico/Auxiliares/cerrar_inventario.py` | Cierre: ETL + importación + conciliación + sello + acta (legado). |
| `Historico/migrar_historico.py` | Vuelca el histórico de `1box` a `historico_1box.db` (legado). |
| `verificar_instalacion.py` | Auto-chequeo de la instalación en otra computadora. |
| `restaurar_respaldo.py` | Lista, verifica e instala respaldos de `pos.db` (`--listar`, `--verificar`, `--restaurar --confirmar`). |
| `empaquetar_proyecto.py` | Genera el ZIP portable y su manifiesto con el nombre final de cada archivo. |
| `verificar_paquete.py` | Audita archivos, carpetas, manifiesto y contenido de las bases del ZIP. |
| `tests/test_packaging.py` | Regresiones aisladas de colecta, manifiesto y rutas de ticket/logo. |

---

## 3. Las dos bases

`pos.db` es la **única base operativa** (esquema v11, 23 tablas). El catálogo
limpio (`Historico/Auxiliares/inventario_moderno.db`) y el histórico
(`historico_1box.db`) son de **consulta/referencia** y nunca se escriben desde la
caja.

### `pos.db` — la base operativa (WAL, `synchronous=FULL`)

| Tabla | Para qué |
|---|---|
| `products` | Inventario propio: SKU, barcode, `alt_codes`, nombre, categoría, ubicación, **unidad**, costo, precio, stock, `active`, `source` |
| `units` | Unidades de venta: nombre, abreviatura, activa. Se administran desde la caja. |
| `categories` / `locations` / `reasons` | Catálogos administrables de categorías, ubicaciones y motivos de ajuste. |
| `stock_movements` | Cada cambio de existencia: tipo, cantidad, stock antes/después, **usuario**, **fecha y hora**, motivo, referencia |
| `notifications` | Un aviso por producto con stock negativo; se resuelve cuando el stock vuelve a `>= 0`. |
| `users` | Nombre, rol (`administrador`/`gerente`/`cajero`), PIN con PBKDF2-SHA256 y sal |
| `sessions` | Tokens de sesión con expiración (12 h) |
| `shifts` | Turnos: fondo, apertura, cierre, efectivo esperado, contado y diferencia |
| `sales` / `sale_items` | Venta y renglones con precio/costo congelados al vender; cada renglón guarda `stock_product_id` y `stock_factor`. `status`: `pending` (despachada por vendedor), `completed` o `cancelled`; `paid_at`/`paid_by` registran quién la cobró en Caja |
| `customers` | Clientes importados del histórico 1box, con datos de contacto y estado activo; incluye el público general predeterminado |
| `sale_customers` | Relación de cada venta nueva con el cliente y una copia de su nombre/RFC al vender. Evita alterar la tabla `sales` |
| `proveedores` | Empresa, contacto, RFC/cuenta, teléfono, correo, dirección, saldo, notas, estado, origen y `legacy_id` único para importación |
| `payments` | Efectivo, tarjeta o transferencia, con referencia |
| `returns` / `return_items` | Devoluciones por ticket, con folio `R-AAAAMMDD-0001` y el producto/factor con que se repuso el stock |
| `cash_movements` | Efectivo del turno: ventas, devoluciones, cancelaciones, ingresos y retiros |
| `shift_denoms` | Conteo físico por denominación al cerrar turno (`value`, `qty`, `amount`); se imprime en el corte y permite el retiro sugerido |
| `price_overrides` | Precios de emergencia (temporales, con motivo y caducidad) |
| `audit` | Bitácora de todo lo que hace una persona |
| `meta` | Versión del esquema y versión del catálogo |

### `Historico/historico_1box.db` — solo consulta (~162 MB)

Réplica del sistema anterior con vistas de reporte: `v_ventas_dia`,
`v_ventas_producto`, `v_ventas_cajero`, `v_ventas_metodo_pago`,
`v_margen_producto`. Los renglones traen el **SKU del POS** para poder cruzar
histórico e inventario actual.

---

## 4. Reglas de negocio implementadas

| Regla | Dónde |
|---|---|
| El precio y el costo se congelan al vender | `pos_core.create_sale` |
| El carrito manda la **clave del inventario** (`product_key` = id de `pos.db`), nunca el id de `1box` | `ui/caja.js` → `pos_api._checkout` |
| `legacy_item_id` sólo se guarda como referencia (para cruzar con el histórico) | `sale_items.legacy_item_id` |
| Vender descuenta existencias; devolver repone; cancelar devuelve | `_apply_stock` en `pos_core` |
| Todo cambio de existencia exige motivo y queda con usuario/fecha/hora | `pos_core.set_stock` |
| “Descuento” y “Abono a Cuenta” no se venden | `pos_api.blocked_reason` + `pos_config.json` |
| Los códigos de barras repetidos no se cobran a ciegas | `catalogo` marca `duplicate`; la UI exige elegir |
| Cancelar o devolver requiere PIN de gerente o administrador | `pos_api._require_manager` |
| Gerente y administrador comparten las operaciones; **sólo el administrador** gestiona usuarios | `pos_api._users`, `_user_active`, `_user_pin`, `_user_create` |
| Proveedores: cualquier sesión consulta; sólo gerente/administrador crea, edita o desactiva; toda modificación se audita | `pos_api._proveedores`, `_proveedor*`, `core/suppliers.py` |
| Una devolución/cancelación en efectivo de un turno cerrado se carga al turno abierto actual; sin turno abierto se rechaza | `pos_core.create_return`, `cancel_return`, `cancel_sale` |
| El stock negativo no bloquea la venta; genera un aviso persistente hasta corregirlo | `pos_core.notify_negative_stock`, `notifications` |
| La página del ticket exige un token HMAC por venta | `pos_core.ticket_token`, `app_pos` |
| Máximo 8 usuarios activos | `pos_core.create_user` + `security.max_users` |
| El precio de emergencia caduca a las 24 h o cuando el catálogo coincide | `pos_core.prune_price_overrides` |
| El inventario congelado bloquea la sincronización con `1box` | `etl_pipeline.check_congelado` |
| Terminal de Caja vs Mostrador: sólo Caja/Gerente abre/cierra turno o manipula cobro; Vendedores despachan y generan ticket con su nombre | `pos_api._require_cashier_authority` + `ui/caja.js` + `pos_print` |

---

## 5. API interna (`/api/pos/*`)

Todas exigen `X-Pos-Token` (salvo `info`, `health` y `login`).

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/info` | Estado del sistema (sin autenticar) |
| GET | `/health` | Latido (para reinicios y verificación) |
| POST | `/bootstrap` | Alta inicial de la caja (gerente + cajero) |
| POST | `/login` / `/logout` | Sesión por PIN |
| GET | `/state` | Usuario, turno, día, reglas, inventario |
| GET | `/search` | Búsqueda por código o nombre |
| GET | `/clientes?q=...&eliminados=0&limit=50` | Búsqueda de clientes para todos los usuarios con sesión |
| GET | `/cliente?id=...` | Ficha y últimas ventas asociadas en el POS |
| GET | `/proveedores?q=...&eliminados=0&limit=100` | Busca proveedores; cualquier usuario con sesión |
| GET | `/proveedor?id=...` | Ficha de proveedor; cualquier usuario con sesión |
| POST | `/proveedores/create` | Alta de proveedor (**gerente o administrador**) |
| POST | `/proveedores/update` | Edición de proveedor (**gerente o administrador**) |
| POST | `/proveedores/delete` | Desactivación lógica (**gerente o administrador**); queda en auditoría |
| POST | `/checkout` | Cobro (valida precios contra el inventario). Si el rol es `cajero`, el servidor lo desvía a `/dispatch` |
| POST | `/dispatch` | Despacho del vendedor: venta `pending`, descuenta inventario y no mueve efectivo; acepta `customer_id` opcional |
| POST | `/sale/pay` | Caja cobra un ticket `pending` (gerente o administrador) |
| GET | `/sales/pending` | Tickets despachados pendientes de cobro |
| POST | `/sale/cancel` | Cancelación con PIN de gerente (una venta `pending` repone el inventario) |
| POST | `/return/lookup` | Busca un ticket para devolver |
| POST | `/return` / `/return/cancel` | Devolución y anulación de devolución |
| POST | `/shift/open` / `/shift/close` | Turnos y corte. El cierre valida que el efectivo contado coincida con la suma del conteo por denominación y guarda el conteo en `shift_denoms` |
| POST | `/cash` | Entrada/salida de efectivo |
| GET | `/cash/movements` | Entradas y salidas del turno abierto |
| POST | `/cash/edit` | Corrige una entrada/salida manual (gerente o administrador; queda en bitácora) |
| POST | `/cash/delete` | Elimina una entrada/salida manual (gerente o administrador; queda en bitácora) |
| GET | `/today` | Ventas y devoluciones del día |
| GET | `/notifications` | Avisos de stock negativo (`?todas=1` incluye resueltos) |
| GET | `/export/sales` | Exporta CSV del día |
| GET | `/prices` | Precios de emergencia vigentes |
| POST | `/price/override` / `/price/revert` | Precio de emergencia (gerente o administrador) |
| GET | `/inventory/movements` | Historial de existencias |
| POST | `/inventory/stock` | Alta/baja de existencias |
| POST | `/inventory/product` / `/inventory/edit` | Alta y edición de producto (gerente o administrador) |
| POST | `/inventory/active` | Dar de baja o reactivar producto (**sólo administrador**) |
| POST | `/inventory/import` | Importa el catálogo limpio (administrador) |
| GET | `/users` · POST `/user` | Usuarios (**sólo administrador**) |
| POST | `/user/active` · `/user/pin` | Desactivar usuario y restablecer PIN (**sólo administrador**) |
| GET | `/audit` | Bitácora (gerente o administrador) |
| POST | `/backup` | Respaldo manual (gerente o administrador) |

`/checkout` también acepta `customer_id`. Si se omite, la venta se relaciona con
`PÚBLICO EN GENERAL`; si se elige otro cliente, se guardan su nombre y RFC (si
existe) como datos de la venta. La relación y la copia de nombre/RFC se guardan
en `sale_customers` dentro de la transacción, y el nombre se copia a
`sales.customer_name` para el ticket. `importar_clientes.py`
importa `Historico/historico_1box.db` en solo lectura y hace respaldo consistente
antes de escribir en `pos.db`.

`importar_proveedores.py` hace lo propio con las tablas `suppliers` y `people`
del histórico: abre el origen en solo lectura, crea un respaldo consistente y
verificado antes de `init_db()`, y planea/aplica un `UPSERT` por `legacy_id` en una transacción
`BEGIN IMMEDIATE`. Por defecto sólo completa campos vacíos; `--sobrescribir`
reemplaza campos administrados, saldo y activo, pero nunca las notas locales.
Los valores inválidos se omiten y se reportan. `--dry-run` no escribe en
`pos.db`. La importación es idempotente y puede correr con el servidor abierto.

Fuera de `/api/pos/*`, `app_pos.py` sirve la página imprimible
`GET /pos/ticket/<id>?token=...`; sin el token firmado responde `403`.

---

## 6. Decisiones y porqués

### Red y acceso

| | |
|---|---|
| Binding por omisión | `127.0.0.1` (sólo esta computadora) |
| Modo red | `python app_pos.py --pos --red` o `"network": {"allow_lan": true}` → escucha en `0.0.0.0` |
| Lista blanca | `"network": {"allowed_ips": ["192.168.0.55"]}` → el resto recibe 403 |
| Puerto | 8090 (si está ocupado usa 8091, 8092…); `--puerto N` fuerza uno |
| Instancia única | Si ya hay una caja corriendo, `caja.bat` reutiliza la existente en vez de abrir una segunda (evita dos cajas con el mismo stock) |
| Firewall | Windows ya permite `runtime\python.exe` en perfil Private; `abrir_puerto_firewall.bat` añade la regla si hace falta |

### Otras decisiones

| Decisión | Motivo |
| :--- | :--- |
| Base propia `pos.db` y no el catálogo del ETL | El ETL regenera `Historico/Auxiliares/inventario_moderno.db`; un POS que viva encima se rompería. Además permite que la caja sea dueña del inventario. |
| SQLite en modo WAL con `synchronous=FULL` | Cero dependencias, una sola archivo que respaldar y durability de las ventas. |
| Validación de precios en el servidor | El navegador no puede inventar precios ni productos. |
| Movimientos de inventario con usuario/fecha/hora | Trazabilidad de quién movió qué y por qué. |
| PIN con PBKDF2 y sesión con expiración | La base se puede copiar o leer sin exponer credenciales. |
| Sólo `127.0.0.1` | La caja no queda expuesta a la red local ni a internet por omisión. El acceso desde otros equipos de la red es opt-in (`caja_en_red.bat` o `network.allow_lan`), con lista blanca de IPs (`network.allowed_ips`) y PIN obligatorio en cada acceso. |
| Python portable en la carpeta | El proyecto se mueve a otra PC sin instalar nada ni depender del PATH. |
| Impresión local ESC/POS | POS-80 por USB; cada PC puede usar su agente local y el servidor central actúa como respaldo. |
| Sin CFDI | Decisión de negocio: el ticket interno basta. |
| Sin descuentos | Decisión de negocio. |

---

## 7. Qué falta (y está planeado)

| Pendiente | Notas |
|---|---|
| Historial de cortes consultable | Hoy el corte se imprime o se abre en el navegador y queda en `tickets/`; falta una pantalla para volver a ver cortes cerrados. |
| CFDI | Sólo si se requiere facturación fiscal (requiere PAC y datos fiscales). |
| Entradas de mercancía (compras) | Hoy el inventario se ajusta con altas/bajas manuales; falta el módulo de compras con proveedor. |
| Apartados / crédito | No está contemplado por ahora. |
| Segunda caja | El diseño lo permite (base compartida), pero no se ha probado. |

---

## 8. Operación segura

- **Respaldo**: al arrancar, cada **4 horas** si hubo ventas nuevas y hay turno
  abierto, y al cerrar turno. Se conservan **168 automáticos**; los manuales y
  de corte no se borran solos. Cada respaldo se verifica (`integrity_check` +
  conteos).
- **Restauración**: `restaurar_respaldo.py --listar` / `--restaurar ... --confirmar`;
  exige la caja cerrada y crea un respaldo `antes_restaurar` antes de reemplazar.
- **Cierre de inventario**: Historico/Auxiliares/cerrar_inventario.py concilia y sella.
- **Verificación**: `verificar_instalacion.py` comprueba base, permisos, puerto
  e impresora; deja reporte en `Historico/Auxiliares/migracion/`.
- **Reversión**: todo cambio de inventario queda en `stock_movements` y en
  `audit`, así que siempre se puede reconstruir qué pasó.
- **Tickets**: los enlaces llevan un token HMAC por venta; conocer el folio no
  permite abrir el ticket de otra persona.

---

## 9. Distribución y regresiones

- `empaquetar_proyecto.py` incluye `importar_proveedores.py`, `logo.ico`, la
  carpeta `core/` y la interfaz `ui/js/suppliers.js`.
- `tickets/` se excluye para no distribuir tickets operativos, pero se agrega
  `tickets/logo.png` si existe.
- La copia consistente de SQLite se empaqueta como **`pos.db`** y el manifiesto
  registra ese mismo nombre final y su SHA-256.
- `verificar_paquete.py` exige los archivos de proveedores, `core/` y la tabla
  `proveedores`; su resumen de bases compara el contenido de esa tabla.
  `verificar_instalacion.py` también exige esos archivos, la carpeta y la tabla.
- `tests/test_packaging.py` cubre la colecta, el manifiesto y las rutas
  `/pos/ticket/<id>` y `/pos/logo.ico` con handlers y datos ficticios, sin
  abrir servidores ni bases reales:

  ```powershell
  python -m unittest discover -s tests -v
  ```

---
*Arquitectura · POS La Colonial · 27/11/2026*
