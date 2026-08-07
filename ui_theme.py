# ui_theme.py
# Tema moderno (dark) com cor principal #0f4571 para Tkinter/ttk (sem libs externas)

from tkinter import ttk

PRIMARY = "#0f4571"      # cor da marca
PRIMARY_HOVER = "#145a8d"
PRIMARY_ACTIVE = "#0c3656"

BG_APP = "#0b1724"       # fundo da janela (dark)
CARD_BG = "#0f1e2d"      # fundo de painéis/cards
FG_TEXT = "#e6eef5"      # texto principal
FG_MUTED = "#b7c7d8"
GRID_LINE = "#16354d"    # linhas do grid/canvas

ACCENT_GREEN  = "#1ca61c"
ACCENT_ORANGE = "#ff8c00"
ACCENT_RED    = "#ff3b30"

FONT_BASE = ("Segoe UI", 12)
FONT_SEMI = ("Segoe UI Semibold", 12)
FONT_TITLE = ("Segoe UI", 16, "bold")

def apply_theme(root):
    root.configure(bg=BG_APP)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Base
    style.configure(
        ".", background=BG_APP, foreground=FG_TEXT,
        fieldbackground=CARD_BG, font=FONT_BASE
    )

    # Frames
    style.configure("App.TFrame", background=BG_APP)
    style.configure("Card.TFrame", background=CARD_BG)
    style.configure("Toolbar.TFrame", background=PRIMARY)
    style.configure("Status.TFrame", background="#0b1a29")

    # Labels
    style.configure("TLabel", background=BG_APP, foreground=FG_TEXT, font=FONT_BASE)
    style.configure("Muted.TLabel", background=BG_APP, foreground=FG_MUTED)
    style.configure("Section.TLabel", background=BG_APP, foreground=FG_TEXT, font=FONT_SEMI)
    style.configure("Toolbar.TLabel", background=PRIMARY, foreground="white", font=("Segoe UI Semibold", 11))
    style.configure("Title.TLabel", background=BG_APP, foreground=FG_TEXT, font=FONT_TITLE)

    # Buttons
    style.configure("TButton", background="#14324a", foreground=FG_TEXT, borderwidth=0, padding=(12, 8))
    style.map("TButton", background=[("active", "#1a4061"), ("pressed", "#173853")])

    style.configure("Primary.TButton", background=PRIMARY, foreground="white", borderwidth=0, padding=(14, 8))
    style.map("Primary.TButton", background=[("active", PRIMARY_HOVER), ("pressed", PRIMARY_ACTIVE)])

    style.configure("Ghost.TButton", background=BG_APP, foreground=FG_TEXT, borderwidth=0, padding=(8, 6))
    style.map("Ghost.TButton", background=[("active", "#0e2133")])

    # Combobox
    style.configure("TCombobox",
        fieldbackground=CARD_BG, background=CARD_BG, foreground=FG_TEXT,
        arrowcolor=FG_TEXT, bordercolor=PRIMARY)
    style.map("TCombobox",
        fieldbackground=[("readonly", CARD_BG), ("focus", "#122538")],
        bordercolor=[("focus", PRIMARY)],
        arrowcolor=[("active", "white")])

    # Treeview
    style.configure("Treeview",
        background=CARD_BG, fieldbackground=CARD_BG, foreground=FG_TEXT,
        bordercolor=GRID_LINE, lightcolor=GRID_LINE, darkcolor=GRID_LINE,
        rowheight=30)
    style.configure("Treeview.Heading", background=PRIMARY, foreground="white", borderwidth=0, relief="flat")
    style.map("Treeview", background=[("selected", "#164a79")], foreground=[("selected", "white")])

    # Scrollbar
    style.configure("Vertical.TScrollbar", background="#0c2236", troughcolor="#0c2236",
                    bordercolor=GRID_LINE, arrowcolor=FG_TEXT)
    style.map("Vertical.TScrollbar", background=[("active", "#12304a")])

    # Separators
    style.configure("TSeparator", background=GRID_LINE)

    # Retorna paleta caso queira usar diretamente
    return {
        "PRIMARY": PRIMARY,
        "BG_APP": BG_APP,
        "CARD_BG": CARD_BG,
        "FG_TEXT": FG_TEXT,
        "FG_MUTED": FG_MUTED,
        "GRID_LINE": GRID_LINE,
        "GREEN": ACCENT_GREEN,
        "ORANGE": ACCENT_ORANGE,
        "RED": ACCENT_RED,
    }