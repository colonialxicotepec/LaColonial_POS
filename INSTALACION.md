# Instalación del POS La Colonial en una computadora Windows 11

Guía paso a paso para instalar la caja en un equipo **nuevo**, con
**instalación limpia**, **sin internet** y **sin instalar nada en Windows**
(el paquete trae su propio Python portable).

> **Tiempo estimado:** 10 minutos. **No hace falta** ser técnico: todo se hace
> con doble clic.

---

## 0. Qué necesitas

| Requisito | Detalle |
|---|---|
| Sistema | Windows 11 (64 bits) o Windows 10 |
| Disco libre | 400 MB (el paquete) + 1 GB de margen para respaldos |
| Memoria | 4 GB o más (recomendado 8 GB) |
| Privilegios | **Ninguno**: no hace falta ser administrador |
| Internet | **No hace falta** para operar la caja |
| Red | **No hace falta**: la caja sólo escucha en `127.0.0.1` (esta computadora) |
| Extra | La base con el inventario **ya viene migrada** en el paquete |

Antes de empezar, ten a la mano: los **PIN** del administrador y del cajero, y una
**contraseña del administrador de la PC** por si Windows pregunta por permisos
de firewall (puedes denegarlo, la caja no usa la red).

---

## 0-bis. ¿Esta PC es el servidor o un cliente?

Antes de instalar, decide el rol de esta computadora:

| Rol | Qué es | Archivos clave | Cómo se usa |
|---|---|---|---|
| **Servidor (caja central)** | La PC que tiene la base de datos y atiende a toda la tienda | `caja.bat`, `caja_en_red.bat`, `instalar_inicio_servidor.bat`, `pos.db` | Se abre con `caja.bat` (local) o `caja_en_red.bat` (red) |
| **Cliente (PC de cajero)** | PC adicional que cobra e imprime, pero **no** tiene base propia | `instalar_cajero.bat`, `impresora_local.py`, `impresora_pc.json` | Se abre el acceso directo "La Colonial POS" del Escritorio (proxy local `:8099`) |

- Sólo **una** PC es el servidor. Las demás son clientes.
- El servidor debe estar encendido y con la caja abierta para que los clientes puedan cobrar.
- Los clientes necesitan el **agente de impresora USB** corriendo (se registra automáticamente con `instalar_cajero.bat`).

### Instalación del servidor (PC central)

Sigue las secciones 1 a 5 tal cual. Al final, si quieres que el servidor arranque solo al encender la PC:

```
instalar_inicio_servidor.bat
```

Esto registra `caja_en_red.bat` en el Inicio de Windows para que al encender la PC se abra el POS en red automáticamente.

### Instalación del cliente (PC de cajero)

1. Copia la misma carpeta del proyecto a la PC cliente (o descomprime el ZIP).
2. Ejecuta `instalar_python_portable.bat` (si falta `runtime\`).
3. Ejecuta **`instalar_cajero.bat`** una sola vez. Te pedirá el nombre o IP del servidor (Enter = `SERVIDOR.local`). Crea:
   - `impresora_pc.json` con la URL del servidor.
   - Un acceso directo en Inicio de Windows para el agente de impresora USB (invisible).
   - Un acceso directo "La Colonial POS" en el Escritorio.
4. El agente de impresora arranca automáticamente; la PC cliente ya puede cobrar desde el Escritorio (no necesita ejecutar ningún `.bat`).
5. Para verificar: abre el acceso directo y entra con tu PIN. Si carga el POS, todo bien.

> `verificar_instalacion.bat` **sólo sirve en el servidor** (comprueba `pos.db` y `pos_config.json`). En un cliente no aplica.

---

## 1. Descomprime el paquete

1. Copia el archivo `LaColonial_POS_AAAAMMDD_HHMMSS.zip` a la computadora nueva.
2. Descomprímelo en una carpeta **sencilla y corta**, por ejemplo:

   ```
   C:\LaColonial
   ```

   ⚠️ **Evita** comprimirlo dentro del escritorio de OneDrive, de la carpeta
   `Descargas` sincronizada o dentro de `Program Files`: OneDrive puede
   bloquear los archivos mientras los guarda y `Program Files` exige permisos
   de administrador.

3. Debe quedar así:

   ```
   C:\LaColonial\
      caja.bat                     <- abrir la caja
      caja_en_red.bat              <- abrir la caja para otros equipos de la red
      abrir_puerto_firewall.bat    <- abrir el puerto en Windows (admin)
      verificar_instalacion.bat    <- revisar que todo esté bien
      instalar_python_portable.bat<- sólo si falta runtime\
      pos.db                       <- la base con el inventario migrado
      Historico\Auxiliares\        <- acta de cierre y archivos legados
      runtime\                     <- Python portable (lo crea el instalador)
      ui\  Historico\  ...
   ```

---

## 2. Instala el Python portable

**Doble clic en `instalar_python_portable.bat`.**

Qué hace: descomprime dentro de la carpeta `runtime\` un Python 3.12 que viaja
con el proyecto. **No** se registra en Windows, **no** modifica el `PATH`, **no**
pide permisos de administrador y **no** toca el Python que ya tengas instalado.

Debes ver:

```
Extrayendo vendor\python-3.12.10-embed-amd64.zip ...
Configurando rutas del runtime ...
Python portable OK
```

Si ya existe `runtime\python.exe`, te avisa y no hace nada (puedes repetirlo
sin miedo).

---

## 3. Verifica la instalación

**Doble clic en `verificar_instalacion.bat`.**

Debe verse todo en `OK`:

```
[OK   ] Python 3.9 o superior — 3.12.10 — portable
[OK   ] Archivos del proyecto completos — 13 archivos
[OK   ] Base de datos íntegra (SQLite) — ok
[OK   ] Tablas completas — 20 tablas
[OK   ] Inventario cargado — 7,001 productos (6,999 activos)
[OK   ] Usuarios configurados — 1 usuario(s) activo(s)
[OK   ] Permiso de escritura en la carpeta
[OK   ] Espacio en disco — 30.2 GB libres
[AVISO] Servidor de caja activo — no está corriendo (es normal...)
[OK   ] Impresoras detectadas — \\Caja\CAJA
[OK   ] Inventario congelado — congelado
```

Qué significan los avisos:

| Aviso | Qué hacer |
|---|---|
| “Servidor de caja activo” | Normal: todavía no abriste la caja. |
| “Usuarios configurados: 0” | Normal si aún no creas los PIN. Si ya habías configurado usuarios y aparece 0, **no borres `pos.db`**: revisa la sección 9. |
| “Impresoras detectadas: ninguna” | Normal: la impresión local está pendiente de definirse (sección 7). |

Al final deja un reporte en `Historico\Auxiliares\migracion\verificacion_instalacion.json`.

---

## 4. Primer arranque

**Doble clic en `caja.bat`.**

1. Se abre el navegador en `http://127.0.0.1:8090/pos`.
2. La primera pantalla pide configurar la caja:
   - **Nombre y PIN del administrador** (4 a 8 dígitos).
   - **Nombre y PIN del cajero**.
3. Deja la **ventana negra abierta**: si la cierras, la caja se apaga.

### Prueba de humo (hazla una vez)

1. Entra con el PIN del cajero.
2. “Abrir turno” → fondo inicial `500` → **Abrir caja**.
3. Escanea cualquier producto (o búscalo por nombre) → `Enter` → **COBRAR**.
4. Elige **Efectivo**, escribe lo recibido, `Enter`.
5. Debe mostrar el **cambio** en grande y el ticket en pantalla.
6. `F9` → **Ventas de hoy**: debe listar tu venta.
7. **Corte de caja** → escribe el efectivo contado → **Cerrar turno**.
8. Si el corte dice “sin diferencia”, todo bien.

---

## 5. Configura los datos de la tienda

Abre `pos_config.json` con el Bloc de notas y edita:

```json
"store": {
  "name": "La Colonial",
  "address": "Calle … #…, Colonia …",
  "phone": "55 1234 5678",
  "rfc": "XXXXXXXXXXXXX",
  "footer": "¡Gracias por su compra!",
  "show_address": true
}
```

Estos datos salen en el ticket. Deja la coma al final de cada línea y las
comillas: si el archivo queda con una coma de más, el sistema avisa y usa los
valores por omisión.

> Para cambiar el ancho del ticket usa `"width": 42` (papel de 80 mm) o `32`
> (papel de 58 mm).

---

## 6. Respaldos (esto es importante)

El sistema respalda solo y **verifica cada respaldo** antes de darlo por bueno:

| Cuándo | Dónde |
|---|---|
| Al abrir la caja | `backups\pos_AAAAMMDD_HHMMSS_arranque.db` |
| Cada **4 horas** si hubo ventas nuevas y hay turno abierto | `backups\pos_AAAAMMDD_HHMMSS_auto.db` |
| Al cerrar turno | `backups\pos_AAAAMMDD_HHMMSS_corte.db` |
| A mano (Menú → Respaldar) | `backups\pos_AAAAMMDD_HHMMSS_manual.db` |

Se conservan los **últimos 168 respaldos automáticos** (combinados: arranque,
cada 4 horas y corte). Los respaldos **manuales nunca se borran solos**. Copia la
carpeta `backups\` a una USB o a la nube **una vez por semana** (o al cierre del
día).

### Restaurar un respaldo

1. Cierra la caja (cierra la ventana negra).
2. Lista y verifica los respaldos disponibles:

   ```
   runtime\python.exe restaurar_respaldo.py --listar
   ```

3. Restaura el que elijas (pide confirmación y comprueba que la caja esté cerrada):

   ```
   runtime\python.exe restaurar_respaldo.py --restaurar backups\pos_....db --confirmar
   ```

El script crea un respaldo de seguridad `pos_..._antes_restaurar.db` y sólo
reemplaza `pos.db` si el respaldo pasa la verificación. Después abre `caja.bat`
y confirma con `verificar_instalacion.bat`.

---

## 7. Impresión

La impresión se hace **en la PC que tiene la impresora USB** (normalmente el
servidor, o cada cliente si tiene su propia impresora). El flujo es:

- **Servidor**: genera los bytes ESC/POS (`/api/tickets/print` y `/api/shifts/close`)
  y los envía al cliente que los solicita.
- **Cliente**: recibe los bytes y los manda a la impresora USB vía el agente
  `impresora_local.py` (endpoint `/imprimir-raw`). Si el agente no está
  corriendo, el ticket se muestra en pantalla como respaldo.

### Configuración (`pos_config.json`)

| Dato | Dónde se configura |
|---|---|
| Nombre de la impresora | `pos_config.json` → `"printer": {"name": "\\\\Caja\\CAJA"}` |
| Activar impresión de tickets | `"print_sale": true` |
| Activar impresión del corte | `"print_shift": true` |
| Abrir el cajón al cobrar | `"open_drawer": true` |
| Ancho del papel | `"width": 42` (80 mm) o `32` (58 mm) |

En el cliente, el agente usa `impresora_pc.json` (creado por `instalar_cajero.bat`)
con `server_url` apuntando al servidor y `printer_name` con el nombre de la
impresora USB local.

### Probar la impresión

**Menú → Probar impresora** (manda un ticket de prueba). Si el ticket sale
cortado, ajusta `"width"`.

Si el agente del cliente no está corriendo, reinícialo:

```
runtime\pythonw.exe impresora_local.py
```

O vuelve a ejecutar `instalar_cajero.bat`.

---

## 7-bis. Entrar desde otro equipo de la red local

Por omisión la caja **sólo responde en esta computadora** (`127.0.0.1`): es lo
seguro para una caja con dinero. Si necesitas entrar desde otra tablet,
computadora o celular **de la misma red local** (por ejemplo, una segunda
pantalla o un monitor de pedido):

**Opción A — sin cambiar nada (recomendada para probar)**

1. Cierra `caja.bat` si está abierto.
2. Doble clic en **`C:\LaColonial\caja_en_red.bat`**.
3. En la ventana negra aparece la dirección, por ejemplo:
   `http://192.168.0.104:8090/pos`
4. En el otro equipo, abre esa dirección en el navegador e entra con tu PIN.

**Opción B — dejar la caja siempre accesible en la red**

Edita `C:\LaColonial\pos_config.json` y pon:

```json
"network": {
  "allow_lan": true,
  "allowed_ips": []
}
```

- `allowed_ips: []` = cualquiera en la red local puede entrar (con PIN).
- `allowed_ips: ["192.168.0.55"]` = sólo ese equipo (IP fija del equipo que
  cobra). Es lo recomendado si la red es de oficina.

**Si el otro equipo no entra:**

| Causa | Solución |
|---|---|
| Firewall de Windows | Doble clic en `C:\LaColonial\abrir_puerto_firewall.bat` **como administrador**. (En una red "Privada", Windows ya suele permitir `runtime\python.exe`.) |
| La red aísla equipos (Wi-Fi de tienda) | Configura el router para permitir comunicación cliente a cliente. |
| La caja quedó en modo local | Debe estar abierta con `caja_en_red.bat` (o con `allow_lan: true`). |
| Dirección equivocada | La IP se muestra en la ventana negra de la caja. También sirve `ipconfig`. |

⚠️ **Seguridad:** con la caja en red, cualquiera que llegue a esa dirección
puede intentar entrar. Por eso cada acceso pide **PIN** y el PIN se puede
cambiar o desactivar en **Menú → Usuarios**. Si la red de la tienda es de
gente no confiable, usa `allowed_ips` con la IP del equipo que sí debe cobrar.

---

## 8. Operación diaria (resumen)
| Acción | Cómo |
|---|---|
| Abrir la caja | Doble clic en `caja.bat` (déjala abierta) |
| Entrar | PIN de 4 a 8 dígitos |
| Abrir turno | Fondo inicial (efectivo con el que empiezas) |
| Cobrar | Escanear → `Enter` → `F4` → forma de pago → `Enter` |
| Devolver | `F9` → **Devolver** → motivo + PIN de gerente o administrador |
| Cancelar venta | `F9` → **Cancelar** → motivo + PIN de gerente o administrador |
| Mover inventario | `F10` → Inventario → cantidad + motivo → **+ Alta** / **− Baja** |
| Ver el día | `F9` → Ventas de hoy |
| Exportar el día | `F9` → **Exportar CSV** (para contabilidad) |
| Cerrar turno | **Corte de caja** → efectivo contado → cerrar |
| Salir | Menú → Cerrar sesión, y cierra la ventana negra |

Atajos: `F2` buscar · `F4` cobrar · `F8` vaciar carrito · `F9` ventas del día ·
`F10` inventario · `Esc` cerrar.

El detalle está en **`MANUAL_RAPIDO.md`** (una página, para el personal).

---

## 9. Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| “No se encontró Python” al abrir `caja.bat` | Falta el runtime: ejecuta `instalar_python_portable.bat`. |
| “No se pudo abrir ningún puerto local” | Ya hay otra caja corriendo: cierra la otra ventana negra y vuelve a abrir. Si es otro programa, el sistema usa el siguiente puerto (8091, 8092…) y abre el navegador ahí. |
| Windows pregunta por el firewall | **Deniega**: la caja sólo usa `127.0.0.1`. No hace falta abrir puertos. |
| El navegador no se abre solo | Abre `http://127.0.0.1:8090/pos` a mano. |
| “Sesión expirada” | La sesión dura 12 h sin uso. Vuelve a entrar con tu PIN. |
| "PIN incorrecto" | Revisa que el teclado no esté en otro idioma y que sean los dígitos correctos. Si se olvidó un PIN, **sólo el administrador** lo restablece en **Menú → Usuarios → PIN**. |
| “No se encontró el producto” | El artículo no tiene código de barras capturado: búscalo por nombre y elígelo de la lista. |
| “Ese código está en más de un producto” | Hay códigos repetidos en el catálogo: elige el correcto. |
| “Imprima… no se pudo imprimir” | La impresión está desactivada por ahora; el ticket se ve en pantalla. |
| El antivirus bloquea la carpeta | Añade la carpeta del proyecto a las exclusiones del antivirus (suele ser Windows Defender). |
| Archivos que no se guardan | La carpeta está en OneDrive o en `Program Files`: muévela a `C:\LaColonial`. |
| La hora del ticket no es correcta | Ajusta la fecha y hora de Windows (Panel de control → Fecha y hora). |
| La caja no abre el cajón | Hoy está desactivado a propósito (`open_drawer: false`). |
| Base de datos dañada | Restaura el último respaldo de `backups\` (sección 6). |

---

## 10. Si después quieres cambiar de computadora

1. En la computadora vieja: **Menú → Respaldar la caja ahora** (o cierra un
   turno, que también respalda).
2. Copia **toda la carpeta** del proyecto a la nueva (o genera un paquete
   nuevo con `empaquetar_proyecto.bat`).
3. En la nueva: `instalar_python_portable.bat` → `verificar_instalacion.bat`
   → `caja.bat`.

---

## 11. Lista de verificación final

### Servidor
- [ ] Carpeta descomprimida en `C:\LaColonial` (no en OneDrive ni Program Files)
- [ ] `instalar_python_portable.bat` terminó con “Python portable OK”
- [ ] `verificar_instalacion.bat` no muestra fallos
- [ ] `caja.bat` abre el navegador en `127.0.0.1:8090/pos`
- [ ] (Opcional) `caja_en_red.bat` muestra la dirección y se entra con el PIN
- [ ] (Opcional) `instalar_inicio_servidor.bat` registrado para arranque automático
- [ ] Se crearon el PIN del administrador y el del cajero
- [ ] Se creó el resto de los usuarios (hasta 8) en Menú → Usuarios
- [ ] `pos_config.json` tiene dirección, teléfono y RFC
- [ ] Prueba de humo: venta, cambio, corte sin diferencia
- [ ] Se programó una copia semanal de `backups\` (USB o nube)
- [ ] El equipo está en la lista de excepciones del antivirus

### Cliente
- [ ] Carpeta descomprimida en `C:\LaColonial`
- [ ] `instalar_python_portable.bat` terminó con “Python portable OK”
- [ ] `instalar_cajero.bat` ejecutado y `impresora_pc.json` creado
- [ ] El agente de impresora arranca solo (acceso directo en Inicio de Windows)
- [ ] El acceso directo "La Colonial POS" del Escritorio abre el POS
- [ ] Se puede entrar con el PIN y cobrar
- [ ] La impresora USB imprime ticket y corte (Menú → Probar impresora)
- [ ] El equipo está en la lista de excepciones del antivirus

---

*Documento generado el 16/11/2026 · POS La Colonial · Fase 1*
