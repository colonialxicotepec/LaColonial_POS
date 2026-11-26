# POS La Colonial

Punto de venta (POS) **propio, local y autónomo** para la tienda **La Colonial**.
El inventario, los precios, las existencias y las ventas viven dentro del POS
(`pos.db`, SQLite): la caja **no depende de ningún servidor externo** para
funcionar.

![Python](https://img.shields.io/badge/python-3.12-blue.svg)
![Plataforma](https://img.shields.io/badge/Windows-10%2F11-0078D6?logo=windows)
![Licencia](https://img.shields.io/badge/licencia-MIT-yellow.svg)
![Base de datos](https://img.shields.io/badge/datos-SQLite-003B57.svg)

---

## Características

| Módulo | Qué hace |
|---|---|
| **Caja (cobro)** | Catálogo con búsqueda, escaneo, carrito, cobros mixtos, devoluciones y cierre de turno. |
| **Inventario** | Altas y bajas de productos, entradas/salidas de existencias y auditoría de movimientos con usuario y hora. |
| **Impresión** | Tickets térmicos de 80 mm (POS-80) con el total en doble tamaño y corte de papel; también imprime el corte de caja. |
| **Corte de caja** | Turnos por cajero, arqueo por denominación, entradas/salidas de efectivo y reportes de ventas. |
| **Usuarios y roles** | Administrador, gerente y cajero con PIN; hasta 8 usuarios activos. |
| **Respaldo automático** | Copia de la base cada 60 minutos con retención configurable. |
| **Catálogo** | Importación desde CSV/Excel, categorías, ubicaciones y precios de emergencia. |
| **Módulo histórico (ETL)** | Sincronización y saneamiento del inventario que venía del sistema anterior (MySQL `1box`). |

Todo corre en **Windows 10/11** con **Python 3.12** y **sin dependencias
externas**: sólo la biblioteca estándar (`http.server`, `sqlite3`, `wintypes`).

---

## Requisitos

- Windows 10 u 11 (64 bits).
- Python 3.12 (el proyecto trae un instalador de Python portable: `instalar_python_portable.bat`).
- ~400 MB de disco y 4 GB de RAM (recomendado 8 GB).
- Sin internet y sin privilegios de administrador para operar la caja.

## Instalación

1. Descarga o clona el repositorio:

   ```powershell
   git clone https://github.com/colonialxicotepec/LaColonial_POS.git
   ```

2. Copia la configuración de ejemplo y edítala con los datos de tu tienda:

   ```powershell
   copy pos_config.example.json pos_config.json
   ```

3. Arranca la caja:

   ```powershell
   caja.bat
   ```

4. Abre <http://127.0.0.1:8090/pos> e inicia sesión con tu PIN.

La base `pos.db` se crea sola con su esquema la primera vez. Para cargar el
catálogo inicial usa `Historico\Auxiliares\importar_inventario.bat`.

> Guía completa, paso a paso y sin conexión: **[`INSTALACION.md`](INSTALACION.md)**

---

## Uso diario

| Acción | Cómo |
|---|---|
| Abrir la caja | Doble clic en **`caja.bat`** |
| Abrir la caja desde otro equipo de la red | **`caja_en_red.bat`** → `http://<IP>:8090/pos` |
| Buscar productos | **`buscar_productos.bat`** |
| Imprimir un ticket suelto | **`impresora.bat`** |
| Actualizar el catálogo | **`Historico\Auxiliares\actualizar_inventario.bat`** |
| Verificar la instalación | **`verificar_instalacion.bat`** |
| Empaquetar para otra computadora | **`empaquetar_proyecto.bat`** |

Por omisión la caja **sólo escucha en `127.0.0.1`** (esta computadora). Exponerla
en la red es opt-in con `caja_en_red.bat` o `"allow_lan": true`, y se puede
restringir por equipo con la lista `allowed_ips`.

Detalle de operación: **[`README_POS.md`](README_POS.md)** · Atajos y recetas:
**[`MANUAL_RAPIDO.md`](MANUAL_RAPIDO.md)**

---

## Estructura del proyecto

```
LaColonial_POS/
├── pos_core.py             # Núcleo: base de datos, productos, ventas, caja y usuarios
├── pos_api.py              # API HTTP (JSON) y servidor de tickets
├── app_pos.py              # Servidor web de la caja (UI en navegador)
├── pos_print.py            # Formato e impresión de tickets
├── impresora_local.py      # Puente de impresión local
├── catalogo.py             # Búsqueda y normalización del catálogo
├── importar_clientes.py    # Importación de clientes desde el histórico
├── reiniciar_caja.py       # Reinicio controlado del turno/caja
├── restaurar_respaldo.py   # Restauración de respaldos
├── verificar_instalacion.py / verificar_paquete.py
├── empaquetar_proyecto.py  # Empaquetado para otra computadora
├── ui/                     # Interfaz web (HTML/CSS/JS) de la caja
├── pos_config.json         # Configuración local de la tienda
├── pos_config.example.json # Plantilla de configuración (sin datos reales)
├── Historico/              # Módulo ETL y consultas del inventario histórico
│   ├── Auxiliares/         # Pipeline, corte de inventario e importación
│   └── ui/                 # Interfaz de consultas del histórico
└── *.bat, *.md, *.py       # Utilidades, documentación y scripts
```

Detalle técnico completo: **[`ARQUITECTURA.md`](ARQUITECTURA.md)**

---

## Configuración

La tienda se configura en `pos_config.json`:

| Sección | Contenido |
|---|---|
| `store` | Nombre, dirección, teléfono, RFC y pie de página del ticket. |
| `printer` | Impresora, ancho de papel, copias y estilo del total. |
| `rules` | Reglas de venta: nombres y SKU bloqueados, precio mínimo, fondo máximo, límites de cantidad y efectivo. |
| `security` | Duración de sesión, PIN mínimo, requisitos de gerente para cancelaciones y devoluciones, y el **secreto de firma de tickets**. |
| `network` | Acceso por red local (`allow_lan`) y lista de IPs permitidas. |
| `backup` | Frecuencia de los respaldos automáticos y cuántos conservar. |

`pos_config.example.json` tiene la misma estructura con valores neutros; la
aplicación genera un `ticket_secret` propio si no existe.

> **Nota sobre datos reales:** este repositorio es público e incluye el
> `pos_config.json` de la tienda (RFC, teléfono, dirección y el
> `ticket_secret` de ejemplo). Si vas a reutilizar el proyecto, **cambia el
> `ticket_secret`**: es la clave con la que se firman los enlaces de los
> tickets, y quien la conozca puede generar un enlace válido para cualquier
> venta.

---

## Datos que no se versionan

Por seguridad y tamaño, el repositorio **no** incluye datos operativos:

| Excluido | Contenido |
|---|---|
| `pos.db` | Ventas, productos, existencias, clientes y usuarios. |
| `backups/` | Respaldos automáticos y manuales de la base. |
| `tickets/` | Tickets generados. |
| `Historico/Auxiliares/*.csv`, `*.xlsx`, `*.db` | Inventarios congelados y catálogos extraídos. |
| `runtime/`, `vendor/` | Python portable y dependencias. |
| `*.log`, `__pycache__/` | Registros y caché. |

Ver `.gitignore` para el detalle completo. El repositorio pesa **~0.9 MB** de
código y documentación.

---

## Respaldo en GitHub

El proyecto se respalda en **dos repositorios**:

| Repositorio | Visibilidad | Contenido |
|---|---|---|
| **`LaColonial_POS`** (este) | Público | Código y documentación, sin datos reales. |
| **`LaColonial_POS_Datos`** | **Privado** | Respaldo completo: `pos.db`, `pos_config.json` real, últimos respaldos, histórico (incluido el dump del sistema anterior), entorno portable y copia del código. |

### Cómo se genera el respaldo

```powershell
respaldar_a_github.bat          # o: python respaldo_github.py
```

El script:

1. Copia `pos.db` con la **API de backup de SQLite** (copia consistente aunque la caja esté cobrando).
2. Copia `pos_config.json`, los últimos 5 respaldos `.db` y el módulo `Historico`.
3. Guarda los archivos de más de 100 MB comprimidos en `.zip` (límite de GitHub).
4. Escribe `MANIFIESTO.txt` con fecha, tamaños y SHA-256 de la base.
5. Hace commit y `push` al repositorio **privado**.

Opciones útiles:

```powershell
python respaldo_github.py --keep 10     # conservar más respaldos .db
python respaldo_github.py --no-push     # dejar el commit local, sin subir
python respaldo_github.py --repo D:\OtraCarpeta
```

### Restaurar desde el respaldo

Guía completa paso a paso (máquina nueva, base dañada, catálogo perdido,
problemas frecuentes): **[`RESTAURACION.md`](RESTAURACION.md)**

```powershell
git clone https://github.com/colonialxicotepec/LaColonial_POS_Datos.git
cd LaColonial_POS_Datos
# descomprimir los .zip (archivos > 100 MB) antes de copiar
copy codigo\* D:\LaColonial_POS\ /S /Y
copy datos\pos.db D:\LaColonial_POS\ /Y
copy datos\pos_config.json D:\LaColonial_POS\ /Y
```

---

## Desarrollo con Git

```powershell
git status                      # ver cambios
git add . && git commit -m "mensaje"
git push                        # subir a origin/main
gh repo view --web              # abrir el repositorio en el navegador
```

Convenciones: rama principal `main`, un commit por cambio, mensajes en
español.

---

## Documentación

| Documento | Contenido |
|---|---|
| [`README_POS.md`](README_POS.md) | Manual de la caja: cobro, inventario, cortes, respaldos y límites. |
| [`INSTALACION.md`](INSTALACION.md) | Instalación paso a paso en una computadora Windows limpia. |
| [`MANUAL_RAPIDO.md`](MANUAL_RAPIDO.md) | Recetas y atajos para el día a día. |
| [`ARQUITECTURA.md`](ARQUITECTURA.md) | Diseño técnico: módulos, API, base de datos y seguridad. |
| [`README_ETL.md`](README_ETL.md) | Documentación del módulo de sincronización y saneamiento del inventario. |
| [`Historico/README.md`](Historico/README.md) | Detalle del módulo histórico. |

---

## Licencia

MIT — ver [`LICENSE`](LICENSE).
