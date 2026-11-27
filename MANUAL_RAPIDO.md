# 📖 Manual Rápido de Caja · La Colonial

Este documento es tu guía de bolsillo para el día a día en el punto de venta.  
**Regla de oro:** Tu PIN es personal e intransferible. Todo lo que hagas en el sistema quedará registrado con tu nombre en la bitácora de auditoría.

---

## ⌨️ Atajos de Teclado (Para ir más rápido)
| Tecla | Acción |
| :--- | :--- |
| **F2** | Ir directamente al buscador de productos. |
| **Enter** | Agregar producto al carrito / Confirmar cobro. |
| **↑ / ↓** | Elegir un producto de la lista de búsqueda. |
| **F4** | Cobrar la venta actual (o "Despachar" si eres vendedor de piso). |
| **F6** | Ver tickets pendientes de cobro (solo Caja). |
| **F7** | Registrar Entradas o Salidas de efectivo (gastos, cambio, etc.). |
| **F8** | Vaciar el carrito actual (pide confirmación). |
| **F9** | Ver ventas del día, cancelar o devolver una venta. |
| **F10** | Abrir el panel de Inventario (solo Gerente/Administrador). |
| **Esc** | Limpiar búsqueda o cerrar una ventana emergente. |

---

## 🔄 Flujo Diario de Operación

### 1. Inicio de Turno
1. Abre el programa de la Caja (ícono en el escritorio) e ingresa tu **PIN**.
2. Si eres el primero en llegar, el sistema te pedirá **Abrir Turno**.
3. Ingresa el **Fondo Inicial** (el efectivo con el que empieza la caja, ej. $500) y confirma.

### 2. Cobrar una Venta
1. Escanea el producto o escribe su nombre y presiona **Enter**.
2. Si hay varios productos similares, usa las flechas **↑ / ↓** para elegir el correcto y presiona **Enter**.
3. Ajusta la cantidad con los botones `+` / `-` (o haz clic en el número para escribir decimales, ej. `0.75` kg).
4. Presiona **F4** o el botón **COBRAR**.
5. Selecciona la forma de pago:
   - **Efectivo:** Escribe el monto que te da el cliente. El sistema calcula el cambio automáticamente.
   - **Tarjeta / Transferencia:** Registra el monto exacto cobrado en la terminal.
6. Entrega el ticket al cliente y presiona **Enter** o "Nueva venta" para continuar.

### 3. Asociar un Cliente a la Venta
- Por defecto, las ventas son a **"PÚBLICO EN GENERAL"**.
- Si el cliente lo solicita, haz clic en **"Asignar cliente"** en el panel del carrito *antes* de cobrar.
- Busca por nombre, teléfono o RFC. Selecciónalo y continúa con el cobro normal.

### 4. Despachar (Solo Vendedores de Piso)
- Si tu rol es "Cajero/Vendedor", tu botón dirá **DESPACHAR**.
- Esto reserva el inventario e imprime un ticket marcado como *"PENDIENTE DE COBRO"*.
- **No registra dinero en la caja**. El cliente debe llevar ese ticket a Caja para pagarlo.

---

## 🔐 Operaciones que Requieren PIN de Gerente o Administrador

Estas acciones están protegidas y requieren que un superior ingrese su PIN para autorizarlas:

- **Cancelar una venta ya cobrada:** Ve a *Ventas del día (F9)*, busca el ticket, pulsa *Cancelar*, escribe el motivo y solicita el PIN. El efectivo se descontará del corte del turno actual.
- **Hacer una devolución:** Ve a *Ventas del día (F9)* → *Devolver*. Ingresa el folio o búscalo. Elige qué artículos se devuelven, el motivo, la forma de reembolso (Efectivo, Tarjeta o Transferencia) y solicita el PIN.
- **Ajustar Inventario (F10):** Dar de alta, baja o modificar existencias de un producto. Siempre se debe escribir un motivo (ej. "Merma", "Compra a proveedor").
- **Cambiar un precio temporalmente:** Solo si el catálogo tiene un error urgente. Queda registrado quién lo hizo y caduca a las 24 horas.

---

## 🏦 Cierre de Turno (Corte de Caja)

1. Presiona el botón **Corte de caja** (o ve al Menú).
2. **Paso 1 - Conteo físico:** Ingresa cuántas piezas hay de cada billete y moneda. El sistema sumará el total automáticamente.
   - *Nota:* Si el total supera el fondo máximo permitido, el sistema te sugerirá cuántos billetes retirar para dejar la caja equilibrada para el día siguiente.
3. **Paso 2 - Confirmación:** El sistema comparará tu conteo con el "Efectivo Esperado" (ventas + fondo inicial - gastos). 
4. Si hay una diferencia, puedes agregar una **Nota del cierre** (ej. "Se equivocó el cambio a las 10:00 am").
5. Confirma el cierre. Se imprimirá el reporte de corte y se generará un respaldo automático de seguridad.

---

## 🚨 Primeros Auxilios (Solución de Problemas Rápidos)

| Síntoma | Causa probable y solución rápida |
| :--- | :--- |
| **Escaneo el producto y no pasa nada** | El cursor no está en el buscador. Presiona **F2** y vuelve a escanear. |
| **"Sesión expirada"** | La caja se bloquea tras 12 horas sin uso. Simplemente vuelve a ingresar tu PIN. |
| **El ticket no sale de la impresora** | Revisa el icono 🖨 en la esquina superior derecha. Si dice *"sin impresora local"*, avisa a soporte técnico para que reinicie el servicio de impresión. **La venta ya quedó guardada**, puedes reimprimirla luego con F9. |
| **"Ya hay un turno abierto"** | Un compañero olvidó cerrar su turno. Busca a la persona que abrió la caja para que haga el corte, o solicita al Administrador que lo fuerce. |
| **El sistema dice "Producto bloqueado"** | Ese artículo es de control interno (ej. "Descuento", "Abono a cuenta") y no se puede cobrar. |
| **Aparece un aviso rojo de "Stock negativo"** | Puedes vender el producto normalmente. El aviso es solo un recordatorio para que el gerente revise el inventario físico después. |

---

## 🛡️ Reglas de Seguridad Innegociables

1. **Nunca compartas tu PIN.** Si alguien más lo usa para cancelar una venta o hacer un ajuste, el sistema registrará que fuiste **tú** quien lo hizo.
2. **No apagues ni cierres la ventana negra** del sistema. Si se cierra, la caja se apaga y se interrumpen las operaciones.
3. **No instales programas ni navegues en internet** en la computadora de la caja. Esto puede activar el antivirus y bloquear el funcionamiento del sistema.

> **¿Dudas o fallas que no estén aquí?**  
> No intentes "arreglarlo" reiniciando la computadora a la fuerza. Toma nota del mensaje de error exacto y contacta a soporte técnico.