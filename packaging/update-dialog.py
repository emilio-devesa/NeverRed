#!/usr/bin/env python3
"""Diálogo de actualización de NeverRed (Tkinter, de serie en macOS).

Cuenta qué trae la versión, con las novedades en un campo con scroll, y
tranquiliza: los datos se conservan y la descarga se verifica con firma.

Uso: update-dialog.py <remota> <fichero-notas> [local]
Imprime: install | later | skip.
NEVERRED_DIALOG_TIMEOUT_MS autocierra como 'later' (para pruebas).
"""
import os
import sys
import tkinter as tk
from tkinter import scrolledtext

FALLBACK = ("Mejoras internas y correcciones de errores.\n\n"
            "Tus datos y ajustes se conservan tal cual.")


def main():
    remote = sys.argv[1]
    with open(sys.argv[2], encoding="utf-8", errors="replace") as f:
        raw = f.read().replace("\r\n", "\n").replace("\r", "\n")
    # Las notas pueden traer el bloque de descarga al final: en la app sobra
    # (el usuario ya la tiene instalada); nos quedamos con las novedades.
    notes = raw.split("## Descargar")[0].strip()[:2000] or FALLBACK
    local = sys.argv[3] if len(sys.argv) > 3 else ""
    same = local and local.lstrip("v") == remote.lstrip("v")
    choice = {"v": "later"}
    root = tk.Tk()
    root.title("Actualización de NeverRed")
    head = "NeverRed %s disponible" % remote
    if local and not same:
        head += " (tienes la %s)" % local
    tk.Label(root, text=head, font=("", 13, "bold")).pack(padx=16, pady=(14, 2))
    tk.Label(root, text="Novedades de esta versión:").pack(anchor="w", padx=16)
    txt = scrolledtext.ScrolledText(root, width=64, height=8, wrap="word")
    txt.insert("1.0", notes)
    txt.configure(state="disabled")
    txt.pack(padx=16, pady=6, fill="both", expand=True)
    tk.Label(root, text="Tus datos y ajustes se conservan. "
                        "La descarga se verifica con firma antes de instalar.",
             fg="#888", font=("", 11), wraplength=480,
             justify="left").pack(anchor="w", padx=16, pady=(0, 8))
    bar = tk.Frame(root)
    bar.pack(pady=(0, 14))

    def pick(v):
        choice["v"] = v
        root.destroy()

    tk.Button(bar, text="Omitir versión",
              command=lambda: pick("skip")).pack(side="left", padx=6)
    tk.Button(bar, text="Más tarde",
              command=lambda: pick("later")).pack(side="left", padx=6)
    tk.Button(bar, text="Instalar",
              command=lambda: pick("install"), default="active").pack(side="left", padx=6)
    root.bind("<Return>", lambda e: pick("install"))
    root.bind("<Escape>", lambda e: pick("later"))
    try:
        timeout = int(os.environ.get("NEVERRED_DIALOG_TIMEOUT_MS", "0"))
    except ValueError:
        timeout = 0
    if timeout > 0:
        root.after(timeout, lambda: pick("later"))
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    root.geometry("+%d+%d" % ((root.winfo_screenwidth() - w) // 2,
                              (root.winfo_screenheight() - h) // 3))
    root.mainloop()
    print(choice["v"])


main()
