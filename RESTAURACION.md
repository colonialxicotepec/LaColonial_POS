# Restauración del POS La Colonial

Esta guía cubre la restauración de la base de datos desde un archivo `.db` que
ya tengas disponible en la computadora o en un medio externo.

> **GitHub contiene sólo el código y la documentación.** No guarda ventas,
> clientes, inventario operativo ni la configuración real. Si se pierde la
> computadora y no existe una copia de los datos en otro medio, clonar el repo
> público no recupera esos datos.

## Restaurar la base desde un archivo `.db`

1. Copia el archivo de respaldo a la carpeta `backups` del proyecto.
2. Cierra la caja y asegúrate de que no quede abierto el proceso de Python.
3. Abre PowerShell en la carpeta `D:\LaColonial_POS`.
4. Lista y revisa los respaldos:

   ```powershell
   runtime\python.exe restaurar_respaldo.py --listar
   ```

5. Verifica el archivo que vas a usar:

   ```powershell
   runtime\python.exe restaurar_respaldo.py --verificar backups\NOMBRE_DEL_ARCHIVO.db
   ```

6. Si la verificación indica que es válido, restáuralo:

   ```powershell
   runtime\python.exe restaurar_respaldo.py --restaurar backups\NOMBRE_DEL_ARCHIVO.db --confirmar
   ```

Antes de reemplazar `pos.db`, la herramienta crea un respaldo de seguridad
`antes_restaurar`, elimina los archivos SQLite `-wal`/`-shm` antiguos y vuelve a
verificar la base restaurada. No interrumpas el proceso.

## Verificar después de restaurar

```powershell
runtime\python.exe verificar_instalacion.py
```

Después, abre `caja.bat`, entra en <http://127.0.0.1:8090/pos> y comprueba el
catálogo, las ventas recientes y la impresión de tickets.

También se puede comprobar la integridad de la base:

```powershell
runtime\python.exe -c "import sqlite3; c=sqlite3.connect('pos.db'); print(c.execute('pragma integrity_check').fetchone()[0]); c.close()"
```

El resultado esperado es `ok`.

## Recuperar sólo el código desde GitHub

El repositorio público sirve para recuperar el código, no los datos:

```powershell
cd D:\
git clone https://github.com/colonialxicotepec/LaColonial_POS.git
```

Para una instalación nueva, copia la plantilla de configuración y sigue
[`INSTALACION.md`](INSTALACION.md):

```powershell
cd D:\LaColonial_POS
copy pos_config.example.json pos_config.json
```

Una instalación limpia crea una base vacía; para recuperar las ventas,
existencias y clientes hace falta restaurar un archivo de datos por separado.
