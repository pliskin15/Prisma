# visualizacao_depositos.py
# Visualização de Depósitos × Memorando (100% Supabase, sem SQLite)
# Autor: João/PMZ + ajustes Copilot
# Requisitos: supabase_config.supabase, (opcional) controle.MESES_PTBR

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, date
import calendar

# === Supabase client (mesmo do seu projeto) ===
from supabase_config import supabase

# === Meses em pt-BR (tenta importar do seu controle; se não achar, usa fallback local) ===
try:
    from controle import MESES_PTBR as _MESES
except Exception:
    _MESES = [
        "Janeiro","Fevereiro","Março","Abril","Maio","Junho",
        "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"
    ]

# === Cores/tema simples (ttk) ===
BG_APP = "#0f1b23"
BG_CARD = "#122330"
BG_HEAD = "#0e1a22"
TXT_MAIN = "#e6eef3"
TXT_MUTED = "#9fb2bf"
ACCENT = "#00bfff"
OK = "#1ca61c"
WARN = "#ff8c00"
BAD = "#c9362b"
GRID_LINE = "#2a3a45"

CELL_RED = "#c94a3a"     # sem memorando
CELL_GREEN = "#1aa35e"   # dif == 0
CELL_ORANGE = "#f39c12"  # dif != 0
CELL_BORDER = "#20303a"

def _fmt_valor_brl(v):
    try:
        v = float(v)
    except:
        v = 0.0
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def _sort_numeric_first(x: str):
    sx = str(x)
    return (0, int(sx)) if sx.isdigit() else (1, sx.lower())

def _setup_styles(root: tk.Misc):
    style = ttk.Style(root)
    # tenta tema existente
    try:
        if "azure" in style.theme_names():
            style.theme_use("azure")
    except Exception:
        pass

    # Base frames
    style.configure("App.TFrame", background=BG_APP)
    style.configure("Toolbar.TFrame", background=BG_HEAD)
    style.configure("Status.TFrame", background=BG_HEAD)
    style.configure("Toolbar.TLabel", background=BG_HEAD, foreground=TXT_MAIN, font=("Segoe UI", 10, "bold"))
    style.configure("Section.TLabel", background=BG_APP, foreground=TXT_MAIN, font=("Segoe UI", 10, "bold"))
    style.configure("Muted.TLabel", background=BG_HEAD, foreground=TXT_MUTED, font=("Segoe UI", 9))

    # Botão "fantasma" (flat)
    style.configure("Ghost.TButton",
                    background=BG_HEAD, foreground=TXT_MAIN,
                    padding=(8, 4), borderwidth=0, focusthickness=0)
    style.map("Ghost.TButton",
              background=[("active", "#18303e")],
              foreground=[("active", TXT_MAIN)])

class VisualizacaoDepositosWindow(tk.Toplevel):
    """
    Janela de visualização:
    - Se 'lojas_filter' for fornecido (lista[str]), a combobox Regional vira 'Minhas Lojas' (desabilitada)
      e a grade mostra somente essas lojas.
    - Caso contrário, mostra por Regional, buscando lojas em Supabase.public.lojas.
    - Depósitos: Supabase.public.depositos_agg  (valor_total em centavos)
    - Memorandos: Supabase.public.memorandos    (valor_remessa_dinheiro em centavos)
    """
    def __init__(self, master=None, lojas_filter=None, mes=None, ano=None, titulo_extra=None):
        super().__init__(master)
        self.title("Visualização — Depósitos por Loja" + (f" {titulo_extra}" if titulo_extra else ""))
        try:
            self.state("zoomed")
        except Exception:
            self.geometry("1200x680")

        _setup_styles(self)

        # --- estado principal
        self._lojas_custom = list(map(str, lojas_filter)) if lojas_filter else None
        self._lojas: list[str] = []
        self._dias_mes = 31
        # soma por (loja, dia) -> int (centavos)
        self._sum_idx: dict[tuple[str,int], int] = {}
        # breakdown por (loja, dia) -> list[(cofre, valor_centavos)]
        self._breakdown: dict[tuple[str,int], list[tuple[str,int]]] = {}
        # memorando por (loja, dia) -> int (centavos); ausente=sem memorando
        self._mem_por_dia: dict[tuple[str,int], int] = {}
        self._draw_job = None

        # --- toolbar
        bar = ttk.Frame(self, style="Toolbar.TFrame")
        bar.pack(fill="x")
        ttk.Label(bar, text="Visualização de Depósitos por Loja", style="Toolbar.TLabel").pack(side="left", padx=12, pady=8)
        ttk.Button(bar, text="⟳ Atualizar", style="Ghost.TButton", command=self._refresh_data)\
            .pack(side="right", padx=10, pady=6)

        # --- filtros
        top = ttk.Frame(self, style="App.TFrame")
        top.pack(fill="x", padx=14, pady=(10, 6))

        ano_atual = datetime.now().year
        self.var_mes = tk.StringVar(value=mes if mes else _MESES[datetime.now().month-1])
        self.var_ano = tk.StringVar(value=str(ano if ano else ano_atual))

        ttk.Label(top, text="Regional:", style="Section.TLabel").pack(side="left")
        self.var_regional = tk.StringVar(value="Minhas Lojas" if self._lojas_custom else "Todas")
        self.cbo_regional = ttk.Combobox(top, textvariable=self.var_regional, width=22,
                                         state=("disabled" if self._lojas_custom else "readonly"))
        self.cbo_regional.pack(side="left", padx=(6,16))

        ttk.Label(top, text="Mês:", style="Section.TLabel").pack(side="left")
        self.cbo_mes = ttk.Combobox(top, textvariable=self.var_mes, values=_MESES, state="readonly", width=14)
        self.cbo_mes.pack(side="left", padx=(6,16))

        ttk.Label(top, text="Ano:", style="Section.TLabel").pack(side="left")
        self.cbo_ano = ttk.Combobox(top, textvariable=self.var_ano,
                                    values=[str(a) for a in range(ano_atual-2, ano_atual+3)],
                                    state="readonly", width=8)
        self.cbo_ano.pack(side="left", padx=(6,16))

        # reatividade
        self.var_mes.trace_add("write", lambda *_: self._refresh_data())
        self.var_ano.trace_add("write", lambda *_: self._refresh_data())
        if not self._lojas_custom:
            self.var_regional.trace_add("write", lambda *_: self._refresh_data())

        # --- canvas da grade
        self._canvas = tk.Canvas(self, bg=BG_APP, highlightthickness=0, bd=0, cursor="")
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", self._on_resize)

        # --- status
        status = ttk.Frame(self, style="Status.TFrame")
        status.pack(fill="x", side="bottom")
        self.lbl_status = ttk.Label(status, text="Última atualização: —", style="Muted.TLabel")
        self.lbl_status.pack(side="left", padx=12, pady=6)

        # carrega regionais (se modo regional)
        if not self._lojas_custom:
            self._populate_regionais()

        # primeira carga
        self._refresh_data()

    # ------------------------------
    # Carregadores (Supabase)
    # ------------------------------
    def _populate_regionais(self):
        """Carrega lista de regionais distintas da tabela 'lojas'."""
        try:
            res = supabase.table("lojas").select("regional").execute()
            regs = sorted({(row.get("regional") or "OUTROS") for row in (res.data or [])})
            values = ["Todas"] + regs
            self.cbo_regional["values"] = values
            if self.var_regional.get() not in values:
                self.var_regional.set("Todas")
        except Exception as e:
            print("[Regionais] Erro:", e)
            self.cbo_regional["values"] = ["Todas"]
            self.var_regional.set("Todas")

    def _load_lojas(self):
        """Monta self._lojas conforme filtro atual."""
        if self._lojas_custom is not None:
            self._lojas = sorted(self._lojas_custom, key=_sort_numeric_first)
            return

        try:
            sel = self.var_regional.get()
            if sel == "Todas":
                q = supabase.table("lojas").select("loja")
            else:
                q = supabase.table("lojas").select("loja").eq("regional", sel)
            rows = q.execute().data or []
            lojas = [str(r["loja"]) for r in rows if r.get("loja")]
            self._lojas = sorted(lojas, key=_sort_numeric_first)
        except Exception as e:
            print("[Lojas] Erro:", e)
            self._lojas = []

    def _preload_memorandos_mes(self, ano: int, mes_num: int):
        """Pré-carrega todos os memorandos do mês, opcionalmente filtrando por lojas."""
        self._mem_por_dia.clear()
        try:
            last_day = calendar.monthrange(ano, mes_num)[1]
            ini = f"{ano}-{mes_num:02d}-01"
            fim = f"{ano}-{mes_num:02d}-{last_day:02d}"

            q = supabase.table("memorandos")\
                        .select("data, loja, valor_remessa_dinheiro")\
                        .gte("data", ini).lte("data", fim)

            if self._lojas:
                q = q.in_("loja", [str(l) for l in self._lojas])

            res = q.execute()
            rows = res.data or []

            for r in rows:
                try:
                    loja = str(r["loja"])
                    dt = datetime.strptime(r["data"], "%Y-%m-%d")
                    dia = dt.day
                    val = int(r.get("valor_remessa_dinheiro") or 0)
                    self._mem_por_dia[(loja, dia)] = self._mem_por_dia.get((loja, dia), 0) + val
                except Exception:
                    continue
        except Exception as e:
            print("[Memorandos] Erro:", e)

    def _load_mes_depositos(self, ano: int, mes_num: int):
        """Carrega depósitos agregados do mês, por lojas visíveis."""
        self._sum_idx.clear()
        self._breakdown.clear()
        try:
            last_day = calendar.monthrange(ano, mes_num)[1]
            ini = f"{ano}-{mes_num:02d}-01"
            fim = f"{ano}-{mes_num:02d}-{last_day:02d}"

            q = supabase.table("depositos_agg")\
                        .select("data, loja, numero_cofre, valor_total")\
                        .gte("data", ini).lte("data", fim)

            if self._lojas:
                q = q.in_("loja", [str(l) for l in self._lojas])

            res = q.execute()
            rows = res.data or []

            for r in rows:
                try:
                    loja = str(r["loja"])
                    dt = datetime.strptime(r["data"], "%Y-%m-%d")
                    dia = dt.day
                    cofre = str(r["numero_cofre"])
                    val = int(r.get("valor_total") or 0)
                    key = (loja, dia)
                    self._sum_idx[key] = self._sum_idx.get(key, 0) + val
                    self._breakdown.setdefault(key, []).append((cofre, val))
                except Exception:
                    continue

            self._dias_mes = last_day
        except Exception as e:
            print("[Depósitos] Erro:", e)
            self._dias_mes = 31

    # ------------------------------
    # Atualização / desenho
    # ------------------------------
    def _refresh_data(self):
        try:
            # captura filtros
            mes_nome = self.var_mes.get()
            ano = int(self.var_ano.get())
            if mes_nome not in _MESES:
                return
            mes_num = _MESES.index(mes_nome) + 1

            self._load_lojas()
            if not self._lojas:
                self._mem_por_dia.clear()
                self._sum_idx.clear()
                self._breakdown.clear()
                self._dias_mes = calendar.monthrange(ano, mes_num)[1]
                self._redraw()
                self.lbl_status.configure(text=f"Última atualização: {datetime.now():%H:%M:%S} · Lojas: 0")
                return

            self._preload_memorandos_mes(ano, mes_num)
            self._load_mes_depositos(ano, mes_num)
            self._redraw()
            self.lbl_status.configure(
                text=f"Última atualização: {datetime.now():%H:%M:%S} · Lojas: {len(self._lojas)}"
            )
        except Exception as e:
            messagebox.showerror("Visualização", f"Falha ao atualizar:\n{e}")

    def _on_resize(self, _evt):
        if self._draw_job:
            self.after_cancel(self._draw_job)
        self._draw_job = self.after(120, self._redraw)

    def _redraw(self):
        c = self._canvas
        c.delete("all")

        W = c.winfo_width() or 800
        H = c.winfo_height() or 600

        # margens
        PADX = 12
        PADY = 10

        # medidas (coluna Loja + dias)
        W_LOJA = 120
        H_HEAD = 36
        GAP = 2

        x0 = PADX
        y0 = PADY

        # cabeçalho fundo
        c.create_rectangle(0, 0, W, H_HEAD + PADY*0, fill=BG_HEAD, width=0)

        # títulos
        c.create_text(x0 + W_LOJA/2, H_HEAD/2, text="Loja", fill=TXT_MAIN, font=("Segoe UI", 10, "bold"))
        # dias
        if self._dias_mes <= 0:
            return
        col_w = (W - (x0 + W_LOJA) - PADX) / self._dias_mes
        col_w = max(26, col_w)

        for d in range(1, self._dias_mes + 1):
            x = x0 + W_LOJA + (d-1) * col_w
            c.create_text(x + col_w/2, H_HEAD/2, text=str(d),
                          fill=TXT_MAIN, font=("Segoe UI", 9, "bold"))

        # linhas por loja
        if not self._lojas:
            # mensagem de vazio
            c.create_text(W/2, H/2, text="Sem lojas neste filtro.",
                          fill=TXT_MUTED, font=("Segoe UI", 11))
            return

        n_rows = len(self._lojas)
        usable_h = H - H_HEAD - PADY*1.2
        row_h = max(22, min(34, int((usable_h - (n_rows - 1)*GAP) / max(1, n_rows))))

        for i, loja in enumerate(self._lojas):
            y = H_HEAD + PADY + i*(row_h + GAP)
            # rótulo loja
            c.create_text(x0 + W_LOJA/2, y + row_h/2, text=str(loja),
                          fill=TXT_MAIN, font=("Segoe UI", 9))
            # células por dia
            for d in range(1, self._dias_mes + 1):
                x = x0 + W_LOJA + (d-1) * col_w
                key = (str(loja), d)
                soma = self._sum_idx.get(key, 0)
                memo = self._mem_por_dia.get(key, None)  # None=sem memorando
                # cor
                if memo is None:
                    fill = CELL_RED
                else:
                    fill = CELL_GREEN if int(soma) == int(memo) else CELL_ORANGE

                rect = c.create_rectangle(x, y, x + col_w - 1, y + row_h,
                                          fill=fill, outline=CELL_BORDER, width=1)
                # bind de clique para detalhes
                c.tag_bind(rect, "<Button-1>", lambda _e, lj=str(loja), dd=d: self._abrir_detalhes(lj, dd))

    # ------------------------------
    # Detalhes por célula
    # ------------------------------
    def _abrir_detalhes(self, loja: str, dia: int):
        mes_nome = self.var_mes.get()
        ano = int(self.var_ano.get())
        mes_num = _MESES.index(mes_nome) + 1
        total_dep = int(self._sum_idx.get((loja, dia), 0))
        memo = self._mem_por_dia.get((loja, dia), None)
        dif = None if memo is None else (total_dep - int(memo))
        breakdown = self._breakdown.get((loja, dia), [])

        win = tk.Toplevel(self)
        win.title(f"Detalhes — Loja {loja} · {dia:02d}/{mes_num:02d}/{ano}")
        win.configure(bg=BG_APP)
        try:
            win.grab_set()
        except Exception:
            pass

        head = tk.Frame(win, bg=BG_HEAD)
        head.pack(fill="x")
        tk.Label(head, text=f"Loja {loja}", bg=BG_HEAD, fg=TXT_MAIN, font=("Segoe UI", 11, "bold"))\
            .pack(side="left", padx=10, pady=8)
        tk.Label(head, text=f"Dia {dia:02d}/{mes_num:02d}/{ano}", bg=BG_HEAD, fg=TXT_MUTED, font=("Segoe UI", 9))\
            .pack(side="left", padx=10, pady=8)

        body = tk.Frame(win, bg=BG_APP)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        # totais
        tk.Label(body, text="Depósitos do dia:", bg=BG_APP, fg=TXT_MUTED).grid(row=0, column=0, sticky="w")
        tk.Label(body, text=_fmt_valor_brl(total_dep), bg=BG_APP, fg=TXT_MAIN, font=("Segoe UI", 11, "bold"))\
            .grid(row=0, column=1, sticky="w", padx=(8,0))

        tk.Label(body, text="Memorando:", bg=BG_APP, fg=TXT_MUTED).grid(row=1, column=0, sticky="w", pady=(4,0))
        tk.Label(body, text=("—" if memo is None else _fmt_valor_brl(int(memo))),
                 bg=BG_APP, fg=TXT_MAIN, font=("Segoe UI", 11, "bold"))\
            .grid(row=1, column=1, sticky="w", padx=(8,0), pady=(4,0))

        tk.Label(body, text="Diferença:", bg=BG_APP, fg=TXT_MUTED).grid(row=2, column=0, sticky="w", pady=(4,6))
        if dif is None:
            dif_txt, dif_fg = "—", TXT_MAIN
        else:
            dif_txt = _fmt_valor_brl(dif)
            dif_fg = OK if dif == 0 else CELL_ORANGE
        tk.Label(body, text=dif_txt, bg=BG_APP, fg=dif_fg, font=("Segoe UI", 12, "bold"))\
            .grid(row=2, column=1, sticky="w", padx=(8,0), pady=(4,6))

        # breakdown por cofres
        tk.Label(body, text="Por cofres:", bg=BG_APP, fg=TXT_MUTED).grid(row=3, column=0, sticky="nw", pady=(4,0))
        box = tk.Frame(body, bg=BG_CARD)
        box.grid(row=3, column=1, sticky="nsew", padx=(8,0), pady=(4,0))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(3, weight=1)

        if not breakdown:
            tk.Label(box, text="(nenhum depósito encontrado para este dia)",
                     bg=BG_CARD, fg=TXT_MUTED).pack(anchor="w", padx=8, pady=6)
        else:
            for cofre, val in sorted(breakdown, key=lambda x: (x[0], x[1])):
                tk.Label(box, text=f"• Cofre {cofre}: {__fmt_valor_brl(val)}",
                         bg=BG_CARD, fg=TXT_MAIN).pack(anchor="w", padx=8, pady=2)

        # rodapé
        foot = tk.Frame(win, bg=BG_APP)
        foot.pack(fill="x", pady=(6,10))
        tk.Button(foot, text="Fechar", command=win.destroy,
                  bg="#2a3a45", fg=TXT_MAIN, relief="flat", bd=0, padx=12, pady=6)\
            .pack(side="right", padx=10)

# ============================
# Fim do arquivo
# ============================