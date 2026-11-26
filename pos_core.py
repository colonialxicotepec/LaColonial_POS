"""
Núcleo del punto de venta: fachada de compatibilidad que re-exporta los
módulos organizados dentro de `core/`.

Módulos:
- core/db.py        : configuración, constantes, esquema SQLite y conexión
- core/users.py     : usuarios, PIN, sesiones y auditoría
- core/shifts.py    : turnos de caja y movimientos de efectivo
- core/inventory.py : inventario propio, unidades, categorías, ubicaciones y avisos
- core/sales.py     : ventas, cobros, pendientes, cancelaciones y clientes
- core/ops.py       : respaldos, devoluciones, precios de emergencia y exportaciones
"""

from core.db import *         # noqa: F401, F403
from core.users import *      # noqa: F401, F403
from core.shifts import *     # noqa: F401, F403
from core.inventory import *  # noqa: F401, F403
from core.sales import *      # noqa: F401, F403
from core.ops import *        # noqa: F401, F403
