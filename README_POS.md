# Caja (POS) · La Colonial

Punto de venta **propio**, local y autónomo. El inventario vive dentro del POS
(`pos.db`) y la caja **no depende de `1box`** para funcionar.

> **Cambio de rumbo (16/11/2026):** el POS ya no es una contingencia que corre
> "al lado" de `1box`. `1box` sólo se usó para **traer el inventario limpio**
> (una vez, con `Historico\Auxiliares\importar_inventario.bat`). A partir de ahí el POS es el dueño
> del inventario, de los precios y de las existencias. `1box` ya no se consulta
> ni se modifica.

### Decisiones vigentes

| Tema | Decisión |
|---|---|
| Inventario | **Propio del POS** (`pos.db`). Se importó el catálogo limpio; al 18/11/2026 hay 7,001 productos (6,999 activos) y se actualiza desde el panel Inventario. |
| `1box` | Sólo lectura histórica. **Ya no se consulta ni se escribe.** La sincronización ETL queda como respaldo, no como fuente diaria. |
| CFDI | **No.** El ticket es interno. |
| Impresión | **Activa.** Impresora térmica **POS-80** por USB (papel 80 mm). Cada venta imprime su ticket con el TOTAL en doble tamaño y corte de papel; el corte de caja también se imprime al cerrar el turno. |
| Cajón de dinero | **No se abre ni se cierra físicamente.** `open_drawer: false` (no hay cajón conectado). |
| Descuentos | **No hay.** |
| Devoluciones | **Sí**: por ticket, totales o parciales, con motivo y PIN de gerente/administrador. |
| Altas y bajas | En el POS: **alta de producto**, **baja (desactivar)**, **alta/baja de existencias** y **edición de stock**, todo con usuario, fecha y hora. |
| Usuarios | Hasta **8** activos. Roles: **administrador**, **gerente** y **cajero**. |
| Cajas | **Una sola caja**, una sola sucursal. |
| Red local | La caja **sólo responde en esta computadora** (`127.0.0.1`). Para entrar desde otro equipo de la red hay que abrirla con `caja_en_red.bat` (o `"allow_lan": true` en `pos_config.json`), y se puede restringir por IP con `allowed_ips`. Ver `INSTALACION.md` §7-bis. |

---

## 1. Arranque

| Acción | Cómo |
|---|---|
| Abrir la caja | Doble clic en **`caja.bat`** |
| Abrir la caja para otros equipos de la red | Doble clic en **`caja_en_red.bat`** → entra en `http://<IP>:8090/pos` con tu PIN |
| Abrir el buscador | Doble clic en **`buscar_productos.bat`** |
| Actualizar catálogo | Doble clic en **`Historico\Auxiliares\actualizar_inventario.bat`** (no se puede con la caja cobrando) |
| Dirección | `http://127.0.0.1:8090/pos` (solo esta computadora) |

Si ya hay una caja abierta, `caja.bat` **no arranca otra**: reutiliza la que
está corriendo y abre el navegador. Por omisión no se abre nada a la red local
ni a internet; el acceso desde otros equipos de la red es opt-in
(`caja_en_red.bat` o `"allow_lan": true` en `pos_config.json`, con lista
`allowed_ips` para restringir por equipo). Detalle en `INSTALACION.md` §7-bis.

Para cerrar: cierra la ventana negra o presiona `Ctrl+C` en ella. **No cierres
la ventana mientras haya una venta a medias.**

---

## 2. Primera vez (solo una vez)

1. Abre `caja.bat`.
2. La pantalla pide configurar la caja:
   - nombre y PIN del **administrador** (4 a 8 dígitos),
   - nombre y PIN del **cajero**.
3. Los PIN se guardan cifrados (PBKDF2-SHA256). No se pueden leer desde la base.
4. Desde **Menú → Usuarios** (sólo el administrador) se crean los demás
   usuarios (hasta **8**), se cambia su rol o PIN y se desactivan.

### Roles

| Rol | Puede |
|---|---|
| **Administrador** | Todo: ventas, turnos, cortes, cancelaciones y devoluciones, inventario, **alta/reactivación/baja de productos**, precios de emergencia, respaldos, bitácora y **administración de usuarios** (alta, baja, cambio de rol y PIN). |
| **Gerente** | Las mismas operaciones que el administrador (ventas, turnos, cortes, cancelaciones y devoluciones con su PIN, inventario, precios de emergencia, respaldos y bitácora), **excepto administrar usuarios y dar de baja/reactivar productos**. |
| **Cajero** | Cobrar, abrir/cerrar turno, consultar ventas del día. No ajusta inventario, no cancela ni devuelve sin autorización y no ve la bitácora. |

> **Sólo el administrador** da de alta usuarios, los desactiva y cambia su rol
> o PIN. Si se olvida un PIN, el administrador lo restablece desde
> **Menú → Usuarios → PIN**. El gerente no puede hacerlo: así queda una sola
> persona responsable de las credenciales.

---

## 2-bis. Inventario: cómo se trae y cómo se mueve

### Traer el inventario (una vez, o para refrescar)

La importación del catálogo **no está en la pantalla de la caja**: se hace con
el archivo `.bat` (sólo administrador, y conviene hacerlo con la caja cerrada
o sin ventas en curso):

```
Historico\Auxiliares\importar_inventario.bat
```

Copia el catálogo limpio (`Historico/Auxiliares/inventario_moderno.db`) al
inventario del POS (`pos.db`), emparejando por **SKU**:

- Actualiza datos de productos (nombre, precio, costo, categoría, ubicación).
- **Conserva el stock local** (lo que se ha movido en el POS); sólo los productos
  nuevos entran con el stock del catálogo.
- Con `--reiniciar-stock` el stock se reemplaza por el del catálogo (sirve para
  el corte final).
- Con `--reemplazar` desactiva lo que ya no exista en el origen.

A tener en cuenta:

- **Sobrescribe precios y nombres**: si corregiste un precio a mano en el POS,
  la importación lo regresa al precio del catálogo.
- **Reactiva** los productos que coincidan por SKU, aunque estuvieran dados de baja.
- No borra productos ni toca ventas, devoluciones o turnos; los cambios quedan
  en la auditoría (`importacion_inventario`).
- Los precios nuevos aplican en la siguiente venta (sube la versión del catálogo).

Después del corte, `1box` deja de usarse: el inventario se mueve **desde el POS**.

### Mover el inventario (cada día)

**F10** o botón **Inventario** (administrador o gerente):

| Acción | Cómo | Qué queda registrado |
|---|---|---|
| **Alta de existencias** | Buscar producto → cantidad → motivo (`Error de Conteo` por defecto, `Compra`, `Caducidad`…) → **+ Alta** | Movimiento `alta`, usuario, fecha y hora |
| **Baja de existencias** | cantidad → motivo → **− Baja** | Movimiento `baja`, usuario, fecha y hora |
| **Motivos de movimiento** | **Administrar motivos…** en Movimientos de existencias | Agregar, eliminar o reactivar motivos (`Error de Conteo` predeterminado) |
| **Editar producto** | Botón **Editar** en la lista → nombre, código, **unidad**, **categoría**, **ubicación**, precio, costo → **Guardar cambios** | Auditoría del cambio |
| **Alta de producto** | **Nuevo producto**: nombre, SKU precargado (`M-00016`, corroborado libre y editable), código, **unidad**, **categoría** (`Papelería` por defecto), **ubicación**, precio, costo y existencias iniciales | Alta + movimiento inicial |
| **Unidades de venta** | Menú → **Unidades de venta**, o **Administrar unidades…** en el producto | Agregar o eliminar unidades (`Pieza`, `Kilogramo`, `Paquete`, `Caja`, `Gramo`…) |
| **Categorías** | **Administrar categorías…** en el producto | Agregar, eliminar o reactivar categorías (`Papelería` predeterminada) |
| **Ubicaciones** | **Administrar ubicación…** en el producto | Agregar, eliminar o reactivar ubicaciones (`fila 1 sección 1` a `fila 5 sección 2`) |
| **Baja de producto** | **Dar de baja el producto** | Deja de ofrecerse al cobrar; se puede reactivar |
| **Empaque / presentación** | En **Editar** (o **Nuevo producto**): “Empaque de otro producto” → elige el producto suelto y las **unidades por empaque** | Vínculo auditado; las ventas descuentan del producto base |
| **Precio de emergencia** | Botón **precio** en la lista | Caduca a las 24 h o cuando el catálogo traiga el mismo precio |

El panel de la derecha muestra el **historial de movimientos** del producto:
cantidad, tipo, **usuario**, **fecha**, **hora**, motivo y existencia resultante.
En un empaque, los movimientos mostrados son los del producto base (allí es
donde se descuenta el stock).

### Empaques y presentaciones

Un artículo que se vende **por paquete** puede vincularse al producto suelto
(la base). Ejemplo: `Block Remision…` se vende suelto a $13.50; el
`Paquete c/10` se vincula al block con **10 por empaque**.

- **Crear o editar**: en el producto empaque, sección **Empaque de otro
  producto**, buscar el producto base y escribir las **unidades por empaque**.
  Con **Quitar vínculo** el empaque vuelve a tener existencias propias.
- **Existencias**: el empaque no tiene stock propio; muestra
  `stock del base ÷ unidades por empaque`. Ej. base con 19 piezas y empaque de
  10 → **1.90 empaques** disponibles.
- **Venta**: vender 1 empaque descuenta del base la cantidad indicada
  (1 × 10 = 10 piezas). En la lista el empaque se ve con el distintivo
  `📦 10× base`, y el producto suelto con `📦 Tiene empaque`.
- **Búsqueda y escaneo**: al buscar por nombre o código cualquiera de los dos,
  la lista muestra **siempre juntas** la pieza y su paquete. Al escanear el
  código de un producto que tiene empaque, el primer `Enter` **no** agrega la
  pieza directo: muestra ambas presentaciones para elegir con `↑` `↓` (o clic)
  y confirmar con un segundo `Enter`.
- **Devolución y cancelación**: reponen al base las piezas correspondientes;
  nunca se duplica stock.
- **Alta/baja manual** sobre el empaque mueve el stock del base con su factor
  (dar de alta 2 empaques = +20 piezas).
- **Restricciones**: no se pueden anidar empaques (un empaque no puede ser base
  de otro), ni vincularse a sí mismo, ni a un producto dado de baja. Si el base
  se da de baja, el empaque deja de venderse (aparece “Su producto base está
  dado de baja”). Los precios de venta y de costo son **independientes** entre
  base y empaque.

### Unidades de venta

- Todo producto nuevo nace como **Pieza**; en el alta o en la edición se elige
  **Kilogramo**, **Paquete**, **Caja** o cualquier unidad que se haya agregado.
- **Menú → Unidades de venta** (o **Administrar unidades…** dentro del
  producto) permite **agregar** unidades con su abreviatura (ej. `Gramo` / `gr`)
  y **eliminarlas**. Una unidad en uso no se puede eliminar: primero hay que
  cambiar los productos que la usan. Se puede **reactivar** una eliminada.
- La abreviatura se muestra en el carrito y en el ticket: `0.75 kg`,
  `250 gr`, `2 pza`, `1 caja`.
- La cantidad acepta hasta **2 decimales**. Los botones `−` / `+` siempre
  suman o restan **1**; para una fracción se escribe directamente en la
  cantidad.

---

## 3. Flujo diario

### Abrir turno
1. Entrar con PIN.
2. Escribir el **fondo inicial** (efectivo con el que empieza la caja).
3. “Abrir caja”.

### Cobrar
1. **Escanea** el producto (el lector USB escribe y da Enter) o busca por nombre.
2. `Enter` agrega el producto. `↑` / `↓` elige cuando hay varias coincidencias.
3. Ajusta cantidades con `−` / `+` (suman o restan **1**) o quita con `✕`.
   Para vender por peso, haz **clic en la cantidad** y escribe decimales
   (hasta 2, por ejemplo `0.75`). La unidad del producto (`pza`, `kg`, `paq`,
   `cja`) aparece junto a la cantidad y en el ticket.
4. **F4** o botón **COBRAR**.
5. Elige **Efectivo / Tarjeta / Transferencia**.
   - Efectivo: escribe lo recibido (o usa los botones $50/$100/…). El cambio se calcula solo.
   - Tarjeta/transferencia: se registra el cobro hecho en la terminal bancaria.
6. **Enter** o **Cobrar**. Se imprime el ticket y se abre el cajón (si está configurado).
7. `Enter` en “Nueva venta” para el siguiente cliente.

### Asociar un cliente

- Por defecto, la venta se registra a nombre de **PÚBLICO EN GENERAL**.
- Si necesitas identificar al comprador, pulsa **Elegir otro** en el carrito antes
  de cobrar o despachar.
- Busca por nombre, RFC/cuenta, teléfono o correo; **Ficha** muestra datos de
  contacto y las ventas asociadas en esta caja.
- Pulsa **Usar este cliente en la venta**. El ticket imprime el nombre y, si hay
  uno registrado, el RFC. La venta se vincula al registro para consultar el
  historial del POS.
- La pantalla **Menú → Consultar clientes** permite buscar sin asociar una venta.
- Los clientes importados como dados de baja aparecen sólo al activar
  **Incluir dados de baja**; no se pueden asociar a nuevas ventas. El cliente
  genérico detectado en el histórico se conserva como referencia, pero se oculta
  del selector para no atribuirle operaciones por error.
- Los saldos y límites están etiquetados como **históricos 1box**; no son una
  cuenta de crédito operativa de la caja actual.

La importación se ejecuta desde la carpeta del proyecto:

```text
runtime\python.exe importar_clientes.py
```

El importador crea un respaldo verificado de `pos.db`, lee
`Historico\historico_1box.db` en solo lectura e importa/actualiza los clientes
en una transacción. Se puede ejecutar con la caja abierta; no cierra el turno.
Después de desplegar el nuevo código, hay que reiniciar el proceso del servidor
para que active las rutas de clientes. Ese reinicio no hace corte de caja: el
turno y su fondo siguen guardados.

### Cancelar
- **Antes de cobrar:** quita la línea o vacía el carrito (F8). No requiere permiso.
- **Venta ya cobrada:** `F9` → **Cancelar** → motivo + **PIN del gerente o administrador**.
  La venta queda marcada como cancelada y el efectivo se descuenta del corte.
  Si ya tenía devoluciones, sólo se repone lo que falta y se reintegra el
  importe pendiente: la devolución no se duplica.
  Una devolución de una venta cancelada ya no se puede anular por separado.

### Devolver
1. `F9` → **Devolver** (en la venta) o escribe el **folio** del ticket y pulsa **Buscar**.
2. Aparece cada línea con lo vendido, lo ya devuelto y lo pendiente. Por defecto se
   devuelve todo lo pendiente; puedes cambiar la cantidad línea por línea.
3. Escribe el **motivo** y el **PIN del gerente o administrador**.
4. Elige la **forma de devolución**:
   - **Efectivo**: sale de la caja (queda registrado en el corte). Si el turno
     original ya cerró, el reintegro se carga al **turno abierto actual**; sin
     turno abierto no se permite una devolución en efectivo.
   - **Tarjeta / transferencia**: se reembolsa en la terminal y queda anotado.
5. Se imprime un **comprobante de devolución** (folio `R-AAAAMMDD-0001`).
6. Una devolución se puede **anular** desde la misma lista (queda el registro).
   Si la venta se cancela después, la devolución queda incluida en la
   cancelación y ya no se puede anular por separado.

No se puede devolver más de lo vendido, ni devolver dos veces la misma línea.

### Despachar (vendedores) y cobrar en Caja

- En las terminales cuyo usuario es **cajero**, el botón dice **DESPACHAR (F4)**:
  registra la venta como **pendiente de cobro**, descuenta el inventario y saca
  el ticket con la leyenda `*** PENDIENTE DE COBRO EN CAJA ***`.
- El efectivo **no** entra al turno hasta que Caja la cobra.
- En Caja, el botón **Por cobrar** (o `F6`) lista los tickets pendientes; el
  botón principal cambia a **COBRAR PENDIENTES (N) F4** cuando hay alguno.
- **Cobrar** pide la forma de pago y registra el efectivo en el turno, dejando
  `Vendedor:` y `Cobró:` en el ticket. **Cancelar** devuelve el inventario.
- Si un vendedor intenta cobrar directo (`/checkout`), el servidor lo fuerza a
  despacho: sólo **gerente** o **administrador** completan el cobro.

### Entradas y salidas de efectivo

`F7` (o **Entradas / Salidas**) registra dinero ajeno a las ventas:

- **+ Entrada**: cambio adicional, aportación, fondo extra.
- **− Salida / gasto**: papelería, garrafón, pago a proveedor, retiro parcial.

Cada movimiento pide monto y concepto, y ajusta el **efectivo esperado** del
turno y el corte (líneas *Entradas* y *Salidas*).

**Corregir capturas erróneas (gerente/administrador):** en *Movimientos del
turno actual*, cada renglón tiene **Editar** y **Eliminar**.

- **Editar** precarga monto y concepto; permite cambiar de entrada a salida (o
  al revés). **Guardar cambios** aplica; **Cancelar edición** no toca nada.
- **Eliminar** pide confirmación y borra el movimiento.
- Sólo aplica al **turno abierto**. Las ventas, devoluciones y cancelaciones se
  corrigen desde `F9`, no aquí.
- Cada corrección queda en la **bitácora** (`movimiento_efectivo_editado`,
  `movimiento_efectivo_eliminado`) con usuario, valores anteriores y nuevos.

### Cerrar turno
1. Botón **Corte de caja**.
2. **Paso 1 — conteo por denominación:** captura cuántas piezas hay de cada
   moneda y billete. Se muestra el **total contado** y, si el efectivo supera el
   fondo máximo, un **retiro sugerido** en billetes (fondo por omisión $1,400,
   configurable con `rules.fondo_maximo`). El botón **🖨 Imprimir conteo** saca
   una hoja con el desglose.
3. **Continuar al cierre →** pasa el total al campo de efectivo contado y
   muestra el **efectivo esperado** y la **diferencia**. El campo de efectivo
   es de solo lectura: si el monto no cuadra con lo que contaste, se corrige en
   el paso 1 (el servidor no acepta un corte cuyo conteo no coincida con el
   total declarado).
4. Si quedan **tickets por cobrar**, la pantalla lo avisa antes de cerrar: no
   son efectivo de este turno y se cobran en el siguiente.
5. Se pueden escribir **notas del cierre** (opcional): quedan en el corte
   impreso y sirven para explicar una diferencia.
6. Al confirmar se genera el **corte con el desglose por denominación, el
   retiro sugerido, las devoluciones, las entradas/salidas de efectivo, los
   pendientes de cobro y las notas**, se respalda `pos.db` y se cierra el turno.
   Si la impresora está apagada (`printer.print_shift`), el corte se abre en una
   ventana del navegador para imprimirlo o consultarlo.
   El retiro sugerido es informativo: si se retira efectivo, se registra aparte
   en **Entradas / Salidas (F7)** como salida.

### Ajustes de `pos_config.json`

| Llave | Qué hace |
|---|---|
| `rules.fondo_maximo` | Efectivo que conviene dejar en el cajón para el día siguiente (por omisión `1400`). El contador en pantalla y el corte impreso usan el mismo valor; se aplica sin reiniciar el POS. |
| `rules.max_cash_input` | Tope de una entrada o salida de efectivo. |
| `printer.print_sale` / `printer.print_shift` | Si están en `false`, el ticket o el corte no se mandan a la impresora: el corte se abre en el navegador. |
| `printer.name` | Nombre de la impresora ESC/POS local. |
| `backup.auto_minutes` | Cada cuánto se hace el respaldo automático de `pos.db`. |

---

## 4. Atajos de teclado

| Tecla | Acción |
|---|---|
| `F2` | Enfocar el buscador |
| `Enter` | Agregar el producto / confirmar cobro |
| `↑` `↓` | Elegir resultado |
| `F4` | Cobrar (o despachar, si eres vendedor) |
| `F6` | Tickets pendientes de cobro (Caja) |
| `F7` | Entradas y salidas de efectivo (Caja) |
| `F8` | Vaciar carrito (pide confirmación) |
| `F9` | Ventas del día / devolver / reimprimir |
| `F10` | Inventario (altas, bajas y ajuste de existencias) |
| `Esc` | Cerrar ventana o limpiar búsqueda |

---

## 5. Impresión

**Configurada (24/11/2026).** Impresora térmica **POS-80** por USB (`USB001`),
papel de **80 mm**, sin cajón de dinero.

- Cada venta **imprime su ticket** directamente en la térmica (`print_sale: true`),
  con el **TOTAL en doble tamaño** y corte de papel automático.
- Los tickets **despachados** (pendientes de cobro) también se imprimen, marcados
  como `*** PENDIENTE DE COBRO EN CAJA ***`.
- El **corte de caja** también se imprime en la térmica al cerrar el turno
  (`print_shift: true`), con el conteo por denominación, el retiro sugerido,
  las devoluciones, los pendientes de cobro y las notas de cierre.
- La impresión va por **ESC/POS directo** (bytes crudos a la impresora), no por el
  driver de Windows: por eso el TOTAL sale en doble alto/ancho y el papel se
  corta solo.
- **Menú → Probar impresora** manda un ticket de prueba en cualquier momento.
- Cada ticket queda además guardado en texto en `tickets/AAAA-MM-DD/`.
- Si la impresora falla, **la venta no se pierde**: se queda guardada y el ticket
  se puede reimprimir desde **F9 → Reimprimir** o abrir en el navegador con
  **Ver ticket** (`/pos/ticket/<id>?token=...`). El enlace lleva un **token
  firmado por venta**; sin ese token la página responde `403`, porque un folio
  consecutivo ya no basta para abrir tickets ajenos.

### Cada PC imprime en su propia impresora

La caja central guarda todas las ventas, pero **cada computadora tiene su
propia impresora**. Para que el ticket salga por la impresora USB de la PC
donde se cobró, esa PC corre un **agente de impresión local**.

**En cada PC de cajero:**

1. Copia **3 archivos** a una carpeta (por ejemplo `C:\LaColonialImpresora\`):
   - `impresora_local.py`
   - `pos_print.py`
   - `pos_config.json` (ponle el nombre de **su** impresora en `printer.name`)
   - y el `runtime\python.exe` (o Python instalado).
2. Copia `impresora.bat` (o crea uno con
   `runtime\python.exe impresora_local.py`).
3. **Arranca `impresora.bat`** y déjalo abierto mientras cobras en esa PC.
4. Abre el POS en el navegador: la cabecera muestra
   **🖨 POS-80** (impresora local detectada) o **🖨 sin impresora local**.

**Cómo funciona**

- El agente escucha en `127.0.0.1:8099` de **esa** PC: no se ve desde la red.
- El navegador detecta el agente y le manda las líneas del ticket; el agente
  las convierte a ESC/POS con la **configuración local** (ancho y estilo del
  TOTAL de esa PC) y las manda a su impresora.
- El servidor **no** imprime cuando el navegador se encarga: así el ticket
  sale en la PC del cajero y no en la caja central.
- Si el agente no está corriendo, el POS avisa en la cabecera y **el ticket se
  imprime en la impresora de la caja central** (comportamiento anterior), para
  que no se pierda el comprobante.
- El **corte de caja** también sale en la impresora de la PC que lo cierra.

**Seguridad:** el agente sólo acepta peticiones del origen de la caja
(`http://<ip>:8090` o `localhost`) y sólo escucha en `127.0.0.1`. Un sitio web
ajeno no puede imprimir en tu impresora.

### Ajustes de impresora

| Llave | Valor actual | Para qué |
|---|---|---|
| `printer.name` | `POS-80` | Impresora destino. Fijada a mano para no depender de la predeterminada de Windows. |
| `printer.width` | `42` | Caracteres por línea: 42 ≈ rollo de 80 mm (en 58 mm sería `32`). |
| `printer.print_sale` | `true` | Imprimir el ticket en cada venta. |
| `printer.print_shift` | `true` | Imprimir el corte al cerrar el turno. |
| `printer.open_drawer` | `false` | No hay cajón conectado; el efectivo se cuenta a mano en el corte. |
| `printer.copies` | `1` | Copias por ticket (reservado, hoy no se usa). |
| `printer.total_style` | `raster` | Cómo se imprime el TOTAL: `raster` = dibujado como **imagen de puntos** (trazo de 6 px, mucho más grande y grueso); `escpos` = texto con doble tamaño (por si la impresora no acepta el comando de imagen). |

### El TOTAL gigante

El TOTAL no se imprime como texto con doble tamaño (ESC/POS sólo admite 2x2):
se **dibuja como imagen de puntos** (`GS v 0`) con una fuente 5x7 escalada ×6, o
sea un trazo de **6 píxeles de grosor** y 42 de alto (medio centímetro). La
escala se ajusta sola para que el importe quepa en el ancho del rollo, y se
centra en el marco del ticket. La palabra **TOTAL** se dibuja con la misma
fuente a escala 4, para que el número mande.

Si una impresora concreta no acepta `GS v 0` (sale basura en vez del número),
basta con poner `"total_style": "escpos"` en `pos_config.json` y el TOTAL vuelve
a imprimirse como texto en doble tamaño y negritas, sin tocar código.

### Dejar el cajón

El cajón **no** se abre: `printer.open_drawer: false` (no hay cajón conectado a
la impresora). El efectivo se cuenta a mano en el corte de caja.

---

## 6. Respaldos

| Cuándo | Dónde |
|---|---|
| Al arrancar el servidor | `backups/pos_AAAAMMDD_HHMMSS_arranque.db` |
| Cada **4 horas**, sólo si hubo ventas nuevas y hay un turno abierto | `backups/pos_AAAAMMDD_HHMMSS_auto.db` |
| Al abrir turno | `backups/pos_AAAAMMDD_HHMMSS_turno.db` |
| Al cerrar turno | `backups/pos_AAAAMMDD_HHMMSS_corte.db` |
| Menú → Respaldar ahora | `backups/pos_AAAAMMDD_HHMMSS_manual.db` |

- Se conservan los últimos **168 respaldos automáticos** (`backup.keep_auto`),
  unos 7 días a 6 respaldos por día.
- Los respaldos **manuales y de corte nunca se borran solos**.
- Cada respaldo se verifica al crearse (`PRAGMA integrity_check` y conteos de
  ventas, productos y usuarios); si no pasa, se descarta y se avisa.
- El intervalo se ajusta en `pos_config.json` → `"backup": {"intervalo_horas": 4,
  "keep_auto": 168}`. El plazo se cuenta desde el último respaldo (arranque,
  turno, corte o manual).
- El respaldo es una copia completa y consistente de `pos.db` (API de SQLite,
  no copia de archivo).
- `backups/ultimo_respaldo.json` deja la fecha, ruta y conteos del último
  respaldo correcto.

### Restaurar un respaldo

1. Cierra la caja (la ventana negra).
2. Lista y verifica los respaldos:

   ```
   runtime\python.exe restaurar_respaldo.py --listar
   ```

3. Restaura uno (pide `--confirmar` y comprueba que la caja esté cerrada):

   ```
   runtime\python.exe restaurar_respaldo.py --restaurar backups\pos_....db --confirmar
   ```

Antes de reemplazar `pos.db` se crea un respaldo de seguridad
`pos_..._antes_restaurar.db`; si la restauración no pasa la verificación, no se
aplica. También queda el método manual: guardar `pos.db` como
`pos.db.dañado` y copiar el respaldo elegido como `pos.db`.

---

## 7. Qué guarda la caja (`pos.db`)

El esquema actual es la **versión 6** y tiene **20 tablas**.

| Tabla | Contenido |
|---|---|
| `users` | Usuarios, rol (`administrador` / `gerente` / `cajero`) y PIN cifrado |
| `sessions` | Sesiones activas (se expiran a las 12 h sin uso) |
| `products` | **Inventario propio**: SKU, códigos, nombre, categoría, ubicación, precios, existencias, activo/baja |
| `units` | Unidades de venta (Pieza, Kilogramo, Paquete, Caja…) |
| `categories` / `locations` / `reasons` | Catálogos administrables de categorías, ubicaciones y motivos de movimiento |
| `stock_movements` | **Historial de existencias**: tipo, cantidad, existencia antes/después, usuario, fecha y hora, motivo |
| `shifts` | Turnos: fondo, apertura, cierre, esperado, contado, diferencia |
| `sales` | Venta: folio diario, totales, cambio, estado, cancelación |
| `sale_items` | Renglones con nombre, precio, costo y cantidad **congelados** (y el producto/factor de stock que movieron) |
| `payments` | Efectivo, tarjeta o transferencia (con referencia) |
| `returns` / `return_items` | Devoluciones: folio `R-...`, ticket origen, motivo, forma y el producto/factor con que se repuso el stock |
| `price_overrides` | Precios de emergencia (locales, temporales, con motivo y caducidad) |
| `cash_movements` | Entradas/salidas de efectivo, ventas, cancelaciones y devoluciones |
| `notifications` | **Avisos de stock negativo**, uno por producto, hasta que se corrija |
| `audit` | Bitácora: entradas, ventas, cancelaciones, devoluciones, ajustes de stock, cortes, respaldos |
| `meta` | Versión del esquema y versión del catálogo |

El folio es `AAAAMMDD-0001`, consecutivo por día. El histórico de ventas no se
borra: cancelar cambia el estado y deja rastro de quién autorizó y por qué.

### Las ventas mueven el inventario

Al cobrar, el POS **descuenta** las existencias del producto y deja el
movimiento (`venta`, usuario, fecha y hora). Al devolver, las repone
(`devolucion`). Al cancelar una venta, las devuelve (`cancelacion`). Al anular
una devolución, las vuelve a descontar (`anulacion`).

### Exportar para conciliar con `1box`

**Menú → Exportar ventas de hoy (CSV)** (o el botón del modal de ventas) deja
`exportes/ventas_AAAAMMDD.csv` con ventas y devoluciones del día. Es el archivo
que se usa para capturar o conciliar contra `1box` mientras siga siendo el
sistema oficial.

---

## 8. Stock, stock negativo y actualización del inventario

El POS es el dueño del inventario:

- La tabla `products` de `pos.db` **es** el inventario de la tienda.
- Las ventas, devoluciones y cancelaciones lo actualizan con movimientos auditados.
- `Historico/Auxiliares/inventario_moderno.db` (el catálogo limpio que generó el
  ETL desde `1box`) es la **fuente de la carga inicial** y quedó como referencia;
  se puede volver a importar, pero **no** se consulta en el día a día.
- Mientras el inventario propio esté vacío, la caja usa el catálogo limpio como
  apoyo (modo transitorio). Ya no aplica: el inventario fue importado.

### Stock negativo (venta sin existencias)

- El stock negativo **no bloquea la venta**: el sistema permite vender y deja el
  saldo en negativo a propósito (el conteo físico puede estar desfasado).
- Los productos con stock negativo se muestran en **rojo** en la búsqueda y en
  el carrito, con la etiqueta **“Stock negativo”**.
- Además se crea un **aviso** en el botón **Avisos** de la barra superior, con
  contador. El aviso es **uno por producto** y **permanece hasta que el stock se
  corrija** a cero o más; no se puede “descartar” a mano.
- Los avisos se generan al detectar el negativo en una búsqueda o al cobrar, y
  se cierran solos cuando un alta, devolución o ajuste deja el stock en `>= 0`.
- La consulta está en **`/api/pos/notifications`** y la tabla `notifications`.

### Recargar el catálogo

Cuando `1box` se retire, se puede dejar de correr `Historico\Auxiliares\actualizar_inventario.bat`
(ETL). Si alguna vez se necesita recargar el catálogo, se usa
`Historico\Auxiliares\importar_inventario.bat`.

---

## 9. Lo que no hace todavía

- Facturación CFDI (decidido: no).
- Apertura/cierre físico del cajón (decidido: por ahora no).
- **Descuentos** (decidido: no hay).
- Apartados y crédito a clientes.
- Impuestos desglosados (el precio se cobra tal como está en el catálogo).
- Segunda caja o varias sucursales.
- **Historial de cortes consultable en la app**: el corte se imprime (o se abre
  en el navegador) al cerrar y queda copiado en `tickets/AAAA-MM-DD/`, pero
  todavía no hay una pantalla para volver a ver cortes ya cerrados.

Todo lo demás ya funciona: ventas, devoluciones, inventario con movimientos
auditados, altas/bajas de productos, usuarios y roles, despacho y cobro de
tickets pendientes, entradas y salidas de efectivo, conteo por denominación
al cerrar el turno, cortes y respaldos.

---

## 9-bis. Precios de emergencia

Para cuando el precio del catálogo no sirve (por ejemplo, el gerente cambió el
precio y todavía no se ha recargado el catálogo):

1. El **gerente o administrador** pulsa **precio** en el producto de la lista.
2. Escribe el precio nuevo, el motivo y su PIN.
3. El producto queda marcado **“Precio emergencia”** y se cobra a ese precio.
4. Caduca solo a las **24 h** (`rules.override_hours`) o en cuanto el catálogo
   traiga el mismo precio.
5. **Menú → Precios de emergencia** muestra los ajustes vigentes y permite
   revertirlos.

El ajuste **no escribe en `1box`** y queda en la bitácora con quién lo hizo.

---

## 10. Reglas de catálogo aplicadas en caja

- **No se venden** “Descuento” ni “Abono a Cuenta” (artículos de control interno)
  ni productos con precio menor a `$0.01`.
- **Códigos de barras compartidos** (12 casos conocidos): el escaneo **no cobra
  a ciegas**; muestra los productos y el cajero elige. Ejemplos:
  `834810001458` (Plumon Crayola vs Encendedor Toyo) y `787257129356`
  (juego de geometría grande vs mediano).
- **Productos sin código**: se buscan por nombre (hay ~3,600).
- **Productos con stock 0 o negativo**: se pueden vender (el stock del catálogo
  puede estar desfasado). El 0 se muestra con aviso; el negativo, en **rojo** y
  con un **aviso persistente** hasta corregir el inventario. Se puede prohibir la
  venta sin existencias con `rules.allow_zero_stock: false`.
- El precio y el costo se **congelan al momento de la venta**; si el catálogo
  cambia después, el ticket histórico no cambia.

---

## 11. Archivos del proyecto

| Archivo | Función |
|---|---|
| `caja.bat` | Abre la caja (sólo esta computadora) |
| `caja_en_red.bat` | Abre la caja **accesible desde la red local** (imprime la dirección para entrar) |
| `abrir_puerto_firewall.bat` | Abre el puerto 8090 en el firewall de Windows (como administrador) |
| `instalar_python_portable.bat` | Deja Python 3.12 dentro de `runtime\` (sin instalar nada en Windows) |
| `verificar_instalacion.bat` / `.py` | Auto-chequeo de la instalación (archivos, base, clientes, permisos, puerto, impresora) |
| `importar_clientes.py` | Importación segura e idempotente de clientes desde el histórico |
| `empaquetar_proyecto.bat` / `.py` | Genera el ZIP portable para otra computadora |
| `verificar_paquete.py` | Audita el ZIP contra el proyecto (archivo por archivo y base por base) |
| `reiniciar_caja.py` | Deja la caja como de fábrica (conserva el inventario, borra usuarios/ventas/turnos) |
| `restaurar_respaldo.py` | Lista, verifica y restaura respaldos (`--listar`, `--verificar`, `--restaurar --confirmar`) |
| `Historico/historico_1box.db` | Histórico del sistema anterior (solo consulta, reportes) |
| `Historico/consultar_historico.py` / `.bat` | Interfaz web de solo consulta del histórico anterior |
| `Historico/migrar_historico.py` | Vuelca el histórico de `1box` a `historico_1box.db` (legado) |
| `Historico/Auxiliares/` | Herramientas y archivos legados de migración: `importar_inventario`, `cerrar_inventario`, `etl_pipeline`, catálogo limpio y actas |
| `backups/` | Respaldos automáticos y manuales verificados |
| `tickets/` | Copia en texto de cada ticket, devolución y corte |
| `exportes/` | CSV de ventas y devoluciones |
| `vendor/` | Python portable (para instalar sin internet) |

### Documentos

| Documento | Para quién |
|---|---|
| **`INSTALACION.md`** | Instalar en la computadora nueva (Windows 11 limpio) |
| **`MANUAL_RAPIDO.md`** | El personal de caja (una página) |
| **`Historico/Auxiliares/CORTE_Y_MIGRACION.md`** | Qué se congeló, cifras del cierre, cómo revertir y cómo consultar el histórico |
| **`ARQUITECTURA.md`** | Cómo está construido el sistema, tablas, API y decisiones |
| `README_POS.md` (este) | Operación y mantenimiento |

---

## 12. Problemas comunes

| Síntoma | Qué hacer |
|---|---|
| “No hay conexión con el servidor local” | La ventana negra se cerró. Abre `caja.bat` otra vez. |
| “Ya hay un turno abierto” | Alguien no cerró el turno. Entra con el usuario que lo abrió y usa **Corte de caja**. |
| El ticket no sale | Menú → Probar impresora. Revisa papel, cable/red y que la impresora esté encendida. La venta ya quedó guardada. |
| El cajón no abre | Verifica que esté conectado a la impresora (RJ11) y `open_drawer: true`. |
| PIN incorrecto |Verifica mayúsculas/teclado numérico. Tras varios fallos queda en la bitácora. |
| Corte con diferencia | No cierres a la fuerza: revisa el efectivo y anota el motivo. La diferencia queda registrada. |
| Se fue la luz a media venta | Al reiniciar, la venta sólo existe si se confirmó el cobro. El carrito se recupera del navegador si el turno sigue abierto. |

---

## 13. Estado técnico

- Python estándar + SQLite + navegador. Sin internet, sin dependencias externas.
- Escucha solo en `127.0.0.1` (8090, o el siguiente puerto libre).
- Contraseñas de usuarios cifradas; sesiones con expiración.
- Base en modo WAL con `synchronous=FULL` (prioriza no perder ventas).
- Las ventas se validan **en el servidor** contra el inventario: el navegador
  no puede inventar precios ni productos.
- Inventario propio: **7,001 productos** (6,999 activos) al 18/11/2026.

### Pruebas realizadas (16/11/2026)

Alta de usuarios y los tres roles · login · turno · escaneo exacto · búsqueda
por nombre · código duplicado · producto bloqueado · stock 0 · cobro con cambio ·
**la venta descuenta existencias y deja movimiento con usuario/fecha/hora** ·
ajuste de existencias (+5 y −3) · el cajero **no** puede ajustar stock · se
exige motivo · alta de producto con SKU propio `M-00001` · edición de producto ·
baja de producto (deja de cobrarse) · devolución parcial repone existencias ·
rechazo de PIN inválido · máximo de 8 usuarios · desactivar usuario (cierra su
sesión) · restablecer PIN de otro usuario · corte sin diferencia ($0) ·
exportación CSV · ticket imprimible · respaldo automático · bitácora completa.

- El catálogo se corrigió de codificación: acentos correctos
  (`Papelería y Escolares`, `Electrónica`, `Plásticos`…).

---

## 14. Estado del proyecto

1. **Hecho**: catálogo limpio migrado, caja operativa, inventario propio,
   ventas, devoluciones, movimientos de existencias, usuarios y roles, cortes,
   respaldos, exportación, **impresora térmica configurada** (POS-80 por USB,
   80 mm, sin cajón) y conteo de denominaciones al cierre.
2. **Pendiente de ti**: decidir si más adelante se conecta un **cajón de dinero**
   a la impresora (`open_drawer: true`) y si se quieres un **historial de cortes**
   consultable dentro de la app (hoy el corte se imprime y queda copiado en
   `tickets/AAAA-MM-DD/`).
3. **Corte de `1box`**: cuando se decida, se deja de correr el ETL y el POS
   queda como único sistema. El histórico de `1box` ya está mapeado por
   `legacy_item_id` para cuando quieras consultarlo.

---

*Fase 0 · 16/11/2026*
