# theme_light.py
from tkinter import ttk
import tkinter as tk

# === Tokens do Tema Claro ===
BG_APP = "#F7F9FC"
CARD_BG = "#FFFFFF"
CARD_BORDER = "#E6EAF0"
TEXT_PRIMARY = "#111827"
TEXT_SECONDARY = "#4B5563"

PRIMARY = "#03223f"        # azul solicitado
PRIMARY_HOVER = "#07345f"  # hover
PRIMARY_ACTIVE = "#052a4b" # pressed
PRIMARY_TEXT = "#FFFFFF"

TITLE_FONT = ("Segoe UI", 20, "bold")
BTN_FONT   = ("Segoe UI", 14, "bold")
SMALL_FONT = ("Segoe UI", 10)

def configure_light_theme(root: tk.Tk) -> None:
    """Registra estilos ttk do tema claro PRISMA."""
    root.configure(bg=BG_APP)
    style = ttk.Style()
    try:
        style.theme_use("clam")  # permite personalizar cores/estados
    except tk.TclError:
        pass

    # --- Card/contêiner ---
    style.configure(
        "Prisma.Card.TFrame",
        background=CARD_BG,
        bordercolor=CARD_BORDER,
        lightcolor=CARD_BG,
        darkcolor=CARD_BG,
        relief="flat"
    )

    # --- Tipografia ---
    style.configure("Prisma.Title.TLabel",
                    background=CARD_BG, foreground=TEXT_PRIMARY, font=TITLE_FONT)
    style.configure("Prisma.Secondary.TLabel",
                    background=CARD_BG, foreground=TEXT_SECONDARY, font=SMALL_FONT)

    # --- Botão primário (menu) ---
    style.configure(
        "Prisma.Primary.TButton",
        background=PRIMARY,
        foreground=PRIMARY_TEXT,
        font=BTN_FONT,
        borderwidth=0,
        padding=(18, 12),
        focusthickness=2,
        focuscolor=PRIMARY
    )
    style.map(
        "Prisma.Primary.TButton",
        background=[("pressed", PRIMARY_ACTIVE),
                    ("active", PRIMARY_ACTIVE),
                    ("hover", PRIMARY_HOVER)],
        foreground=[("disabled", "#D1D5DB")],
        relief=[("pressed", "flat"), ("!pressed", "flat")]
    )

    # (opcional) Botão secundário — útil em telas futuras
    style.configure("Prisma.Secondary.TButton",
                    background="#E5E7EB", foreground=TEXT_PRIMARY,
                    font=("Segoe UI", 12), borderwidth=0, padding=(14, 10))
    style.map("Prisma.Secondary.TButton",
              background=[("active", "#DADDE3"), ("pressed", "#CFD3DA")])

    # (opcional) Abas para quando migrarmos o restante
    style.configure("Prisma.TNotebook", background=CARD_BG, borderwidth=0, tabmargins=("8","4","0","0"))
    style.configure("Prisma.TNotebook.Tab", background=CARD_BG, padding=(14,8), font=("Segoe UI", 11))
    style.map("Prisma.TNotebook.Tab",
              foreground=[("selected", TEXT_PRIMARY), ("!selected", TEXT_SECONDARY)],
              background=[("selected", CARD_BG), ("!selected", CARD_BG)])

def card_separator(parent: tk.Widget) -> tk.Frame:
    """Linha separadora sutil para usar dentro do 'card'."""
    return tk.Frame(parent, bg=CARD_BORDER, height=1, bd=0, highlightthickness=0)