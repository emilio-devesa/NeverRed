#!/usr/bin/env python3
"""Copia de seguridad de la base de datos de NeverRed.

Crea una copia con fecha en el directorio de destino y conserva
solo las N más recientes.

Uso:
    python3 backend/backup.py [--db backend/neverred.db] [--dest backend/backups] [--keep 14]

Para automatizarla cada noche (cron):
    0 3 * * * /usr/bin/python3 /ruta/a/neverred/backend/backup.py >> /var/log/neverred-backup.log 2>&1
"""
import argparse
import os
import shutil
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description="Backup de la BD de NeverRed.")
    ap.add_argument("--db", default=os.environ.get(
        "NEVERRED_DB", os.path.join(HERE, "neverred.db")))
    ap.add_argument("--dest", default=os.path.join(HERE, "backups"))
    ap.add_argument("--keep", type=int, default=14)
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        raise SystemExit("No existe la BD: %s (¿arrancaste el servidor alguna vez?)" % args.db)
    os.makedirs(args.dest, exist_ok=True)
    name = "neverred-%s.db" % time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(args.dest, name)
    # Copia atómica: primero a temporal y luego renombra
    tmp = out + ".tmp"
    shutil.copy2(args.db, tmp)
    os.rename(tmp, out)

    snaps = sorted(f for f in os.listdir(args.dest)
                   if f.startswith("neverred-") and f.endswith(".db"))
    for old in snaps[:max(0, len(snaps) - args.keep)]:
        os.remove(os.path.join(args.dest, old))
    print("Backup OK: %s (conservadas: %d)" % (out, min(len(snaps), args.keep)))


if __name__ == "__main__":
    main()
