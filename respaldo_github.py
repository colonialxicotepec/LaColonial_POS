#!/usr/bin/env python3
"""Respaldo del POS La Colonial a un repositorio privado de GitHub.

Crea un snapshot consistente de los datos reales (pos.db, configuracion,
respaldos recientes, historico) y lo publica en un repositorio PRIVADO de
GitHub, junto con una copia del codigo para poder restaurar el proyecto
completo con un solo `git clone`.

Uso:
    python respaldo_github.py                 # respaldo completo + push
    python respaldo_github.py --no-push       # solo deja el commit local
    python respaldo_github.py --keep 10       # conservar 10 respaldos .db
    python respaldo_github.py --repo D:\\Respaldo_LaColonial

Notas:
  * pos.db se copia con la API de backup de SQLite, de modo que el archivo
    resultante es consistente aunque la caja este cobrando en ese momento.
  * El repositorio de destino DEBE ser privado: contiene ventas, clientes,
    RFC y catalogos con costos.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DEFAULT_REPO = Path(r"D:\Respaldo_LaColonial")
DEFAULT_REMOTE = "https://github.com/colonialxicotepec/LaColonial_POS_Datos.git"

CODE_DIR = "codigo"
DATA_DIR = "datos"
ENV_DIR = "entorno"

# GitHub no acepta archivos de mas de 100 MB: los archivos grandes se guardan
# comprimidos en .zip (se restauran con Expand-Archive / unzip).
BIG_FILE_THRESHOLD = 50 * 1024 * 1024


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[respaldo] {msg}", flush=True)


def find_git() -> str:
    git = shutil.which("git")
    if git:
        return git
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd" / "git.exe",
        Path(r"C:\Program Files\Git\cmd\git.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    raise SystemExit("No se encontro git.exe. Instala Git for Windows o agrega git al PATH.")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"Fallo: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return (proc.stdout or "") + (proc.stderr or "")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_tree(src: Path, dst: Path, skip_names=(".git", "__pycache__", "tickets")) -> int:
    """Copia un arbol de archivos. Devuelve la cantidad de archivos copiados."""
    if not src.exists():
        return 0
    count = 0
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip_names and not d.endswith(".log")]
        rel = Path(root).relative_to(src)
        for name in files:
            if name.endswith((".log", ".pyc")) or name == ".DS_Store":
                continue
            s = Path(root) / name
            d = dst / rel / name
            d.parent.mkdir(parents=True, exist_ok=True)
            store_file(s, d, zip_big=True)
            count += 1
    return count


def store_file(src: Path, dst: Path, zip_big: bool = False, force_zip: bool = False) -> str:
    """Guarda un archivo en el repo de respaldo.

    Si el archivo supera el limite de GitHub (100 MB) o si force_zip es True,
    se guarda comprimido como .zip (se restaura con Expand-Archive / unzip).
    Devuelve 'copia', 'zip' o 'sin-cambios'.
    """
    comprimir = force_zip or (src.stat().st_size >= BIG_FILE_THRESHOLD and zip_big)
    if comprimir:
        target = dst.with_name(dst.name + ".zip")
        # Reutiliza el zip si el origen no cambio.
        if target.exists() and abs(target.stat().st_mtime - src.stat().st_mtime) < 2:
            return "sin-cambios"
        tmp = target.with_suffix(".zip.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.write(src, src.name)
        os.replace(tmp, target)
        shutil.copystat(src, target)
        return "zip"
    d = dst
    d.parent.mkdir(parents=True, exist_ok=True)
    if d.exists() and d.stat().st_size == src.stat().st_size:
        try:
            if abs(d.stat().st_mtime - src.stat().st_mtime) < 2:
                return "sin-cambios"
        except OSError:
            pass
    shutil.copy2(src, d)
    return "copia"


def clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Snapshot de la base de datos
# --------------------------------------------------------------------------
def snapshot_sqlite(src: Path, dst: Path) -> None:
    """Copia consistente de SQLite usando la API de backup (sin detener la caja)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d, pages=256)
        finally:
            d.close()
    finally:
        s.close()


def integrate_db(destino: Path) -> None:
    """Reemplaza el destino con una copia consistente de la base de origen."""
    origen = APP_DIR / "pos.db"
    if not origen.exists():
        log("pos.db no existe: se omite la base de datos.")
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    snapshot_sqlite(origen, destino)
    log(f"pos.db copiado ({destino.stat().st_size / 1048576:.1f} MB)")


# --------------------------------------------------------------------------
# Codigo (se toma de los archivos que ya versiona el repo principal)
# --------------------------------------------------------------------------
def copy_code(git: str, destino: Path) -> int:
    clear_dir(destino)
    try:
        out = run([git, "-C", str(APP_DIR), "ls-files"], check=True)
    except RuntimeError:
        out = ""
    files = [f for f in out.splitlines() if f.strip()]
    if not files:
        # Sin git: se copian los archivos sueltos del proyecto.
        for p in APP_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in (".py", ".bat", ".md", ".json", ".txt"):
                if p.name != "pos_config.json":
                    files.append(p.name)
    count = 0
    for rel in files:
        s = APP_DIR / rel
        if not s.is_file() or s.name == "pos_config.json":
            continue
        if s.suffix.lower() in (".db", ".log", ".zip", ".exe"):
            continue
        d = destino / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        count += 1
    log(f"{count} archivos de codigo copiados")
    return count


def copy_config(destino: Path) -> None:
    origen = APP_DIR / "pos_config.json"
    if origen.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, destino)
        log("pos_config.json (configuracion real) copiado")


def copy_backups(destino: Path, keep: int) -> int:
    origen = APP_DIR / "backups"
    if not origen.exists():
        return 0
    dbs = sorted((p for p in origen.glob("*.db") if p.is_file()),
                 key=lambda p: p.stat().st_mtime, reverse=True)
    if not dbs:
        return 0
    clear_dir(destino)
    total = 0
    for p in dbs[:keep]:
        # Se guardan comprimidos: ocupan ~3 veces menos en el historial de Git.
        store_file(p, destino / p.name, force_zip=True)
        total += p.stat().st_size
    log(f"{min(keep, len(dbs))} respaldos conservados ({total / 1048576:.1f} MB)")
    return len(dbs[:keep])


# --------------------------------------------------------------------------
# Repo de respaldo
# --------------------------------------------------------------------------
def prepare_repo(repo: Path, git: str, remote: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        run([git, "init", "-b", "main"], cwd=repo)
        log("Repositorio de respaldo inicializado")
    run([git, "remote", "remove", "origin"], cwd=repo, check=False)
    run([git, "remote", "add", "origin", remote], cwd=repo)
    run([git, "config", "user.name", "Colonial Xicotepec"], cwd=repo, check=False)
    run([git, "config", "user.email", "361277226+colonialxicotepec@users.noreply.github.com"], cwd=repo, check=False)

    gi = repo / ".gitignore"
    gi.write_text(
        "*.log\n__pycache__/\n*.pyc\ntickets/\n",
        encoding="utf-8")


def write_manifest(repo: Path, db_file: Path, stats: dict) -> None:
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lineas = [
        "MANIFIESTO DEL RESPALDO - POS LA COLONIAL",
        "=" * 42,
        f"Generado          : {ahora}",
        f"Repositorio       : {repo}",
        "",
    ]
    if db_file.exists():
        lineas += [
            "Base de datos (pos.db)",
            f"  Tamano   : {db_file.stat().st_size / 1048576:.2f} MB",
            f"  SHA-256  : {sha256(db_file)}",
            "",
        ]
    for k, v in stats.items():
        lineas.append(f"{k:<34}: {v}")
    zips = sorted(p.name for p in repo.rglob("*.zip"))
    if zips:
        lineas.append("")
        lineas.append("Archivos comprimidos (superan el limite de 100 MB de GitHub):")
        for z in zips:
            lineas.append(f"  - {z}")
    lineas += [
        "",
        "RESTAURAR:",
        "  git clone https://github.com/colonialxicotepec/LaColonial_POS_Datos.git",
        "  cd LaColonial_POS_Datos",
        "  # descomprimir los .zip (archivos grandes) antes de copiar:",
        "  #   Expand-Archive -Path datos\\historico\\historico_1box.db.zip -DestinationPath ...",
        "  copy codigo\\* D:\\LaColonial_POS\\ /S /Y",
        "  copy datos\\pos.db D:\\LaColonial_POS\\ /Y",
        "  copy datos\\pos_config.json D:\\LaColonial_POS\\ /Y",
        "",
    ]
    (repo / "MANIFIESTO.txt").write_text("\n".join(lineas), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Respaldo del POS a GitHub (repo privado)")
    ap.add_argument("--repo", default=str(DEFAULT_REPO), help="Carpeta del repositorio de respaldo")
    ap.add_argument("--remote", default=DEFAULT_REMOTE, help="URL del repositorio privado en GitHub")
    ap.add_argument("--keep", type=int, default=5, help="Cuantos respaldos .db conservar")
    ap.add_argument("--no-push", action="store_true", help="No subir a GitHub")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if repo == APP_DIR or APP_DIR in repo.parents:
        print("ERROR: el repositorio de respaldo no puede estar dentro del proyecto.",
              file=sys.stderr)
        return 2

    git = find_git()
    log(f"Proyecto : {APP_DIR}")
    log(f"Respaldo : {repo}")

    prepare_repo(repo, git, args.remote)

    codigo = repo / CODE_DIR
    datos = repo / DATA_DIR
    entorno = repo / ENV_DIR

    stats = {}
    copy_code(git, codigo)

    # La guia de restauracion es la portada del repositorio privado de datos.
    guia = codigo / "RESTAURACION.md"
    if guia.exists():
        shutil.copy2(guia, repo / "README.md")
        log("README.md (guia de restauracion) actualizado")

    copy_config(datos / "pos_config.json")

    db_dest = datos / "pos.db"
    integrate_db(db_dest)

    n_backups = copy_backups(datos / "respaldos", args.keep)
    stats["Respaldos .db conservados"] = n_backups

    exp = APP_DIR / "exportes"
    if exp.exists():
        n = copy_tree(exp, datos / "exportes")
        stats["Exportes copiados"] = n

    n_hist = copy_tree(APP_DIR / "Historico", datos / "historico")
    stats["Archivos de historico"] = n_hist

    n_rt = copy_tree(APP_DIR / "runtime", entorno / "runtime")
    n_vd = copy_tree(APP_DIR / "vendor", entorno / "vendor")
    stats["Entorno portable (runtime/vendor)"] = n_rt + n_vd

    write_manifest(repo, db_dest, stats)

    run([git, "add", "-A"], cwd=repo)
    estado = run([git, "status", "--porcelain"], cwd=repo)
    if not estado.strip():
        log("Sin cambios: el respaldo ya estaba al dia.")
    else:
        mensaje = f"Respaldo automatico {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        run([git, "commit", "-m", mensaje], cwd=repo)
        log(f"Commit: {mensaje}")

    if not args.no_push:
        try:
            out = run([git, "push", "-u", "origin", "main"], cwd=repo, check=False)
            log(out.strip() or "Push completado")
            if "rejected" in out.lower() or "error" in out.lower():
                log("AVISO: el push fallo. Revisa la red o vuelve a ejecutar.")
                return 1
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: no se pudo subir ({exc}). El commit quedo guardado en local.")
            return 1
    else:
        log("Push omitido (--no-push)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
