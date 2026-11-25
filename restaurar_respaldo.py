"""
Respaldo y restauración de la caja.

Uso:
    python restaurar_respaldo.py --listar
    python restaurar_respaldo.py --verificar backups\\pos_....db
    python restaurar_respaldo.py --restaurar backups\\pos_....db --confirmar

La restauración exige que la caja esté cerrada. Antes de reemplazar pos.db
se crea un respaldo de seguridad con etiqueta "antes_restaurar".
"""

import argparse
import os
import shutil
import sys

import pos_core


def _caja_abierta():
    import urllib.request
    for offset in range(10):
        try:
            with urllib.request.urlopen(
                    f'http://127.0.0.1:{8090 + offset}/api/pos/health', timeout=0.8) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            continue
    return False


def listar():
    rows = pos_core.list_backups()
    if not rows:
        print('No hay respaldos en', pos_core.BACKUP_DIR)
        return 0
    print(f'Respaldos en {pos_core.BACKUP_DIR}')
    print('-' * 78)
    for row in rows:
        estado = 'OK' if row['ok'] else f"ERROR: {row['error']}"
        print(f"{row['name']}  {row['modified']}  {row['size'] / 1024 / 1024:6.1f} MB  "
              f"ventas={row['sales']} productos={row['products']}  {estado}")
    return 0


def main():
    parser = argparse.ArgumentParser(description='Respaldo y restauración del POS.')
    parser.add_argument('--listar', action='store_true', help='Lista y verifica los respaldos.')
    parser.add_argument('--verificar', metavar='ARCHIVO', help='Verifica un respaldo.')
    parser.add_argument('--restaurar', metavar='ARCHIVO', help='Restaura un respaldo.')
    parser.add_argument('--confirmar', action='store_true',
                        help='Confirma la restauración (obligatorio con --restaurar).')
    args = parser.parse_args()

    if args.verificar:
        info = pos_core.verify_backup(args.verificar)
        print(info)
        return 0 if info['ok'] else 1

    if args.restaurar:
        if not args.confirmar:
            print('Agrega --confirmar para restaurar. La caja debe estar cerrada.')
            return 2
        if _caja_abierta():
            print('La caja está abierta. Ciérrala antes de restaurar.')
            return 2
        info = pos_core.verify_backup(args.restaurar)
        if not info['ok']:
            print('El respaldo no es válido:', info['error'])
            return 1
        safety = pos_core.backup('antes_restaurar')
        print('Respaldo de seguridad:', safety)
        for suffix in ('-wal', '-shm'):
            extra = pos_core.POS_DB + suffix
            if os.path.exists(extra):
                os.remove(extra)
        shutil.copy2(args.restaurar, pos_core.POS_DB)
        check = pos_core.verify_backup(pos_core.POS_DB)
        if not check['ok']:
            print('La restauración falló la verificación:', check['error'])
            return 1
        print(f'Restaurado: {pos_core.POS_DB}')
        print(f"Ventas={check['sales']} productos={check['products']} usuarios={check['users']}")
        return 0

    return listar()


if __name__ == '__main__':
    sys.exit(main())
