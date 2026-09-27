#!/usr/bin/env python3
"""Diálogo de actualización de NeverRed (Tkinter, de serie en macOS).

La pregunta va separada de los cambios: estos aparecen en un campo de
texto con unas pocas líneas a la vista y barras de desplazamiento.

Uso: update-dialog.py <versión> <fichero-changelog>
Imprime: install | later | skip.
NEVERRED_DIALOG_TIMEOUT_MS autocierra como 'later' (para pruebas).
"""
import os
import sys
import tkinter as tk
from tkinter import scrolledtext


def main():
    version, path = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8", errors="replace") as f:
        notes = f.read()[:2000]
    choice = {"v": "later"}
    root = tk.Tk()
    root.title("Actualización de NeverRed")
    tk.Label(root, text="¿Deseas actualizar a la versión %s?" % version,
             font=("", 13, "bold")).pack(padx=16, pady=(14, 4))
    tk.Label(root, text="Cambios de la nueva versión:").pack(anchor="w", padx=16)
    txt = scrolledtext.ScrolledText(root, width=64, height=8, wrap="word")
    txt.insert("1.0", notes or "(sin notas de versión)")
    txt.configure(state="disabled")
    txt.pack(padx=16, pady=6, fill="both", expand=True)
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
