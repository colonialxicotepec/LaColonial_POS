# Histórico 1box — consulta del inventario viejo

Interfaz de **solo consulta** para buscar productos en `historico_1box.db`
(la copia del sistema anterior). No modifica nada: ni el histórico, ni el
inventario del POS (`pos.db`).

Se puede usar:

- **sólo en esta computadora** (por omisión), o
- **publicada en la red local**, para que los cajeros consulten desde otros
  equipos durante la migración.

---

## Cómo abrirla (sólo esta computadora)

Doble clic en:

```text
D:\LaColonial_POS\Historico\consultar_historico.bat
```

Se abre el navegador en `http://127.0.0.1:8095/`. La ventana negra debe quedar
abierta; al cerrarla se detiene la consulta.

Si el puerto configurado está ocupado, usa el siguiente libre (8096, 8097…).
Si ya hay una consulta abierta, el lanzador reutiliza esa ventana en vez de
abrir una segunda.

---

## Publicarla en la red local (para los cajeros)

### Opción A — un clic (recomendada)

> La consulta ya viene **publicada en la red** (`historico_config.json` tiene
> `"publicar_en_red": true`): al abrirla con cualquiera de los lanzadores, los
> otros equipos entran con `http://IP-DE-ESTA-PC:8095/`. El historico se carga
> **una sola vez en la memoria del servidor**: los equipos de la red consultan
> esa misma copia en lugar de abrir la base cada vez.

Doble clic en:

```text
D:\LaColonial_POS\Historico\historico_en_red.bat
```

La ventana negra muestra la dirección, por ejemplo:

```text
Red local : http://192.168.0.104:8095/
```

Esa dirección se escribe en el navegador de la computadora o tablet del cajero
(debe estar en la **misma red local**). Se pueden conectar varios equipos a la
vez. La etiqueta **RED LOCAL** aparece en la interfaz cuando está publicada.

### Opción B — dejarla publicada siempre

Edita `historico_config.json`:

```json
{
  "puerto": 8095,
  "publicar_en_red": true,
  "ips_permitidas": []
}
```

Desde la próxima vez, `consultar_historico.bat` arranca ya publicada.

### Opción C — desde la terminal

```text
consultar_historico.bat --red
consultar_historico.bat --red --puerto 8100
```

### Si otro equipo no entra: firewall

Windows puede bloquear el puerto. Ejecuta **como administrador**:

```text
D:\LaColonial_POS\Historico\abrir_puerto_historico.bat
```

Abre el puerto 8095 sólo para redes **privadas**. Si usas otro puerto:

```text
abrir_puerto_historico.bat 8100
```

Comprueba también que el otro equipo esté en la misma red y que la IP sea la
correcta (la ventana negra la muestra al arrancar).

> **Seguridad:** esta interfaz no pide PIN porque es de sólo lectura sobre una
> copia histórica. Cualquiera en la red local puede consultarla si se publica.
> Si quieres limitarla a ciertos equipos, usa `ips_permitidas` (ver abajo).

---

## Puerto y configuración

Archivo: `Historico\historico_config.json`

```json
{
  "puerto": 8095,
  "publicar_en_red": true,
  "ips_permitidas": []
}
```

| Clave | Qué hace |
|---|---|
| `puerto` | Puerto de la interfaz. Por omisión **8095**. Si está ocupado, se prueba 8096, 8097… |
| `publicar_en_red` | `true` = escucha en la red local; `false` = sólo esta computadora. |
| `ips_permitidas` | Lista de IPs autorizadas, por ejemplo `["192.168.0.55", "192.168.0.60"]`. Vacía = cualquiera de la red local. |

Los argumentos de la línea de comandos tienen prioridad sobre el archivo:

| Argumento | Efecto |
|---|---|
| `--puerto N` | Fuerza el puerto (ejemplo: `--puerto 8100`). |
| `--red` / `--lan` | Publica en la red local. |
| `--local` | Fuerza sólo esta computadora (aunque el config diga `true`). |
| `--ips IP1,IP2` | Restringe el acceso a esas IPs. |
| `--no-browser` | No abre el navegador (útil para pruebas o accesos directos). |

---

## Qué se puede buscar

| Forma | Ejemplo | Resultado |
|---|---|---|
| Código de barras (escaneado) | `7501015223369` | Ficha exacta del producto |
| SKU de 1box | `91930` | Ficha exacta |
| Código adicional / UPC/EAN/ISBN | `071641212749` | Ficha exacta |
| Código de variación | `7192716220407` | Ficha del producto con sus variaciones |
| Nombre (sin acentos ni mayúsculas) | `cuaderno college` | Lista de coincidencias |
| Categoría | `papeleria` | Lista por categoría |

- Se incluyen **productos eliminados e inactivos** (marcados con etiqueta),
  porque el objetivo es consultar el inventario viejo completo.
  La casilla **“Incluir productos eliminados”** permite ocultarlos.
- Si un código pertenece a varios productos, la ficha muestra una lista para
  elegir el correcto.
- Funciona con cualquier **lector USB en modo teclado** (escribe el código y
  envía Enter). El campo de búsqueda siempre queda listo para el siguiente
  escaneo.

### Atajos

| Tecla | Acción |
|---|---|
| Escanear + Enter | Consulta inmediata del código |
| Escribir | Búsqueda por nombre en vivo |
| **F2** | Enfoca el buscador |
| **Esc** | Limpia la consulta |

---

## Clientes y proveedores

La pantalla tiene **dos pestañas**: **Productos** (el inventario viejo) y
**Clientes y proveedores**. La segunda muestra los terceros que quedaron
registrados en 1box, tal como estaban, para revisar contactos, cuentas y
saldos: esa base se capturaba a mano y tiene muchos errores de captura.

| Tipo | Registros | De dónde sale |
|---|---|---|
| **Cliente** | 403 | `customers` + `people` |
| **Proveedor** | 123 | `suppliers` + `people` |
| **Marca** | 212 | `manufacturers` |
| **Persona** | 18 | `people` sueltas (empleados y contactos) |

- Se busca por **nombre, teléfono, correo o número de cuenta** (sin depender de
  cómo se capturó el dato).
- El desplegable filtra por tipo; la casilla **“Incluir eliminados”** muestra
  también los que estaban dados de baja en 1box.
- La ficha de cada tercero muestra: nombre, razón social, **cuenta**, RFC,
  teléfono, correo y dirección; **saldo**, límite de crédito y puntos; número
  de operaciones, importe acumulado y última operación.
- **Cliente**: últimas operaciones (fecha, folio, importe, forma de pago) y
  movimientos de la **cuenta corriente**.
- **Proveedor**: últimas recepciones (fecha, folio, artículos, importe, forma
  de pago) y los **artículos que surte**.
- Los datos delicados (notas internas de 1box, datos fiscales de los
  empleados) **no se muestran**.

> Nota: en 1box la tabla `items` no guarda qué fabricante es cada artículo, así
> que las marcas se listan como catálogo, sin artículos asociados.

---

## Qué muestra la ficha de producto

- Nombre, categoría, proveedor, SKU, ID de 1box y fecha de última modificación.
- Estado en 1box: **Activo**, **Inactivo** o **Eliminado**.
- Código de barras, SKU, códigos adicionales y variaciones (con su precio).
- Existencia histórica y ubicación en tienda tal como estaban al corte.
- Precio, costo y precio de promoción.
- Última venta, unidades vendidas e importe vendido (desde el histórico).
- Historial de precios, últimos movimientos de existencia y últimas ventas
  (se cargan al seleccionar el producto).

---

## Seguridad y alcance

- La base se abre con `mode=ro` y `PRAGMA query_only = ON`: es **imposible**
  escribir en ella desde esta interfaz.
- No toca `pos.db`: las ventas, existencias y usuarios del POS actual no se
  consultan ni se modifican aquí.
- Sin internet: sólo escucha en la red local cuando se publica.
- Si se publica, conviene usar `ips_permitidas` para limitar los equipos.
- El histórico es una copia del 16/11/2026; no se actualiza solo.

---

## Archivos

| Archivo | Descripción |
|---|---|
| `consultar_historico.py` | Servidor (sólo biblioteca estándar de Python). |
| `ui/index.html`, `ui/estilos.css`, `ui/app.js` | Interfaz de consulta (productos y terceros). |
| `consultar_historico.bat` | Lanzador local de doble clic. |
| `historico_en_red.bat` | Lanzador publicado en la red local. |
| `abrir_puerto_historico.bat` | Regla de firewall para el puerto (requiere administrador). |
| `historico_config.json` | Puerto, publicación en red e IPs permitidas. |
| `README.md` | Este documento. |

La base que se consulta es `D:\LaColonial_POS\Historico\historico_1box.db`.

---

## Requisitos

- Python 3.9 o superior. Los lanzadores usan primero el Python portable del
  proyecto (`..\runtime\python.exe`) y, si no existe, el `python` del sistema.
- Sólo biblioteca estándar: no hay que instalar nada.

---

*Histórico 1box · POS La Colonial · 17/11/2026*
