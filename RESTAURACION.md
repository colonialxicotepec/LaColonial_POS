# Restauración del POS La Colonial desde GitHub

Guía completa para recuperar el proyecto **completo** (código + datos) desde
GitHub cuando la computadora falla, se cambia, o la base de datos se daña.

---

## 1. Qué hay guardado y dónde

| Recurso | Ubicación | Visibilidad | Qué contiene |
|---|---|---|---|
| **Código y documentación** | `github.com/colonialxicotepec/LaColonial_POS` | Público | Todos los `.py`, `.bat`, `.md`, `ui/`, licencia, `.gitignore` |
| **Datos y entorno** | `github.com/colonialxicotepec/LaColonial_POS_Datos` | **Privado** | `pos.db`, `pos_config.json` real, respaldos `.db`, histórico, Python portable y copia del código |
| **Copia local de trabajo** | `D:\Respaldo_LaColonial` | Local | Clon del repo privado; se regenera solo con el próximo respaldo |

> El repo privado es el que permite **restaurar la información de la tienda**.
> Sin internet, sin acceso a `D:\` y sin la computadora original, ese repositorio
> es la única fuente de recuperación.

### Anatomía del repositorio privado

```
LaColonial_POS_Datos/
├── MANIFIESTO.txt                 Fecha, tamaños y SHA-256 del último respaldo
├── README.md                      Esta guía (copia de RESTAURACION.md)
├── codigo/                        Copia del código del proyecto
│   ├── *.py, *.bat, *.md, *.json
│   ├── ui/                        Interfaz web de la caja
│   └── Historico/                 Módulo ETL y consultas
├── datos/
│   ├── pos.db                     Base de datos actual (ventas, productos, clientes)
│   ├── pos_config.json            Configuración real de la tienda
│   ├── respaldos/                 Últimos 5 respaldos .db comprimidos (.zip)
│   ├── historico/                 Dump del sistema anterior + inventarios congelados
│   └── exportes/                  Reportes exportados (si existen)
└── entorno/
    ├── runtime/                   Python 3.12 portable (para operar sin internet)
    └── vendor/                    Instalador de Python portable (.zip)
```

---

## 2. Requisitos

- **Windows 10/11** con PowerShell.
- **Git** instalado (https://git-scm.com/download/win) — o el `git.exe` portátil
  que viene en `entorno/` del repo de respaldo.
- **GitHub CLI** (`gh`) sólo si se van a crear repos o autenticar el equipo
  nuevo: https://cli.github.com/
- **Credenciales de GitHub** de una cuenta con acceso a los dos repositorios
  (el privado requiere collaborator o acceso de la organización).
- ~500 MB de disco libre.

---

## 3. Escenario principal: máquina nueva o disco muerto

### Paso 1 — Clonar el repo privado (los datos)

```powershell
cd D:\
git clone https://github.com/colonialxicotepec/LaColonial_POS_Datos.git
cd LaColonial_POS_Datos
```

Si el equipo nuevo todavía no tiene Git, usa el portable incluido en
`entorno/runtime/cmd` (o descomprime `entorno/vendor/python-3.12.10-embed-amd64.zip`
para tener Python sin instalar nada).

### Paso 2 — Descomprimir los archivos grandes

GitHub no acepta archivos de más de 100 MB, así que el histórico y los
respaldos viajan comprimidos:

```powershell
Expand-Archive -Path datos\historico\historico_1box.db.zip -DestinationPath datos\historico -Force
Get-ChildItem datos\respaldos\*.zip | ForEach-Object {
    Expand-Archive -Path $_.FullName -DestinationPath datos\respaldos -Force
}
```

### Paso 3 — Clonar el código (o usar la copia que ya viene)

```powershell
cd D:\
git clone https://github.com/colonialxicotepec/LaColonial_POS.git LaColonial_POS
```

Alternativa sin clonar: la carpeta `codigo/` del repo privado ya contiene una
copia completa del código (incluye `ui/` y `Historico/`).

### Paso 4 — Reconstruir la instalación del proyecto

```powershell
cd D:\LaColonial_POS

# 1) Restaurar el entorno portable (Python 3.12 sin internet)
copy /Y D:\LaColonial_POS_Datos\entorno\runtime\* .\runtime\
copy /Y D:\LaColonial_POS_Datos\entorno\vendor\* .\vendor\

# 2) Restaurar la base de datos y la configuración reales
copy /Y D:\LaColonial_POS_Datos\datos\pos.db .
copy /Y D:\LaColonial_POS_Datos\datos\pos_config.json .

# 3) (Opcional) Restaurar el módulo histórico y sus inventarios
copy /Y D:\LaColonial_POS_Datos\datos\historico\* .\Historico\ /S
```

> **Importante:** `pos_config.json` contiene el `ticket_secret` con el que se
> firman los tickets. Al restaurarlo se recuperan también los enlaces de
> tickets emitidos con ese secreto.

### Paso 5 — Verificar la instalación

```powershell
runtime\python.exe verificar_instalacion.py
```

El script revisa Python, archivos obligatorios, base de datos, permisos de
escritura, puerto e impresora, y deja un reporte en
`Historico\Auxiliares\migracion\verificacion_instalacion.json`.

### Paso 6 — Comprobar los datos restaurados

```powershell
runtime\python.exe -c "import sqlite3; c=sqlite3.connect('pos.db'); print('integridad:', c.execute('pragma integrity_check').fetchone()[0]); print('productos:', c.execute('select count(*) from products').fetchone()[0]); print('ventas:', c.execute('select count(*) from sales').fetchone()[0]); print('clientes:', c.execute('select count(*) from customers').fetchone()[0])"
```

Valores de referencia del último respaldo: **7,091 productos, 916 ventas,
403 clientes, 7 usuarios, `integrity_check: ok`**.

### Paso 7 — Arrancar la caja

```powershell
caja.bat
```

Abre <http://127.0.0.1:8090/pos>. Entra con el **PIN de administrador**.

### Paso 8 — Reactivar el respaldo automático

```powershell
schtasks /Create /TN "POS La Colonial - Respaldo GitHub" /TR "cmd /c D:\LaColonial_POS\runtime\python.exe D:\LaColonial_POS\respaldo_github.py > D:\LaColonial_POS\respaldo_github.log 2>&1" /SC DAILY /ST 23:30 /F
```

Y comprueba que el equipo ya está autenticado en GitHub:

```powershell
gh auth status
```

---

## 4. Escenario: la base de datos está dañada (la máquina sigue viva)

No hace falta clonar nada: se usa el respaldo que ya está en GitHub o en la
carpeta local `backups/`.

### Opción A — Con la herramienta del proyecto (recomendado)

```powershell
# 1) Listar y verificar los respaldos disponibles
runtime\python.exe restaurar_respaldo.py --listar

# 2) Verificar uno concreto
runtime\python.exe restaurar_respaldo.py --verificar backups\pos_20261125_180117_arranque.db

# 3) Restaurar (la caja debe estar CERRADA)
runtime\python.exe restaurar_respaldo.py --restaurar backups\pos_20261125_180117_arranque.db --confirmar
```

El script crea antes un respaldo de seguridad con etiqueta `antes_restaurar`,
borra los archivos `-wal`/`-shm` y verifica la base restaurada.

### Opción B — Desde el repo de respaldo de GitHub

```powershell
cd D:\LaColonial_POS_Datos
Expand-Archive datos\respaldos\pos_20261125_180117_arranque.db.zip -DestinationPath datos\respaldos -Force
Copy-Item datos\respaldos\pos_20261125_180117_arranque.db D:\LaColonial_POS\backups\ -Force
cd D:\LaColonial_POS
runtime\python.exe restaurar_respaldo.py --restaurar backups\pos_20261125_180117_arranque.db --confirmar
```

### Opción C — Reemplazar `pos.db` directamente

```powershell
# Caja cerrada
Copy-Item D:\LaColonial_POS_Datos\datos\pos.db D:\LaColonial_POS\pos.db -Force
```

---

## 5. Escenario: recuperar el catálogo y el histórico del sistema anterior

El dump `historico_1box.db` (161 MB) y los inventarios congelados permiten
reconstruir el catálogo si `pos.db` se perdió por completo.

```powershell
# 1) Restaurar los archivos del histórico
Expand-Archive D:\LaColonial_POS_Datos\datos\historico\historico_1box.db.zip -DestinationPath D:\LaColonial_POS\Historico -Force
Copy-Item D:\LaColonial_POS_Datos\datos\historico\* D:\LaColonial_POS\Historico\ -Recurse -Force

# 2) Ver las consultas del histórico
Historico\abrir_puerto_historico.bat

# 3) Reimportar el catálogo al POS nuevo
Historico\Auxiliares\importar_inventario.bat

# 4) (Opcional) Empezar con caja limpia, conservando el inventario
runtime\python.exe reiniciar_caja.py --si
```

`reiniciar_caja.py` deja la caja como de fábrica (sin usuarios, ventas ni
turnos) **sin tocar el inventario**, que se reimporta desde el catálogo
congelado.

---

## 6. Escenario: sólo necesito el código (sin datos)

```powershell
git clone https://github.com/colonialxicotepec/LaColonial_POS.git
cd LaColonial_POS
copy pos_config.example.json pos_config.json
runtime\python.exe verificar_instalacion.py
caja.bat
```

La base `pos.db` se crea sola con su esquema; después se importa el catálogo.

---

## 7. Copias de seguridad fuera de GitHub

GitHub es una copia **remota**, pero no es un espejo: si se pierde la cuenta,
el repo o el token, se pierde el respaldo. Recomendado:

1. **Copia local** del repo de respaldo:
   ```powershell
   Copy-Item -Recurse -Force D:\Respaldo_LaColonial E:\Respaldo_LaColonial
   ```
2. **Disco externo** con copia semanal de `pos.db` y de `D:\Respaldo_LaColonial`.
3. **Otra nube** (OneDrive, Google Drive, MEGA) sincronizando `D:\Respaldo_LaColonial`.
4. **Rotar el `ticket_secret`** si alguien más accede a los repositorios.

---

## 8. Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| `gh: To use GitHub CLI in a GitHub Actions workflow...` / `not logged into` | El equipo nuevo no está autenticado | `gh auth login` (flujo con código en el navegador) y luego `gh auth setup-git` |
| `Permission denied (publickey)` o pide usuario/contraseña | Se usó SSH con una clave que no está en este equipo | Cambia la URL a HTTPS: `git remote set-url origin https://github.com/colonialxicotepec/LaColonial_POS_Datos.git` |
| `fatal: could not read Username` | Repositorio privado sin credenciales | Necesitas ser collaborator del repo privado, o usar un token personal (PAT) como contraseña |
| `error: RPC failed; HTTP 413 curl error 6` | Se intentó subir un archivo de más de 100 MB | Los archivos grandes van comprimidos; no los descomprimas **dentro** del repo antes de hacer push |
| `database is locked` al restaurar | La caja está abierta o hay un proceso de respaldo activo | Cierra la caja (ventana del navegador y `caja.bat`/consola) y reintenta |
| `La caja está abierta. Ciérrala antes de restaurar.` | `restaurar_respaldo.py` detecta la caja en `127.0.0.1:8090+` | Cierra la caja y repite el comando |
| `No se encontro git.exe` | Git no está instalado ni en el PATH | Usa `entorno/runtime/cmd/git.exe` del repo de respaldo o instala Git for Windows |
| La tarea programada no sube nada | La sesión de Windows estaba cerrada a las 23:30 | La tarea es "Solo interactivo"; ejecuta `respaldar_a_github.bat` a mano o pide una tarea con `-LogonType S4U` (requiere administrador) |
| El repo privado supera 5 GB | El historial creció con muchos respaldos diarios | `runtime\python.exe respaldo_github.py --keep 2` y, si hace falta, reescribir el historial |
| `No such table: products` | Se arrancó sin `pos.db` o con una base vacía | Restaura `pos.db` desde `datos/` (sección 4) o importa el catálogo |

---

## 9. Checklist de verificación posterior a una restauración

- [ ] `runtime\python.exe verificar_instalacion.py` termina sin errores.
- [ ] `pragma integrity_check` devuelve `ok`.
- [ ] Conteo de productos ≈ 7,091 y ventas ≈ 916 (o los de la fecha del respaldo).
- [ ] `caja.bat` abre <http://127.0.0.1:8090/pos> y acepta el PIN de administrador.
- [ ] El catálogo se busca y el stock coincide con el último corte.
- [ ] Se emite una venta de prueba y el ticket se imprime.
- [ ] `respaldar_a_github.bat` sube un commit nuevo al repo privado.
- [ ] La tarea programada "POS La Colonial - Respaldo GitHub" quedó **Listo**.

---

## 10. Comandos de referencia

```powershell
# Respaldar ahora
respaldar_a_github.bat
runtime\python.exe respaldo_github.py --keep 10      # más respaldos .db
runtime\python.exe respaldo_github.py --no-push      # sólo commit local

# Ver respaldos locales
runtime\python.exe restaurar_respaldo.py --listar

# Verificar el último respaldo de GitHub
cd D:\LaColonial_POS_Datos
Get-Content MANIFIESTO.txt

# Estado de ambos repositorios
gh repo view colonialxicotepec/LaColonial_POS
gh repo view colonialxicotepec/LaColonial_POS_Datos
```
