# Manual rápido — Caja La Colonial

Una página. Si esto es lo único que necesitas leer, es suficiente para cobrar.

---

## Antes de cobrar

1. Doble clic en **`caja.bat`** y **déjalo abierto** (si lo cierras, la caja se apaga).
2. Escribe tu **PIN** y presiona **Entrar**.
3. **Abrir turno** → cuenta el efectivo con el que empiezas → escribe el
   **fondo inicial** → **Abrir caja**.

> Si la tienda pide cobrar desde otra pantalla o tablet, el encargado abre
> `caja_en_red.bat` y te pasa la dirección (algo como
> `http://192.168.0.104:8090/pos`). Tú entras igual, con tu PIN.
>
> **En cada PC que cobra:** deja corriendo **`impresora.bat`** para que el
> ticket salga en **la impresora de esa misma PC** (y no en la de la caja
> central). Arriba a la derecha debe verse **🖨 POS-80**; si ves
> **🖨 sin impresora local**, arranca `impresora.bat`.

---

## Modo Mostrador (Vendedores en rol de cajero)

Si estás en una PC o tablet de mostrador (con rol de **Cajero**):
- El botón verde dice **DESPACHAR (F4)** en lugar de COBRAR.
- Al presionar **F4**, la venta se registra de inmediato, descuenta existencias y genera el ticket con tu nombre (`Vendedor: [Tu Nombre]`).
- **No capturas dinero ni calculas cambio:** entrega el ticket impreso al cliente indicándole que pase a liquidar a la **Caja central**.
- La terminal central de **Caja** es la única que tiene cajón de dinero, abre el turno por la mañana y realiza el corte de caja al final del día.

---

## Cobrar (Terminal central de Caja)

1. **Escanea** el producto (el lector escribe el código y da Enter solo).
   Si el producto **no tiene código**, escribe parte del nombre y elígelo de
   la lista con el mouse. Si el producto tiene **pieza y paquete**, al escanear
   aparecen ambos: elige cuál con `↑` `↓` (o con el mouse) y pulsa **Enter**.
2. Para cobrar otro más del mismo: escánalo otra vez.
3. Cantidades: usa los botones **−** y **+** de cada línea (suman o restan 1).
   Para vender por peso, haz **clic en la cantidad** y escribe el decimal
   (por ejemplo `0.75`); la unidad (`kg`, `gr`, `pza`, `paq`, `cja`) aparece
   junto a la cantidad. Para quitar la línea: la **✕**.
4. Presiona **F4** (o el botón **COBRAR**).
5. Elige cómo paga:
   - **Efectivo**: escribe lo que te dieron (o usa los botones de $50, $100…).
     La pantalla te dice el **cambio**.
   - **Tarjeta / Transferencia**: escribe la referencia si te la piden.
6. **Enter** para cobrar. Verás el cambio en grande.
7. **Enter** otra vez para el siguiente cliente.

> 🖨 **El ticket sale solo** por la impresora térmica (POS-80, papel de 80 mm),
> con el **TOTAL enorme** (letras dibujadas a 6× de grosor) y el papel cortado.
> Si la impresora falla, **la venta NO se pierde**: queda guardada y el ticket
> se puede reimprimir con **F9 → Reimprimir** (o abrirlo en el navegador con
> **Ver ticket**).

### Atajos

| Tecla | Qué hace |
|---|---|
| `F2` | Volver al buscador |
| `Enter` | Agregar el producto / confirmar el cobro |
| `↑` `↓` | Elegir de la lista |
| `F4` | Cobrar (o despachar, si eres vendedor) |
| `F6` | Tickets despachados pendientes de cobro (Caja) |
| `F7` | Entradas y salidas de efectivo (Caja) |
| `F8` | Vaciar el carrito |
| `F9` | Ventas del día |
| `F10` | Inventario (sólo administrador o gerente) |
| `Esc` | Cerrar la ventana |

---

## Si el cliente devuelve algo

**F9** → botón **Devolver** (o escribe el folio del ticket y **Buscar**) →
marca las líneas → **motivo** → **PIN del gerente o administrador** →
**Confirmar devolución**.

Se imprime un comprobante y el dinero sale de la caja (queda registrado en el
corte). Si el turno original ya se cerró, el reintegro se carga al turno abierto;
sin turno abierto no se puede devolver en efectivo. Si te equivocaste al
devolver, en la misma lista la **anulas**. Si después cancelas la venta, la
devolución ya queda incluida en la cancelación y no se puede anular por separado.

---

## Si te equivocaste al cobrar

- **Antes de cobrar**: quita la línea con la **✕** o vacía el carrito con `F8`.
- **Ya cobraste**: **F9** → **Cancelar** → motivo + **PIN del gerente o administrador**.
  Si la venta ya tenía una devolución parcial, sólo se repone lo que falta y se
  devuelve el efectivo pendiente.

---

## Tickets despachados por vendedores (por cobrar)

Los vendedores en modo mostrador no reciben dinero: al pulsar **DESPACHAR (F4)**
el ticket queda **pendiente de cobro** y el inventario se descuenta de una vez.

- En la **Caja central**, el botón cambia a **COBRAR PENDIENTES (N) F4** y también
  hay un botón **Por cobrar** con contador (atajo **F6**).
- **Cobrar** abre la ventana de pago con el folio, el vendedor y los artículos.
  Hasta ese momento el efectivo **no** se suma al turno.
- Si el cliente no paga, **Cancelar** devuelve el inventario y no mueve efectivo.
- La pantalla de Caja revisa nuevos despachos cada pocos segundos.

---

## Entradas y salidas de efectivo (Caja)

**F7** o **Entradas / Salidas** registra dinero ajeno a las ventas:

- **+ Entrada de efectivo**: cambio adicional, aportación, fondo extra.
- **− Salida / Gasto**: papelería, garrafón, pago a proveedor, retiro parcial.

Cada movimiento pide **monto** y **concepto**, y se refleja de inmediato en el
**efectivo esperado** del turno y en el corte.

### Corregir un error de captura (gerente o administrador)

En la lista **Movimientos del turno actual** cada renglón tiene **Editar** y
**Eliminar**:

- **Editar** carga el movimiento en el formulario. Puedes cambiar monto,
  concepto e incluso convertir una entrada en salida (o al revés). Guarda con
  **Guardar cambios**; **Cancelar edición** deja todo como estaba.
- **Eliminar** pide confirmación y borra el movimiento.
- Sólo se pueden corregir movimientos del **turno abierto**. Las ventas,
  devoluciones y cancelaciones no se tocan desde aquí: usa **F9**.
- Cada edición o eliminación queda en la **bitácora** con el nombre de quien la
  hizo, el valor anterior y el nuevo.

---

## Cerrar el día

1. **Corte de caja** (abajo a la derecha).
2. **Cuenta por denominación**: escribe cuántas piezas hay de cada moneda y
   billete. La pantalla suma el total y, si sobra efectivo, sugiere cuánto
   retirar en billetes para dejar el **fondo máximo** en caja (por omisión
   $1,400; se ajusta con `rules.fondo_maximo` en `pos_config.json`).
3. **🖨 Imprimir conteo** saca una hoja con el desglose (opcional).
4. **Continuar al cierre →**. El total contado se pasa solo al campo de
   efectivo (ese campo **no se puede escribir**: si el monto no cuadra,
   vuelve al paso 1 y corrige las piezas; la caja no acepta un corte
   descuadrado).
5. La pantalla te dice el **efectivo esperado** y la **diferencia**.
6. Si hay diferencia, repítela antes de cerrar.
7. Si quedaron **tickets por cobrar**, la pantalla te avisa: no son efectivo
   de este turno, se cobran en el siguiente.
8. Puedes escribir una **nota de cierre** (opcional) para explicar una
   diferencia o un faltante; sale impresa en el corte.
9. **Cerrar turno**. El corte incluye el **desglose por denominación**, el
   **retiro sugerido**, las devoluciones, las entradas/salidas de efectivo, los
   pendientes de cobro y tus notas; se guarda el corte y se hace un respaldo.
   El corte **sale impreso** en la térmica; si la impresora falla, se abre en
   una ventana del navegador para imprimirlo o consultarlo.

> El retiro sugerido es solo una recomendación: **no** descuenta dinero del
> turno ni genera un movimiento. Si retiras efectivo, regístralo antes en
> **Entradas / Salidas (F7)** como salida.

---

## Inventario (administrador o gerente)

**F10** → busca el producto →

- **+ Alta / − Baja**: suma o resta existencias. El motivo viene en **`Error de Conteo`**
  por defecto y se elige de la lista desplegable (`Error de Conteo`, `Compra`,
  `Caducidad`…), con botón **Administrar motivos…** para agregar o quitar.
  Queda registrado con tu nombre, la fecha y la hora.
- **Guardar cambios**: corrige nombre, código, categoría, ubicación, precio o costo
  (unidad, categoría y ubicación son listas desplegables administrables).
- **Dar de baja**: el producto deja de venderse (no se borra, se puede reactivar).
- **Nuevo producto**: alta de un artículo que no existe en el catálogo (el SKU
  viene precargado con el siguiente libre, categoría en `Papelería` y ubicación
  en `fila 1 sección 1`; todos se pueden cambiar).
- **Empaque**: en el producto, sección “Empaque de otro producto” → elige el
  suelto y las unidades por empaque. Vender 1 empaque descuenta esas unidades
  del suelto (ej. paquete de 10 → descuenta 10 piezas).

> La **importación del catálogo** (`Historico/Auxiliares/inventario_moderno.db` →
> `pos.db`) no se hace desde esta pantalla: se hace con **`Historico/Auxiliares/importar_inventario.bat`**
> en la computadora de la caja (sólo el administrador). Deja el stock local como
> está y refresca nombres y precios; los precios nuevos aplican en la siguiente
> venta.

### Stock negativo

Si un producto aparece en **rojo** con **“Stock negativo”**, se puede vender
igual: el sistema deja el saldo negativo a propósito. El aviso **Avisos** de la
barra superior lo recuerda hasta que el inventario se corrija con un alta, una
devolución o un ajuste. No se descarta a mano: se cierra solo cuando el stock
vuelve a cero o más.

---

## Si algo falla

| Mensaje | Qué hacer |
|---|---|
| “Sesión expirada” | Vuelve a entrar con tu PIN. |
| “Ese código está en más de un producto” | Elige el correcto de la lista. |
| “Producto sin existencias” | Se puede vender igual: revisa con el gerente. El stock negativo se marca en rojo y deja aviso. |
| “El pago es insuficiente” | Escribe el efectivo recibido completo. |
| “No se encontró el producto” | Búscalo por nombre. |
| El ticket no sale | **Menú → Probar impresora**. Revisa que esté encendida y con papel; si sigue igual, la venta ya quedó guardada: reimprímela con **F9 → Reimprimir**. |
| El cajón no abre | No hay cajón conectado: el efectivo se cuenta a mano en el corte de caja. |
| La caja se cerró | Vuelve a abrir `caja.bat`. |

**No borres nada** de la carpeta del proyecto y **no cierres la caja** con una
venta a medias. Ante una duda, anótala y consulta antes de tocar los datos.

---
*Manual rápido · POS La Colonial · 24/11/2026*
