import tkinter as tk
from tkinter import messagebox
import json, os, re
from collections import defaultdict
from datetime import datetime
import calendar
import numpy as np
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet
from tkinter import filedialog
from supabase_config import supabase
from visualizacao_depositos import VisualizacaoDepositosWindow
from pathlib import Path
import sys
_here = Path(__file__).parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))


MAPA_REGIONAIS = {
    "AMAZONAS": "AM", "AM": "AM",
    "RORAIMA": "RR", "RR": "RR",
    "PARÁ": "PA", "PA": "PA",
    "MARANHÃO": "MA", "MA": "MA",
    "MATO GROSSO": "MT", "MT": "MT",
    "AMAPÁ": "AP", "AP": "AP"
}


# === NOVAS CONSTANTES PARA ANÁLISE DE VALES ===
ARQUIVO_VALES = "analise_vales.json"
REGIONAIS_VALES = ["AM", "AP", "MA", "MT", "PA", "RR"]


import re

def normalizar_loja_valor(v):
    """Converte qualquer valor de 'Loja' (str/int/float) para string numérica limpa."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()

def normalizar_regional(nome):
    if not nome:
        return "OUTROS"
    nome = nome.strip().upper()
    return MAPA_REGIONAIS.get(nome, "OUTROS")


# === LOJAS QUE ABREM AOS DOMINGOS (apenas para não preencher "Domingo" na planilha) ===
LOJAS_ABREM_DOMINGO = {"32", "35", "40", "43", "86"}


STATUS_OPCOES = [
    "Finalizado", "Domingo", "Atenção", "Falta no caixa", "Não iniciada", "Comprovante GoodCard", "Sem documentação"
]

CORES_STATUS = {
    "Finalizado": "#1ca61c", "Domingo": "#000000", "Atenção": "#ff8c00",
    "Falta no caixa": "#6a0dad", "Não iniciada": "#ffc0cb", "Comprovante GoodCard": "#00ffaa", "Sem documentação": "#ff0000"
}

MESES_PTBR = [
    "Janeiro","Fevereiro","Março","Abril","Maio","Junho",
    "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"
]

BG_DARK = "#1e1e1e"
BG_PANEL = "#2e2e2e"
FG_TEXT = "#ffffff"
FG_ACTIVE = "#00bfff"
FONT_UI = ("Segoe UI", 10)

ARQUIVO_PERFIS = "perfis.json"
ARQUIVO_LOJAS = "lojas.json"
REGIONAIS_ORDENADAS = ["AM","AP","MA","MT","PA","RR","OUTROS"]

def cnpj_ultimos4(cnpj_str):
    numeros = re.sub(r'\D', '', cnpj_str or "")
    return numeros[-4:] if len(numeros) >= 4 else numeros


import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta
from unidecode import unidecode
import math, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from io import BytesIO
from PIL import Image, ImageTk

# >>> Severidade de Pendências (novo) <<<
# Nível 3 (crítico): Falta no caixa, Sem documentação
# Nível 2 (médio): Comprovante GoodCard
# Nível 1 (baixo): Atenção, Não iniciada
PEND_SEVERIDADE = {
    "FALTA NO CAIXA": 3,
    "SEM DOCUMENTACAO": 3,
    "COMPROVANTE GOODCARD": 2,
    "ATENCAO": 1,
    "NAO INICIADA": 1,
}
CORES_NIVEL_PEND = {3: "#E53935", 2: "#FDD835", 1: "#1E88E5"}  # vermelho / amarelo / azul

STATUS_FINAIS = {"FINALIZADO", "DOMINGO", ""}

def _norm_txt(s: str) -> str:
    if s is None: return ""
    s = unidecode(str(s)).strip().upper()
    s = s.replace("SEM DOCUMENTAÇÃO", "SEM DOCUMENTACAO")
    s = s.replace("ATENÇÃO", "ATENCAO")
    s = s.replace("NÃO INICIADA", "NAO INICIADA")
    return s

def _nivel_pendencia(rot_norm: str) -> int | None:
    return PEND_SEVERIDADE.get(rot_norm)

def _somar_dias_ignorando_domingo(data_inicial: datetime, dias: int) -> datetime:
    # Mesma regra do app (D+N ignorando domingos)  → consistente com sua UI atual.
    # (Este helper local evita dependência de métodos de instância)  [1](https://pmz365-my.sharepoint.com/personal/joao_teles_grupopmz_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/prisma.py)
    data = data_inicial; add = 0
    while add < dias:
        data += timedelta(days=1)
        if data.weekday() != 6:  # 6=domingo
            add += 1
    return data

def _ultimo_dia_mes(ano: int, mes_num: int) -> int:
    import calendar
    return calendar.monthrange(ano, mes_num)[1]


def _dia_valido_por_vigencia(loja_dict: dict, ano: int, mes_num: int, d: int) -> bool:
    # Sanear vigência para [1..dias_mes]; se inválida, considerar mês inteiro
    dias_mes = _ultimo_dia_mes(ano, mes_num)
    v = (loja_dict or {}).get("vigencia") or {}
    try:
        ini = int(v.get("ini", 1) or 1)
        fim = int(v.get("fim", dias_mes) or dias_mes)
    except Exception:
        ini, fim = 1, dias_mes

    # CLAMP + fallback quando 'fim < ini'
    if ini < 1: ini = 1
    if fim > dias_mes: fim = dias_mes
    if fim < ini:
        # vigência invertida/zerada → usa mês inteiro como fallback
        ini, fim = 1, dias_mes

    return ini <= int(d) <= fim

def _classificar_dia(loja_dict: dict, ano: int, mes_num: int, d: int, prazo_dias: int):
    """
    Retorna:
      categoria_4: 'sem_pend_dentro'|'sem_pend_fora'|'com_pend_dentro'|'com_pend_fora'|None
      pendencia_principal: rótulo (ou None)
      nivel: 3|2|1|None
      flags: dict
    Regras: vigência, domingo fora quando não finalizado, D+N ignorando domingo,
    logs 'manual' apenas (GoodCard +7 automático não conta para 'primeira ação').  [1](https://pmz365-my.sharepoint.com/personal/joao_teles_grupopmz_com_br/Documents/Arquivos%20de%20Microsoft%20Copilot%20Chat/prisma.py)
    """
    import calendar
    dias = (loja_dict.get("dias") or {})
    meta = (loja_dict.get("meta") or {})
    dia_str = str(d)
    status_final = _norm_txt(dias.get(dia_str, ""))

    if calendar.weekday(ano, mes_num, d) == 6 and status_final != "FINALIZADO":
        return None, None, None, {"ignorado": "domingo_nao_finalizado"}

    logs = (meta.get(dia_str, {}).get("logs") or [])
    logs_manuais = [l for l in logs if str(l.get("origem","")).lower() == "manual"]

    base = datetime(ano, mes_num, d)
    limite = _somar_dias_ignorando_domingo(base, prazo_dias).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )

    def _ts(x):
        try: return datetime.strptime(x, "%Y-%m-%d %H:%M:%S")
        except: return None

    finalizado_ts = None
    if status_final == "FINALIZADO":
        ts_txt = (meta.get(dia_str, {}) or {}).get("finalizado_ts")
        finalizado_ts = _ts(ts_txt) if ts_txt else None
        if not finalizado_ts:
            return None, None, None, {"ignorado":"finalizado_sem_ts"}

    logs_manuais_sorted = sorted(logs_manuais, key=lambda l: _ts(l.get("data_hora","")) or datetime.min)
    houve_pend_previa = False
    pend_princ = None; nivel = None

    if logs_manuais_sorted:
        for l in logs_manuais_sorted:
            st = _norm_txt(l.get("status","")); ts = _ts(l.get("data_hora",""))
            if st in STATUS_FINAIS: continue
            if ts and ts <= limite: houve_pend_previa = True
            pend_princ = st  # último status pendente antes do finalizado
            n = _nivel_pendencia(st)
            if n is not None: nivel = n

    if status_final != "FINALIZADO":
        return None, pend_princ, nivel, {"ignorado":"nao_finalizado"}

    if not finalizado_ts:
        return None, pend_princ, nivel, {"ignorado":"finalizado_sem_ts"}

    dentro = (finalizado_ts <= limite)
    if houve_pend_previa:
        cat = "com_pend_dentro" if dentro else "com_pend_fora"
    else:
        cat = "sem_pend_dentro" if dentro else "sem_pend_fora"

    return cat, pend_princ, nivel, {}


def _agregar(perfis_docs: list, mes: str, ano: int, incluir_goodcard=True, reg_filtro=None):
    """
    Agrega métricas (geral/regional/loja) aplicando:
    - Vigência por loja no perfil (denominador).
    - Domingos fora do denominador (a menos que FINALIZADO).
    - Classificação D+N (ignora domingos) para dentro/fora do prazo.
    - Logs manuais para pendência principal e severidade.
    - GoodCard vencidos (opcional).
    """
    from collections import Counter
    from controle import MESES_PTBR, normalizar_regional
    import calendar

    def _regional_from_doc(loja_dict: dict, perfil_doc: dict) -> str:
        nome_loja = str((loja_dict or {}).get('loja') or '').strip()
        r = (loja_dict or {}).get('regional')
        if r not in (None, '', ' '):
            return normalizar_regional(r)
        for lj in (perfil_doc or {}).get('lojas', []) or []:
            if str(lj.get('loja') or '').strip() == nome_loja:
                return normalizar_regional(lj.get('regional'))
        return 'OUTROS'

    mes_num = MESES_PTBR.index(mes) + 1
    def _ultimo_dia_mes(a: int, m: int) -> int:
        return calendar.monthrange(a, m)[1]
    dias_mes = _ultimo_dia_mes(ano, mes_num)
    base_mes_const = sum(1 for _d in range(1, dias_mes + 1) if calendar.weekday(ano, mes_num, _d) != 6)
    geral = {
        'dias_validos': 0, 'finalizados': 0,
        'sem_pend_dentro': 0, 'sem_pend_fora': 0,
        'com_pend_dentro': 0, 'com_pend_fora': 0,
        'goodcard_vencidos': 0,
    }
    regionais = {}
    lojas = {}
    pend_geral = Counter()
    pend_geral_nivel = Counter()

    def _init_reg(reg):
        if reg not in regionais:
            regionais[reg] = {
                'dias_validos': 0, 'finalizados': 0,
                'sem_pend_dentro': 0, 'sem_pend_fora': 0,
                'com_pend_dentro': 0, 'com_pend_fora': 0,
                'pend_por_rotulo': Counter(), 'pend_por_nivel': Counter(),
                'goodcard_vencidos': 0
            }

    def _init_loja(reg, nome):
        if (reg, nome) not in lojas:
            from collections import Counter as _C
            lojas[(reg, nome)] = {
                'dias_validos': 0, 'finalizados': 0,
                'sem_pend_dentro': 0, 'sem_pend_fora': 0,
                'com_pend_dentro': 0, 'com_pend_fora': 0,
                'pend_por_rotulo': _C(), 'pend_por_nivel': _C(),
                'goodcard_vencidos': 0
            }

    PRAZO_DIAS_DEFAULT = 2

    for perfil in (perfis_docs or []):
        if str(perfil.get('mes', '')).strip() != f"{mes}/{ano}":
            continue

        lojas_decl = perfil.get('lojas') or []
        planilha = perfil.get('planilha') or []
        def _nome(x):
            return str((x or {}).get('loja') or '').strip()
        idx_plan = { _nome(p): p for p in planilha if _nome(p) }
        entradas = []
        for lj in lojas_decl:
            nome = _nome(lj)
            if not nome:
                continue
            base = { 'loja': nome, 'cnpj': lj.get('cnpj', ''), 'regional': lj.get('regional'), 'dias': {}, 'meta': {} }
            p = idx_plan.get(nome)
            if p:
                for k in ('dias', 'meta', 'vigencia', 'regional', 'cnpj'):
                    if p.get(k) not in (None, '', {}):
                        base[k] = p.get(k)
            entradas.append(base)
        for nome, p in idx_plan.items():
            if not any(e['loja'] == nome for e in entradas):
                entradas.append(p)

        def _normalize_loja_maps(e: dict):
            dm = e.get('dias') or {}
            if isinstance(dm, dict) and any(not isinstance(k, str) for k in dm.keys()):
                try:
                    e['dias'] = {str(k): v for k, v in dm.items()}
                except Exception:
                    pass
            mm = e.get('meta') or {}
            if isinstance(mm, dict) and any(not isinstance(k, str) for k in mm.keys()):
                try:
                    e['meta'] = {str(k): v for k, v in mm.items()}
                except Exception:
                    pass
            v = (e.get('vigencia') or {})
            try:
                _ini = int(v.get('ini', 1) or 1)
                _fim = int(v.get('fim', dias_mes) or dias_mes)
            except Exception:
                _ini, _fim = 1, dias_mes
            if _ini < 1: _ini = 1
            if _fim > dias_mes: _fim = dias_mes
            if _fim < _ini: _ini, _fim = 1, dias_mes
            e['vigencia'] = {'ini': _ini, 'fim': _fim}

        for e in entradas:
            _normalize_loja_maps(e)

        for loja in entradas:
            reg = _regional_from_doc(loja, perfil)
            if reg_filtro and reg != reg_filtro:
                continue
            nome = str((loja or {}).get('loja', '')).strip()
            if not nome:
                continue
            _init_reg(reg)
            _init_loja(reg, nome)

            v = (loja.get('vigencia') or {})
            try:
                ini = int(v.get('ini', 1) or 1)
                fim = int(v.get('fim', dias_mes) or dias_mes)
            except Exception:
                ini, fim = 1, dias_mes
            if ini < 1: ini = 1
            if fim > dias_mes: fim = dias_mes
            if fim < ini: ini, fim = 1, dias_mes
            loja['vigencia'] = {'ini': ini, 'fim': fim}
            def _in_vig(d_int: int) -> bool:
                return ini <= int(d_int) <= fim

            
            vig_atual = int(max(0, fim - ini + 1))
            lojas[(reg, nome)]['dias_planejados'] = lojas[(reg, nome)].get('dias_planejados', 0) + vig_atual  # soma vigências de perfis (troca de auditor)
            lojas[(reg, nome)]['dias_vigencia_total'] = lojas[(reg, nome)].get('dias_vigencia_total', 0) + vig_atual
            lojas[(reg, nome)]['base_mes'] = base_mes_const


            for d in range(1, dias_mes + 1):
                if not _in_vig(d):
                    continue
                dia_str = str(d)
                dias_map = loja.get('dias') or {}
                status_raw = dias_map.get(dia_str)
                if status_raw is None:
                    status_raw = dias_map.get(d)
                raw_status = status_raw if status_raw is not None else ''
                status_norm = _norm_txt(raw_status)

                if calendar.weekday(ano, mes_num, d) == 6 and status_norm != 'FINALIZADO':
                    continue
                if status_norm == 'DOMINGO':
                    continue

                lojas[(reg, nome)]['dias_validos'] += 1
                regionais[reg]['dias_validos'] += 1
                geral['dias_validos'] += 1

                cat, pend, niv, _ = _classificar_dia(loja, ano, mes_num, d, prazo_dias=PRAZO_DIAS_DEFAULT)
                if cat is None:
                    continue
                lojas[(reg, nome)]['finalizados'] += 1
                regionais[reg]['finalizados'] += 1
                geral['finalizados'] += 1
                lojas[(reg, nome)][cat] = lojas[(reg, nome)].get(cat, 0) + 1
                regionais[reg][cat] = regionais[reg].get(cat, 0) + 1
                geral[cat] = geral.get(cat, 0) + 1
                if pend:
                    pend_geral[pend] += 1
                    regionais[reg]['pend_por_rotulo'][pend] += 1
                    lojas[(reg, nome)]['pend_por_rotulo'][pend] += 1
                    if niv:
                        regionais[reg]['pend_por_nivel'][niv] += 1
                        lojas[(reg, nome)]['pend_por_nivel'][niv] += 1

            if incluir_goodcard:
                meta = (loja.get('meta') or {})
                from datetime import datetime
                for dia_k, info in meta.items():
                    try:
                        if not info.get('lembrete_ativo', False):
                            continue
                        prazo_txt = info.get('prazo_gc_fim')
                        if not prazo_txt:
                            continue
                        prazo_dt = datetime.strptime(prazo_txt, '%Y-%m-%d %H:%M:%S')
                        st = _norm_txt((loja.get('dias') or {}).get(str(dia_k), ''))
                        if datetime.now() > prazo_dt and st != 'FINALIZADO':
                            lojas[(reg, nome)]['goodcard_vencidos'] += 1
                            regionais[reg]['goodcard_vencidos'] += 1
                            geral['goodcard_vencidos'] += 1
                    except Exception:
                        pass

    return geral, regionais, lojas, pend_geral, pend_geral_nivel



def _fig_to_photo(fig, size=None):
    """Converte uma Figure em PhotoImage respeitando o tema dark e detalhes brancos."""
    BG = "#1e1e1e"      # fundo
    FG = "#ffffff"      # detalhes/texto/ticks
    GRID = "#3a3a3a"    # grade discreta

    # fundo figura
    fig.patch.set_facecolor(BG)

    # aplica nos eixos
    for ax in fig.get_axes():
        ax.set_facecolor(BG)
        # títulos, rótulos e ticks em branco
        ax.title.set_color(FG)
        ax.xaxis.label.set_color(FG)
        ax.yaxis.label.set_color(FG)
        ax.tick_params(colors=FG)
        # bordas em branco
        for spine in ax.spines.values():
            spine.set_color(FG)
        # grade sutil
        ax.grid(True, color=GRID, alpha=0.6, linewidth=0.6)

        # legenda (se existir): fundo dark, borda branca e texto branco
        leg = ax.get_legend()
        if leg:
            leg.get_frame().set_facecolor(BG)
            leg.get_frame().set_edgecolor(FG)
            for txt in leg.get_texts():
                txt.set_color(FG)
            if leg.get_title() is not None:
                leg.get_title().set_color(FG)

    # exporta
    from io import BytesIO
    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=130, facecolor=BG, edgecolor="none")
    plt.close(fig)

    # converte para PhotoImage
    buf.seek(0)
    from PIL import Image, ImageTk
    img = Image.open(buf)
    if size:
        img = img.resize(size, resample=Image.BICUBIC)
    return ImageTk.PhotoImage(img)

def carregar_perfis_docs():
    """
    Carrega os perfis diretamente do Supabase e retorna a lista de docs JSONB.
    Mantém o formato que a análise precisa: uma lista de dicts (cada 'doc' de perfil).
    """
    try:
        res = supabase.table("perfis").select("doc, mes, usuario_dono").execute()
        rows = res.data or []
        # cada row tem {"doc": {...}, "mes": "Mês/Ano", "usuario_dono": "..."}
        docs = []
        for r in rows:
            doc = r.get("doc") or {}
            # garante que o doc carregado preserve 'mes' e 'usuario_dono' no próprio dict
            doc.setdefault("mes", r.get("mes"))
            doc.setdefault("usuario_dono", r.get("usuario_dono"))
            docs.append(doc)
        return docs
    except Exception as e:
        raise RuntimeError(f"Falha ao carregar perfis do banco: {e}")





def abrir_analise_de_lojas(master):
    """
    Janela Toplevel — Análise → Análise de Lojas

    Abas:
      • Geral     → parâmetros + rosca (com legenda) + Pareto (%, em pé), lado a lado
      • Regionais → empilhado 100% (compacto) + UM Pareto com filtro (tamanho 'congelado')
      • Lojas     → filtro de regional, barras % (com cor por nível) e heatmap

    Regras (iguais à UI atual):
      - Vigência por loja; domingo fora quando não finalizado
      - Prazo D+N (ignora domingos) para "dentro/fora do prazo"
      - Logs 'manuais' para pendência / 'pendência principal'
      - GoodCard vencidos: lembrete_ativo & prazo_gc_fim vencido & não finalizado
    """
    import tkinter as tk
    from tkinter import ttk, messagebox
    import math
    import pandas as pd
    import matplotlib.pyplot as plt
    import seaborn as sns

    # --------------------------
    # 1) Carregar perfis (docs)
    # --------------------------
    try:
        try:
            perfis_docs_all = carregar_perfis_docs()     # preferido
        except NameError:
            perfis_docs_all = carregar_perfis()          # fallback, se ainda existir
    except Exception as e:
        messagebox.showerror("Análise de Lojas", f"Falha ao carregar perfis:\n{e}")
        return


    # ------------------------------
    # 2) Criar janela Toplevel dark
    # ------------------------------
    win = tk.Toplevel(master)
    win.title("Análise de Lojas")
    win.configure(bg="#1e1e1e")
    win.geometry("1200x820")
    try:
        win.grab_set()
    except Exception:
        pass

    # Notebook
    nb = ttk.Notebook(win)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    tab_geral  = tk.Frame(nb, bg="#1e1e1e")
    tab_regs   = tk.Frame(nb, bg="#1e1e1e")
    tab_lojas  = tk.Frame(nb, bg="#1e1e1e")

    nb.add(tab_geral, text="Geral")
    nb.add(tab_regs,  text="Regionais")
    nb.add(tab_lojas, text="Lojas")

    # --------------------------
    # 3) ABA GERAL — Parâmetros
    # --------------------------
    from controle import MESES_PTBR  # já existe no módulo

    meses_unicos = sorted(
        {p.get("mes","") for p in (perfis_docs_all or []) if p.get("mes")},
        key=lambda s: (int(s.split("/")[1]), MESES_PTBR.index(s.split("/")[0])+1)
    ) if perfis_docs_all else []

    mes_var = tk.StringVar(value=meses_unicos[-1] if meses_unicos else "")
    prazo_var = tk.IntVar(value=2)          # D+2 (UI)
    goodcard_var = tk.BooleanVar(value=True)

    barra = tk.Frame(tab_geral, bg="#1e1e1e")
    barra.pack(fill="x", padx=12, pady=(10, 6))

    tk.Label(barra, text="Mês/Ano:", bg="#1e1e1e", fg="#ffffff").pack(side="left")
    cb_mes = ttk.Combobox(barra, textvariable=mes_var, values=meses_unicos, width=20)
    cb_mes.pack(side="left", padx=8)

    tk.Label(barra, text="Prazo (dias):", bg="#1e1e1e", fg="#ffffff").pack(side="left", padx=(16,0))
    tk.Entry(barra, textvariable=prazo_var, width=6, bg="#2e2e2e", fg="#ffffff",
             relief="flat", insertbackground="#ffffff").pack(side="left", padx=8)

    tk.Checkbutton(barra, text="Incluir GoodCard vencidos",
                   variable=goodcard_var, bg="#1e1e1e", fg="#ffffff",
                   selectcolor="#1e1e1e").pack(side="left", padx=(16,0))

    # KPIs
    kpi_lbl = tk.Label(tab_geral, bg="#1e1e1e", fg="#ffffff", font=("Segoe UI", 12))
    kpi_lbl.pack(pady=(6, 6))

    # Linha de gráficos (rosca + pareto geral)
    geral_row_holder = tk.Frame(tab_geral, bg="#1e1e1e")
    geral_row_holder.pack(fill="both", expand=True, padx=10, pady=8)

    # ------------------------------------------------
    # 4) ABA REGIONAIS — widgets fixos + cfg 'lock'
    # ------------------------------------------------
    # Empilhado 100% (compacto)
    regs_100 = tk.Label(tab_regs, bg="#1e1e1e")
    regs_100.pack(pady=(8, 4))

    # Filtro + Pareto único
    reg_ctrl = tk.Frame(tab_regs, bg="#1e1e1e")
    reg_ctrl.pack(fill="x", padx=10, pady=(6, 0))
    tk.Label(reg_ctrl, text="Regional (Pareto):", bg="#1e1e1e", fg="#ffffff").pack(side="left")
    reg_sel_var = tk.StringVar(value="")
    reg_sel_cb  = ttk.Combobox(reg_ctrl, textvariable=reg_sel_var, values=[], width=8)
    reg_sel_cb.pack(side="left", padx=8)

    pareto_reg_lbl = tk.Label(tab_regs, bg="#1e1e1e")
    pareto_reg_lbl.pack(pady=8)

    # Config 'congelada' para manter o mesmo look a cada troca
    pareto_cfg = {
        "locked": False,            # vira True após 1ª render
        "fig_w": 0, "fig_h": 0,     # px
        "dpi": 96,                  # DPI
        "margins": (0.08, 0.98, 0.86, 0.32),  # left, right, top, bottom
        "ylim": (0, 100),           # % fixo
        "tick_rot": 28,
        "labelsize": 10,
        "init_frac_w": 0.92,        # frações da largura da janela (1ª vez)
        "init_frac_h": 0.22,
    }

    # -------------------------
    # 5) ABA LOJAS — widgets UI
    # -------------------------
    top_ctrl = tk.Frame(tab_lojas, bg="#1e1e1e")
    top_ctrl.pack(fill="x", padx=10, pady=(10, 2))
    tk.Label(top_ctrl, text="Regional:", bg="#1e1e1e", fg="#ffffff").pack(side="left")
    reg_var = tk.StringVar(value="")
    ttk.Combobox(top_ctrl, textvariable=reg_var, values=["","AM","AP","MA","MT","PA","RR"], width=6).pack(side="left", padx=6)

    # Guarda a última tabela renderizada (já filtrada/ordenada) para exportação
    _lojas_export_state = {"df": None}

    def _exportar_lojas_xlsx():
        df_exp = _lojas_export_state.get("df")
        if df_exp is None or df_exp.empty:
            messagebox.showinfo("Exportar XLSX", "Não há dados para exportar.")
            return

        sufixo_reg = f"_{reg_var.get().strip()}" if reg_var.get().strip() else ""
        nome_sugerido = f"analise_lojas{sufixo_reg}.xlsx"

        caminho = filedialog.asksaveasfilename(
            title="Exportar tabela de Lojas",
            defaultextension=".xlsx",
            initialfile=nome_sugerido,
            filetypes=[("Arquivo Excel", "*.xlsx")],
        )
        if not caminho:
            return

        try:
            df_fmt = df_exp.copy()
            if "% Finalizados" in df_fmt.columns:
                df_fmt["% Finalizados"] = pd.to_numeric(df_fmt["% Finalizados"], errors="coerce").round(1)

            with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
                df_fmt.to_excel(writer, index=False, sheet_name="Lojas")

                ws = writer.sheets["Lojas"]
                # Largura automática simples por coluna
                for j, col in enumerate(df_fmt.columns, start=1):
                    maior = max([len(str(col))] + [len(str(v)) for v in df_fmt[col].tolist()])
                    ws.column_dimensions[ws.cell(row=1, column=j).column_letter].width = min(40, maior + 2)

            messagebox.showinfo("Exportar XLSX", f"Tabela exportada com sucesso:\n{caminho}")
        except Exception as e:
            messagebox.showerror("Exportar XLSX", f"Falha ao exportar:\n{e}")

    tk.Button(
        top_ctrl, text="📊 Exportar XLSX", command=_exportar_lojas_xlsx,
        bg="#2e7d32", fg="#ffffff", relief="flat", padx=10, pady=3,
        activebackground="#388e3c", activeforeground="#ffffff"
    ).pack(side="left", padx=(16, 0))

    lojas_tbl = tk.Frame(tab_lojas, bg="#1e1e1e")
    lojas_tbl.pack(fill="both", expand=True, padx=10, pady=6)

    lojas_bar = tk.Label(tab_lojas, bg="#1e1e1e")
    lojas_bar.pack(pady=6)

    lojas_heat = tk.Label(tab_lojas, bg="#1e1e1e")
    lojas_heat.pack(pady=6)

    # ---------------------------------------
    # 6) Função principal de Render (_render)
    # ---------------------------------------



    def _render():
        # limpar a linha de gráficos...
        for w in geral_row_holder.winfo_children():
            w.destroy()

        if not mes_var.get():
            messagebox.showwarning("Parâmetros", "Selecione um mês.")
            return

        mes_nome, ano_str = mes_var.get().split("/")
        ano_num = int(ano_str)
        incluir_gc = bool(goodcard_var.get())

                
        # --- Diagnóstico de origem: memória vs banco ---
        mes_full = f"{mes_nome}/{ano_str}"

        # A) Fonte A (memória) – lista carregada ao abrir a janela
        mem_rows = [p for p in (perfis_docs_all or []) if str(p.get("mes","")).strip() == mes_full]

        # B) Fonte B (banco) – query direta no mês (sem filtrar por role)
        try:
            res = supabase.table("perfis").select("doc,mes,usuario_dono").eq("mes", mes_full).execute()
            db_rows = res.data or []
        except Exception as e:
            db_rows = []
            print(f"[ORIGEM] Falha Supabase: {e}")

        db_docs = []
        for r in db_rows:
            d = r.get("doc") or {}
            d.setdefault("mes", r.get("mes"))
            d.setdefault("usuario_dono", r.get("usuario_dono"))
            db_docs.append(d)

        def _key(p):
            return (str(p.get("nome","")).strip(),
                    str(p.get("mes","")).strip(),
                    str(p.get("usuario_dono","")).strip())

        mem_keys = { _key(p) for p in mem_rows }
        db_keys  = { _key(p) for p in db_docs }

        only_mem = sorted(mem_keys - db_keys)
        only_db  = sorted(db_keys - mem_keys)

        print(f"[ORIGEM] Perfis do mês  mem={len(mem_rows)}  db={len(db_docs)}")
        if only_mem:
            print("[ORIGEM] Só na memória:", only_mem)
        if only_db:
            print("[ORIGEM] Só no banco  :", only_db)

        # União com dedupe (para a Análise, use o conjunto mais completo)
        docs_mes = {}
        for p in mem_rows + db_docs:
            docs_mes[_key(p)] = p
        perfis_docs_mes = list(docs_mes.values())

        print(f"[ORIGEM] União p/ análise = {len(perfis_docs_mes)} perfis")


        # >>> use a lista já carregada e filtre por mês (memória)
        perfis_docs_mes = [
            p for p in (perfis_docs_all or [])
            if str(p.get("mes", "")).strip() == mes_full
        ]
        print(f"[Análise de Lojas] Perfis do mês {mes_full}: {len(perfis_docs_mes)}")

        geral, regionais, lojas, pend_geral, pend_nivel = _agregar(
            perfis_docs_mes, mes_nome, ano_num,
            incluir_goodcard=incluir_gc, reg_filtro=None
        )

        print("[CHECK] dias_validos =", geral.get("dias_validos", 0),
            "finalizados =", geral.get("finalizados", 0))


        # ---------- KPIs ----------
        dv = geral.get("dias_validos", 0)
        fin = geral.get("finalizados", 0)
        pct = 0 if dv == 0 else fin / dv * 100.0
        kpi_lbl.config(text=f"Dias válidos: {dv:,} | Finalizados: {fin:,} | % Finalizados: {pct:,.1f}%")

        # ============
        # GERAL: Rosca
        # ============
        win.update_idletasks()
        W_px = max(700, win.winfo_width())
        # cada gráfico usa ~48% da largura; altura ~62% dessa largura
        G_W = int(W_px * 0.48)
        G_H = int(G_W * 0.45)

        base = fin
        vals = [
            geral.get("sem_pend_dentro", 0),
            geral.get("sem_pend_fora",   0),
            geral.get("com_pend_dentro", 0),
            geral.get("com_pend_fora",   0),
        ]
        rotulos_legenda = [
            "S.Pendência — No prazo",
            "S.Pendência — Fora do prazo",
            "Pendência — No prazo",
            "Pendência — Fora do prazo",
        ]
        rosca_img = None
        if base > 0 and sum(vals) > 0:
            fig, ax = plt.subplots(figsize=(G_W/96, G_H/96))
            # margens ajustadas para evitar cortes (bordas/legenda)
            fig.subplots_adjust(left=0.12, right=0.74, top=0.92, bottom=0.12)

            wedges, _ = ax.pie(
                vals,
                labels=None,  # rótulos só na legenda
                wedgeprops=dict(width=0.35),
            )
            ax.set_title("Finalizados")

            textos = [f"{lbl}: {v/base:.1%}" for lbl, v in zip(rotulos_legenda, vals)]
            leg = ax.legend(
                wedges, textos, title="Categorias",
                loc="center left", bbox_to_anchor=(0.96, 0.5),
                borderaxespad=0.0, labelcolor="#ffffff",
                facecolor="#1e1e1e", edgecolor="#ffffff", framealpha=1.0
            )
            if leg and leg.get_title():
                leg.get_title().set_color("#ffffff")

            rosca_img = _fig_to_photo(fig)  # _fig_to_photo salva sem 'apertar' o layout

        # ==========================
        # GERAL: Pareto (vertical, %)
        # ==========================
        pareto_img = None
        if pend_geral and sum(pend_geral.values()) > 0:
            total_pend = sum(pend_geral.values())
            items = sorted(pend_geral.items(), key=lambda kv: kv[1], reverse=True)
            rot = [k for k, _ in items]
            val = [(v / total_pend) * 100.0 for _, v in items]
            cores = [CORES_NIVEL_PEND.get(_nivel_pendencia(_norm_txt(k)), "#90A4AE") for k in rot]

            fig, ax = plt.subplots(figsize=(G_W/96, G_H/96))
            fig.subplots_adjust(left=0.12, right=0.98, top=0.88, bottom=0.28)

            bars = ax.bar(rot, val, color=cores, edgecolor="#263238")
            ax.set_title("Pendências — % do total")
            ax.set_ylabel("%")
            ax.set_ylim(0, (max(val) * 1.2 if val else 1))
            ax.tick_params(axis='x', rotation=28)
            pad = (max(val) * 0.02 if val else 1)
            for rct, v in zip(bars, val):
                ax.text(rct.get_x() + rct.get_width()/2, rct.get_height() + pad,
                        f"{v:.1f}%", ha="center", va="bottom", fontsize=10,
                        color="#ffffff",
                        bbox=dict(facecolor="#263238", alpha=0.55, boxstyle="round,pad=0.12"))
            pareto_img = _fig_to_photo(fig)

        # Alojamento lado a lado (grid 2 colunas)
        geral_row_holder.grid_columnconfigure(0, weight=1, uniform="plots")
        geral_row_holder.grid_columnconfigure(1, weight=1, uniform="plots")
        geral_row_holder.grid_rowconfigure(0, weight=1)
        if rosca_img:
            lbl1 = tk.Label(geral_row_holder, image=rosca_img, bg="#1e1e1e")
            lbl1.image = rosca_img
            lbl1.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        if pareto_img:
            lbl2 = tk.Label(geral_row_holder, image=pareto_img, bg="#1e1e1e")
            lbl2.image = pareto_img
            lbl2.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        # --------------------------------------
        # 6.2) REGIONAIS — empilhado 100% leve
        # --------------------------------------
        data_emp = []
        for reg, d in regionais.items():
            base_reg = d["finalizados"]
            if base_reg == 0:
                continue
            data_emp.append({
                "Regional": reg,
                "Sem pend. — dentro do prazo": d["sem_pend_dentro"]/base_reg*100,
                "Sem pend. — fora do prazo":   d["sem_pend_fora"]/base_reg*100,
                "Com pend. — dentro do prazo": d["com_pend_dentro"]/base_reg*100,
                "Com pend. — fora do prazo":   d["com_pend_fora"]/base_reg*100,
            })

        EMP_W = int(W_px * 0.92)
        EMP_H = int(W_px * 0.25)

        if data_emp:
            df_emp = pd.DataFrame(data_emp).set_index("Regional")
            fig, ax = plt.subplots(figsize=(EMP_W/96, EMP_H/96))
            fig.subplots_adjust(left=0.08, right=0.88, top=0.88, bottom=0.22)

            cores_emp = ["#1E88E5", "#FDD835", "#43A047", "#E53935"]  # Azul, Amarelo, Verde, Vermelho
            df_emp[[
                "Sem pend. — dentro do prazo",
                "Sem pend. — fora do prazo",
                "Com pend. — dentro do prazo",
                "Com pend. — fora do prazo"
            ]].plot(kind="bar", stacked=True, ax=ax, width=0.85, color=cores_emp)

            ax.set_ylabel("% sobre finalizados")
            ax.set_title("Análise por Regional")
            ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", framealpha=1.0)
            ax.tick_params(axis='x', rotation=18)

            regs_100_img = _fig_to_photo(fig)
            regs_100.config(image=regs_100_img, text=""); regs_100.image = regs_100_img
        else:
            regs_100.config(image="", text="Sem dados para regionais.", fg="#cccccc")

        # -------------------------------------------------
        # 6.3) REGIONAIS — Pareto Único (TODAS as pendências)
        # -------------------------------------------------
        regs_lista = sorted(regionais.keys(), key=lambda r: (r == "OUTROS", r))
        reg_sel_cb["values"] = regs_lista
        if reg_sel_var.get() not in regs_lista:
            reg_sel_var.set(regs_lista[0] if regs_lista else "")

        def _render_pareto_reg(_evt=None):
            reg_sel = reg_sel_var.get().strip()
            if not reg_sel or reg_sel not in regionais:
                pareto_reg_lbl.config(image="", text="Selecione uma regional.", fg="#cccccc")
                return

            pend = regionais[reg_sel]["pend_por_rotulo"]
            if not pend:
                pareto_reg_lbl.config(image="", text=f"{reg_sel}: sem pendências.", fg="#cccccc")
                return

            # ► TODAS as pendências (sem limitar a Top-5)
            tot = sum(pend.values())
            items = sorted(pend.items(), key=lambda kv: kv[1], reverse=True)
            rot = [k for k, _ in items]
            vv  = [(v / tot) * 100.0 for _, v in items]
            cores = [CORES_NIVEL_PEND.get(_nivel_pendencia(_norm_txt(k)), "#90A4AE") for k in rot]

            # Congelar dimensões na primeira render
            if not pareto_cfg["locked"]:
                W0 = max(1000, win.winfo_width())
                pareto_cfg["fig_w"] = int(W0 * pareto_cfg["init_frac_w"])
                pareto_cfg["fig_h"] = int(W0 * pareto_cfg["init_frac_h"])
                pareto_cfg["locked"] = True

            FW = max(640, pareto_cfg["fig_w"])
            FH = max(240, pareto_cfg["fig_h"])
            DPI = pareto_cfg["dpi"]
            L, R, T, B = pareto_cfg["margins"]
            Y0, Y1     = pareto_cfg["ylim"]
            xrot       = pareto_cfg["tick_rot"]
            xsize      = pareto_cfg["labelsize"]

            # Heurísticas de legibilidade para muitos rótulos
            n = len(rot)
            if n >= 8:
                xrot = 32; xsize = 9; B = max(B, 0.36)
            if n >= 12:
                xrot = 38; xsize = 8; B = max(B, 0.42)
            if n >= 18:
                xrot = 44; xsize = 8; B = max(B, 0.48)

            fig, ax = plt.subplots(figsize=(FW / DPI, FH / DPI), dpi=DPI)
            fig.subplots_adjust(left=L, right=R, top=T, bottom=B)

            bars = ax.bar(rot, vv, color=cores, edgecolor="#ffffff", linewidth=0.7)
            ax.set_title(f"{reg_sel} — Pareto (% por pendência)")
            ax.set_ylabel("%")
            ax.set_ylim(Y0, Y1)  # Y fixo 0..100
            ax.tick_params(axis='x', rotation=xrot, labelsize=xsize)

            pad = 100 * 0.02
            for rct, pval in zip(bars, vv):
                ax.text(
                    rct.get_x() + rct.get_width()/2,
                    min(Y1, pval + pad),
                    f"{pval:.1f}%",
                    ha="center", va="bottom", fontsize=10, color="#ffffff",
                    bbox=dict(facecolor="#263238", alpha=0.55, boxstyle="round,pad=0.12")
                )

            img = _fig_to_photo(fig)  # _fig_to_photo deve salvar sem tight_layout
            pareto_reg_lbl.config(image=img, text=""); pareto_reg_lbl.image = img

        # 1ª render do Pareto por regional + bind do filtro
        _render_pareto_reg()
        reg_sel_cb.bind("<<ComboboxSelected>>", _render_pareto_reg)

        # ---------------------------------
        # 6.4) LOJAS — tabela, barras, mapa
        # ---------------------------------
        for w in lojas_tbl.winfo_children():
            w.destroy()

        hdr = ["Regional","Loja","Dias válidos","Finalizados","% Finalizados",
                "Sem pend. — dentro do prazo","Sem pend. — fora do prazo",
                "Com pend. — dentro do prazo","Com pend. — fora do prazo",
                "Pendência principal (mês)","Nível máx. (mês)","GoodCard vencidos"]

        header = tk.Frame(lojas_tbl, bg="#1e1e1e")
        header.pack(fill="x")
        for j, col in enumerate(hdr):
            tk.Label(header, text=col, bg="#000000", fg="#ffffff", font=("Segoe UI", 9),
                        relief="solid", bd=1, width=18).grid(row=0, column=j, sticky="nsew")

        linhas = []
        for (reg, nome), d in lojas.items():
            c3 = int(d["pend_por_nivel"].get(3, 0))
            c2 = int(d["pend_por_nivel"].get(2, 0))
            c1 = int(d["pend_por_nivel"].get(1, 0))
            pontos = 3*c3 + 2*c2 + 1*c1

            dias_validos = int(d.get("dias_validos", 0))  # denominador usado nos KPIs
            dias_vig_total = int(d.get("dias_vigencia_total", d.get("dias_planejados", dias_validos)))
            base_mes = int(d.get("base_mes", 0))
            final = int(d["finalizados"])
            pct_real = (0 if dias_validos in (0, None) else final / float(dias_validos) * 100.0)
            pct_norm = (0 if base_mes in (0, None) else final / float(base_mes) * 100.0)
            troca = "⚑" if (base_mes and dias_vig_total < base_mes) else ""

            linhas.append({
                "Regional": reg,
                "Loja": nome,
                "Pontos (ranking)": pontos,
                "Pend L3": c3, "Pend L2": c2, "Pend L1": c1,

                # >>> Bases e percentuais
                "Dias válidos": dias_validos,
                "Vigência total (mês)": dias_vig_total,
                "Base do mês (sem dom.)": base_mes,

                "Finalizados": final,
                "% Finalizados": pct_real,
                "% Finalizados (normalizado)": pct_norm,

                "Troca no mês": troca,

                # (demais colunas iguais)
                "Sem pend. — dentro do prazo": d["sem_pend_dentro"],
                "Sem pend. — fora do prazo": d["sem_pend_fora"],
                "Com pend. — dentro do prazo": d["com_pend_dentro"],
                "Com pend. — fora do prazo": d["com_pend_fora"],
                "Pendência principal (mês)": (max(d["pend_por_rotulo"].items(), key=lambda kv: kv[1])[0] if d["pend_por_rotulo"] else ""),
                "Nível máx. (mês)": (max(d["pend_por_nivel"].keys(), default=0)),
                "GoodCard vencidos": d.get("goodcard_vencidos", 0),
            })



        df_lojas = pd.DataFrame(linhas) if linhas else pd.DataFrame(columns=hdr)

        # >>> Sugestão: reordenar as colunas do cabeçalho para incluir o ranking de forma visível
        hdr = ["Regional","Loja",
            "Pontos (ranking)","Pend L3","Pend L2","Pend L1",   # <<< novas
            "Dias válidos","Finalizados","% Finalizados",
            "Sem pend. — dentro do prazo","Sem pend. — fora do prazo",
            "Com pend. — dentro do prazo","Com pend. — fora do prazo",
            "Pendência principal (mês)","Nível máx. (mês)","GoodCard vencidos"]
        
        
        # --- MAPA: (regional, loja) -> qtd de auditores únicos no mês ---
        from collections import defaultdict

        # helper para achar a regional a partir do cadastro geral (self.lojas) — consistente
        def _regional_cadastro(loja_nome: str) -> str:
            try:
                for l in (self.lojas or []):
                    if l.get("loja") == loja_nome:
                        return normalizar_regional(l.get("regional"))
            except Exception:
                pass
            return "OUTROS"

        auditores_por_loja = defaultdict(set)   # {(reg, loja): {owners}}
        
        for doc in (perfis_docs_mes or []):
            dono = (doc.get("usuario_dono") or "").strip()
            for lj in (doc.get("lojas") or []):
                nome = str(lj.get("loja") or "").strip()
                if not nome:
                    continue
                # regional preferindo o cadastro geral (mais estável)
                reg = _regional_cadastro(nome) or normalizar_regional(lj.get("regional"))
                auditores_por_loja[(reg, nome)].add(dono)

        # atalho prático: contagem por loja
        owners_count = {k: (len(v) if len(v) > 0 else 1) for k, v in auditores_por_loja.items()}

        def _render_lojas_table():
            for w in lojas_tbl.winfo_children():
                w.destroy()

            # --- Canvas + Scroll
            container = tk.Frame(lojas_tbl, bg="#1e1e1e"); container.pack(fill="both", expand=True)
            canvas = tk.Canvas(container, bg="#1e1e1e", highlightthickness=0)
            vsb = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=vsb.set)
            vsb.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
            table_frame = tk.Frame(canvas, bg="#1e1e1e")
            canvas.create_window((0, 0), window=table_frame, anchor="nw")
            table_frame.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

            # --- Cabeçalho (removidos: Pend L1/L2/L3 e Nível)
            hdr = [
                "Regional","Loja","Pontos (ranking)",
                "Dias válidos (norm.)","Finalizados","% Finalizados",
                "Sem pend. — dentro do prazo","Sem pend. — fora do prazo",
                "Com pend. — dentro do prazo","Com pend. — fora do prazo",
                "Pendência principal (mês)","GoodCard vencidos","Auditores"
            ]
            header = tk.Frame(table_frame, bg="#1e1e1e"); header.pack(fill="x")
            for j, col in enumerate(hdr):
                tk.Label(header, text=col, bg="#000000", fg="#ffffff",
                        font=("Segoe UI", 9), relief="solid", bd=1, width=18)\
                .grid(row=0, column=j, sticky="nsew")

            # --- Monta DataFrame de exibição a partir do dicionário 'lojas'
            linhas = []
            for (reg, nome), d in lojas.items():
                # pontos do ranking (L3=3pts, L2=2pts, L1=1pt) — já vem calculado em outro lugar?
                c3 = int(d["pend_por_nivel"].get(3, 0))
                c2 = int(d["pend_por_nivel"].get(2, 0))
                c1 = int(d["pend_por_nivel"].get(1, 0))
                pontos = (3*c3 + 2*c2 + 1*c1)

                # bases
                dv_real = int(d.get("dias_validos", 0))  # base usada na agregação
                final   = int(d.get("finalizados", 0))
                # normalização por nº de auditores distintos no mês
                owners  = int(owners_count.get((reg, nome), 1))
                dv_norm = max(1, int(round(dv_real / owners))) if dv_real > 0 else 0

                # % finalizados (sobre base normalizada, como você pediu)
                pct = (0.0 if dv_norm in (0, None) else (final / float(dv_norm) * 100.0))

                linhas.append({
                    "Regional": reg,
                    "Loja": nome,
                    "Pontos (ranking)": pontos,
                    "Dias válidos (norm.)": dv_norm,
                    "Finalizados": final,
                    "% Finalizados": pct,
                    "Sem pend. — dentro do prazo": int(d["sem_pend_dentro"]),
                    "Sem pend. — fora do prazo":   int(d["sem_pend_fora"]),
                    "Com pend. — dentro do prazo": int(d["com_pend_dentro"]),
                    "Com pend. — fora do prazo":   int(d["com_pend_fora"]),
                    "Pendência principal (mês)": (max(d["pend_por_rotulo"].items(), key=lambda kv: kv[1])[0]
                                                if d["pend_por_rotulo"] else ""),
                    "GoodCard vencidos": int(d.get("goodcard_vencidos", 0)),
                    "Auditores": owners,
                })

            df_view = pd.DataFrame(linhas) if linhas else pd.DataFrame(columns=hdr)

            # --- Filtro de regional
            rf = reg_var.get().strip()
            if rf:
                if "Regional" in df_view.columns:
                    df_view = df_view[df_view["Regional"] == rf]
                else:
                    df_view = df_view.iloc[0:0]

            # --- Tipos numéricos e ORDEM por Pontos (ranking) DESC
            for col in ["Pontos (ranking)","% Finalizados","Finalizados","Dias válidos (norm.)",
                        "Sem pend. — dentro do prazo","Sem pend. — fora do prazo",
                        "Com pend. — dentro do prazo","Com pend. — fora do prazo","GoodCard vencidos","Auditores"]:
                if col in df_view.columns:
                    df_view[col] = pd.to_numeric(df_view[col], errors="coerce").fillna(0)

            if not df_view.empty:
                df_view = df_view.sort_values(
                    by=["Pontos (ranking)", "% Finalizados", "Finalizados", "Dias válidos (norm.)"],
                    ascending=[False,            False,           False,            False],
                    kind="mergesort"  # estável; preserva empates
                )

            # Guarda cópia para exportação em XLSX (botão "Exportar XLSX")
            _lojas_export_state["df"] = df_view.copy()

            # --- Render das linhas
            body = tk.Frame(table_frame, bg="#1e1e1e"); body.pack(fill="both", expand=True)
            for i, row in df_view.iterrows():
                for j, col in enumerate(hdr):
                    val = row[col]
                    if isinstance(val, float):
                        # formata % com 1 casa para a coluna de %; demais com int
                        txt = f"{val:.1f}%" if col == "% Finalizados" else f"{val:.0f}"
                    else:
                        txt = f"{val}"
                    tk.Label(body, text=txt, bg="#2e2e2e", fg="#ffffff",
                            font=("Segoe UI", 9), relief="solid", bd=1, width=18)\
                    .grid(row=i+1, column=j, sticky="nsew")

            # Scroll wheel local
            def _on_mousewheel(e): canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
            canvas.bind("<MouseWheel>", _on_mousewheel)

            def _render_lojas_barras():
                rf = reg_var.get().strip()
                df_view = df_lojas if not rf else df_lojas[df_lojas["Regional"] == rf]
                if df_view.empty:
                    lojas_bar.config(image="", text="Sem dados para exibir.", fg="#cccccc"); return

                # Ordena por 'Com pend. — fora do prazo' (quantidade absoluta)
                df_sorted = df_view.sort_values("Com pend. — fora do prazo", ascending=False).head(10)  # TOP 10

                labels = [f"{r} - {n}" for r, n in zip(df_sorted["Regional"], df_sorted["Loja"])]

                # base: % sobre finalizados da loja
                base = df_sorted["Finalizados"].where(df_sorted["Finalizados"] > 0, pd.NA)
                valores = ((df_sorted["Com pend. — fora do prazo"] * 100.0) / base)\
                        .fillna(0.0).astype(float).tolist()

                cores = [CORES_NIVEL_PEND.get(int(n if not pd.isna(n) else 0), "#90A4AE")
                        for n in df_sorted.get("Nível máx. (mês)", [0]*len(df_sorted)).values]

                # --- Tamanho e margens dinâmicos (ajusta à tela)
                Wp = max(900, min(1280, win.winfo_width()))
                B_W = int(Wp * 0.90)
                B_H = int(max(260, Wp * 0.28))

                fig, ax = plt.subplots(figsize=(B_W/96, B_H/96))
                # margens: mais espaço para os rótulos em baixo
                fig.subplots_adjust(left=0.08, right=0.98, top=0.86, bottom=0.28)

                bars = ax.bar(labels, valores, color=cores, edgecolor="#263238")
                ax.set_title("Com pendência — fora do prazo (Top 10, % de finalizados)" + (f" — {rf}" if rf else ""))

                ax.set_ylabel("% sobre finalizados")
                ax.set_ylim(0, (max(valores) * 1.25 if valores else 1))
                ax.tick_params(axis='x', rotation=18, labelsize=9)

                # rótulo de valor
                pad = (max(valores) * 0.03 if valores else 1)
                for i, v in enumerate(valores):
                    ax.text(i, v + pad, f"{v:.1f}%", ha="center", va="bottom", fontsize=9, color="#ffffff")

                img = _fig_to_photo(fig)
                lojas_bar.config(image=img, text=""); lojas_bar.image = img


        def _on_reg_change(*_):
            _render_lojas_table()
            _render_lojas_barras()
        reg_var.trace_add("write", _on_reg_change)

    # Auto-render: mudar Mês/Ano recalcula; Enter no prazo também
    def _auto(*_): _render()
    cb_mes.bind("<<ComboboxSelected>>", lambda _e: _auto())
    barra.bind("<Return>", lambda _e: _auto())

    # Render inicial e focar na aba Geral

    # Render inicial
    _render()

    # Focar na aba Geral, apenas quando o Notebook existir e estiver pronto
    def _select_safe():
        try:
            if nb.winfo_exists() and tab_geral.winfo_exists():
                nb.select(tab_geral)
        except tk.TclError:
            pass  # widget foi destruído no meio do caminho; apenas ignore

    # Agenda para depois que a janela estiver mapeada
    win.after_idle(_select_safe)

class ControleLojas(tk.Frame):
    def __init__(self, master, voltar_callback, current_user):
        super().__init__(master, bg=BG_DARK)
        self.master = master
        self.voltar_callback = voltar_callback
        self.current_user = current_user or {}  # <- guarda o usuário logado

        self.lojas = self.carregar_lojas()
        self.perfis = self.carregar_perfis()
        self.analise_vales = self.carregar_analise_vales()

        self.conteudo_frame = tk.Frame(self, bg=BG_DARK)
        self.conteudo_frame.pack(fill="both", expand=True)

        self.tela_inicial()

    def limpar_todos_os_perfis(self):
        from tkinter import messagebox

        if not messagebox.askyesno("Confirmação",
            "⚠️ Isso vai APAGAR TODOS os perfis do banco.\n\nDeseja continuar?"):
            return

        try:
            supabase.table("perfis").delete().neq("nome", "").execute()
            self.perfis = []     # também limpa localmente
            messagebox.showinfo("Sucesso", "Todos os perfis foram apagados!")
            self.tela_controle_lojas()  # atualiza a tela
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao limpar perfis:\n{e}")
    
    def _ensure_loja_in_perfil(self, perfil: dict, loja_nome: str, cnpj_hint: str = ""):
        """
        Garante que 'perfil["lojas"]' contenha a loja informada.
        Usa cnpj_hint quando fornecido; senão tenta puxar do cadastro (self.lojas).
        """
        arr = perfil.setdefault("lojas", [])
        if any((x.get("loja") == loja_nome) for x in arr):
            return  # já existe

        # tenta descobrir CNPJ/Regional do cadastro geral
        info = next((x for x in (self.lojas or []) if x.get("loja") == loja_nome), None)
        cnpj = cnpj_hint or (info.get("cnpj") if info else "")
        reg  = normalizar_regional(info.get("regional") if info else "OUTROS")

        arr.append({"loja": loja_nome, "cnpj": cnpj, "regional": reg})


    def carregar_analise_vales(self):
        try:
            res = supabase.table("analise_vales_doc") \
                        .select("doc") \
                        .eq("id","singleton") \
                        .limit(1).execute()
            data = res.data or []
            if data and isinstance(data[0].get("doc"), dict):
                return data[0]["doc"]
        except:
            pass
        return {}

    LOJAS_ABREM_DOMINGO = {"32", "35", "40", "43", "86"}
    
    def loja_abre_domingo(self, nome_loja: str) -> bool:
        try:
            return normalizar_loja_valor(nome_loja) in LOJAS_ABREM_DOMINGO
        except Exception:
            return False
    
    def _ultimo_dia_mes_from_mes_full(self, mes_full: str) -> int:
        """Recebe 'Mês/AAAA' e retorna o último dia daquele mês."""
        try:
            mes_nome, ano_str = mes_full.split("/")
            ano = int(ano_str)
            mes_num = MESES_PTBR.index(mes_nome) + 1
            import calendar
            return calendar.monthrange(ano, mes_num)[1]
        except Exception:
            return 31  # fallback seguro

    def _get_perfis_do_mes(self, mes_full: str) -> list[dict]:
        return [p for p in (self.perfis or []) if p.get("mes") == mes_full]

    def _find_planilha_entry(self, perfil: dict, loja_nome: str) -> dict | None:
        for ent in (perfil.get("planilha") or []):
            if ent.get("loja") == loja_nome:
                return ent
        return None

    def _garantir_entry_destino(self, perfil_dest: dict, loja_origem_entry: dict) -> dict:
        """Garante que o perfil destino tenha uma entrada de planilha para a loja; cria se necessário."""
        ent = self._find_planilha_entry(perfil_dest, loja_origem_entry.get("loja"))
        if ent is None:
            ent = {
                "loja": loja_origem_entry.get("loja"),
                "cnpj": loja_origem_entry.get("cnpj", ""),
                "dias": {},
                "meta": {}
            }
            perfil_dest.setdefault("planilha", []).append(ent)
        return ent

    def _mover_faixa_dias(self, ent_origem: dict, ent_destino: dict, dia_ini: int, dia_fim: int):
        """Move dias e meta no intervalo [dia_ini..dia_fim] da origem para o destino (merge seguro)."""
        ent_destino.setdefault("dias", {})
        ent_destino.setdefault("meta", {})
        ent_origem.setdefault("dias", {})
        ent_origem.setdefault("meta", {})

        for d in range(int(dia_ini), int(dia_fim) + 1):
            k = str(d)
            # dias (status)
            if k in ent_origem["dias"]:
                if k not in ent_destino["dias"] or not ent_destino["dias"][k]:
                    ent_destino["dias"][k] = ent_origem["dias"][k]
                del ent_origem["dias"][k]
            # meta (logs etc.)
            if k in ent_origem["meta"]:
                if k not in ent_destino["meta"]:
                    ent_destino["meta"][k] = ent_origem["meta"][k]
                del ent_origem["meta"][k]

    def abrir_wizard_troca_loja(self):
        """Wizard para realizar a troca de loja entre perfis dentro do mesmo mês."""
        import tkinter as tk
        from tkinter import ttk, messagebox
        
        # --- GUARDA DE ACESSO: somente admin ---
        role = (self.current_user or {}).get("role", "") or ""
        if role != "admin":
            try:
                messagebox.showwarning("Acesso negado", "Somente administradores podem realizar troca de loja.")
            except Exception:
                pass
            return


        top = tk.Toplevel(self.master)
        top.title("Troca de Loja (mês corrente)")
        top.configure(bg=BG_DARK)
        top.grab_set()

        # Linha 1: Mês/Ano
        frm = tk.Frame(top, bg=BG_DARK); frm.pack(padx=16, pady=12, fill="x")
        meses_unicos = sorted(
            {p.get("mes","") for p in (self.perfis or []) if p.get("mes")},
            key=lambda s: (int(s.split("/")[1]), MESES_PTBR.index(s.split("/")[0]) + 1)
        )
        tk.Label(frm, text="Mês/Ano:", bg=BG_DARK, fg=FG_TEXT).grid(row=0, column=0, sticky="e", padx=(0,6))
        mes_var = tk.StringVar(value=meses_unicos[-1] if meses_unicos else "")
        cb_mes = ttk.Combobox(frm, textvariable=mes_var, values=meses_unicos, width=24)
        cb_mes.grid(row=0, column=1, sticky="w")

        # Linha 2: Loja + Dia de corte
        tk.Label(frm, text="Loja:", bg=BG_DARK, fg=FG_TEXT).grid(row=1, column=0, sticky="e", padx=(0,6), pady=(8,0))
        lojas_nomes = sorted({l.get("loja") for p in self._get_perfis_do_mes(mes_var.get()) for l in (p.get("lojas") or [])})
        loja_var = tk.StringVar(value=lojas_nomes[0] if lojas_nomes else "")
        cb_loja = ttk.Combobox(frm, textvariable=loja_var, values=lojas_nomes, width=24)
        cb_loja.grid(row=1, column=1, sticky="w", pady=(8,0))

        tk.Label(frm, text="Dia de corte:", bg=BG_DARK, fg=FG_TEXT).grid(row=1, column=2, sticky="e", padx=(16,6), pady=(8,0))
        corte_var = tk.StringVar(value="15")
        tk.Entry(frm, textvariable=corte_var, width=6, bg=BG_PANEL, fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat").grid(row=1, column=3, sticky="w", pady=(8,0))

        # Linha 3: Perfil origem/destino (do mesmo mês)
        tk.Label(frm, text="Perfil ORIGEM:", bg=BG_DARK, fg=FG_TEXT).grid(row=2, column=0, sticky="e", padx=(0,6), pady=(8,0))
        orig_var = tk.StringVar()
        tk.Label(frm, text="Perfil DESTINO:", bg=BG_DARK, fg=FG_TEXT).grid(row=2, column=2, sticky="e", padx=(16,6), pady=(8,0))
        dest_var = tk.StringVar()

        def _refresh_perfis(_=None):
            perfis_mes = self._get_perfis_do_mes(mes_var.get())
            nomes = [f"{p.get('nome','(sem nome)')} — owner:{p.get('usuario_dono','')}" for p in perfis_mes]
            cb_orig["values"] = nomes; cb_dest["values"] = nomes
            if nomes:
                orig_var.set(nomes[0])
                dest_var.set(nomes[0])
            # lojas desse mês
            nomes_lojas = sorted({l.get("loja") for p in perfis_mes for l in (p.get("lojas") or []) if l.get("loja")})
            cb_loja["values"] = nomes_lojas
            if nomes_lojas and not loja_var.get():
                loja_var.set(nomes_lojas[0])

        cb_orig = ttk.Combobox(frm, textvariable=orig_var, values=[], width=40)
        cb_orig.grid(row=2, column=1, sticky="w", pady=(8,0))
        cb_dest = ttk.Combobox(frm, textvariable=dest_var, values=[], width=40)
        cb_dest.grid(row=2, column=3, sticky="w", pady=(8,0))
        _refresh_perfis()
        cb_mes.bind("<<ComboboxSelected>>", _refresh_perfis)

        # Rodapé de ações
        btns = tk.Frame(top, bg=BG_DARK); btns.pack(pady=12)

        def _resolve_perfil(label_txt: str) -> dict | None:
            nome = label_txt.split(" — owner:")[0].strip()
            for p in self._get_perfis_do_mes(mes_var.get()):
                if p.get("nome","").strip() == nome:
                    return p
            return None

        def aplicar():
            try:
                mes_full = mes_var.get().strip()
                loja_nome = loja_var.get().strip()
                corte = int((corte_var.get() or "0").strip())
                if not (mes_full and loja_nome and corte > 0):
                    messagebox.showwarning("Troca de Loja", "Informe todos os campos e um dia de corte > 0.")
                    return

                perf_origem = _resolve_perfil(orig_var.get())
                perf_dest   = _resolve_perfil(dest_var.get())
                if perf_origem is None or perf_dest is None:
                    messagebox.showwarning("Troca de Loja", "Selecione perfis origem/destino válidos.")
                    return
                if perf_origem is perf_dest:
                    messagebox.showwarning("Troca de Loja", "Origem e destino devem ser diferentes.")
                    return

                # último dia do mês
                ultimo = self._ultimo_dia_mes_from_mes_full(mes_full)
                if corte >= ultimo:
                    messagebox.showwarning("Troca de Loja", f"O corte deve ser < {ultimo}.")
                    return

                # localizar entrada da loja na origem
                ent_o = self._find_planilha_entry(perf_origem, loja_nome)
                if ent_o is None:
                    messagebox.showwarning("Troca de Loja", "A loja selecionada não pertence ao perfil de ORIGEM.")
                    return

                # garantir entrada no destino
                ent_d = self._garantir_entry_destino(perf_dest, ent_o)

                # ajustar vigências
                ent_o["vigencia"] = {"ini": 1, "fim": corte}
                ent_d["vigencia"] = {"ini": corte + 1, "fim": ultimo}

                # mover dias/meta da faixa (corte+1 .. ultimo)
                self._mover_faixa_dias(ent_o, ent_d, corte + 1, ultimo)
                
                self._ensure_loja_in_perfil(perf_origem, loja_nome, ent_o.get("cnpj", ""))
                self._ensure_loja_in_perfil(perf_dest,   loja_nome, ent_o.get("cnpj", ""))

                # logs de transferência (simples, para auditoria)
                from datetime import datetime as _dt
                ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                for perf, papel in ((perf_origem, "origem"), (perf_dest, "destino")):
                    perf.setdefault("transferencias", [])
                    perf["transferencias"].append({
                        "ts": ts,
                        "loja": loja_nome,
                        "mes": mes_full,
                        "papel": papel,
                        "corte": corte,
                        "usuario_exec": (self.current_user or {}).get("username","")
                    })

                # persistir
                self.salvar_perfis()
                messagebox.showinfo("Troca de Loja", "Troca aplicada com sucesso.")
                top.destroy()

            except Exception as e:
                messagebox.showerror("Troca de Loja", f"Falha ao aplicar troca:\n{e}")

        tk.Button(btns, text="Aplicar", command=aplicar,
                font=FONT_UI, bg="#31a252", fg=FG_TEXT,
                activebackground="#6ecf42", activeforeground="#172a38",
                relief="flat", bd=0, padx=12, pady=8).pack(side="left", padx=8)
        tk.Button(btns, text="Cancelar", command=top.destroy,
                font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                activebackground="#444444", activeforeground=FG_ACTIVE,
                relief="flat", bd=0, padx=12, pady=8).pack(side="left", padx=8)


    def salvar_analise_vales(self):
        try:
            supabase.table("analise_vales_doc").upsert({
                "id": "singleton",
                "doc": self.analise_vales or {}
            }).execute()
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao salvar análise de vales: {e}")

    def carregar_lojas(self):
        try:
            res = supabase.table("lojas").select("*").order("id").execute()
            dados = res.data or []
            lojas = []
            for d in dados:
                lojas.append({
                    "loja": d.get("loja","Sem Nome"),
                    "cnpj": d.get("cnpj",""),
                    "regional": normalizar_regional(d.get("regional"))
                })
            return lojas
        except Exception as e:
            print("ERRO CARREGAR LOJAS:", e)
            return []
    
    def _dia_dentro_vigencia(self, loja_dict: dict, dia_int: int, dias_mes: int) -> bool:
        """
        Retorna True se 'dia_int' estiver dentro da vigência cadastrada para a loja neste perfil.
        - Ausência de 'vigencia' => mês inteiro (1..dias_mes).
        - Campos ausentes/invalidos são tratados com defaults seguros.
        """
        try:
            v = (loja_dict or {}).get("vigencia") or {}
            if not isinstance(v, dict):
                return True
            ini = int(v.get("ini", 1) or 1)
            fim = int(v.get("fim", dias_mes) or dias_mes)
            if ini < 1: ini = 1
            if fim > dias_mes: fim = dias_mes
            return ini <= int(dia_int) <= fim
        except Exception:
            # Se algo vier torto, não quebra contagem: considera válido (compatível com antes)
            return True


    def carregar_perfis(self):
        """
        Carrega perfis no formato JSONB do Supabase.
        Admin vê todos; auditor vê apenas os dele.
        """
        try:
            role = (self.current_user or {}).get("role","")
            username = (self.current_user or {}).get("username","")

            q = supabase.table("perfis").select("doc")
            if role != "admin":
                q = q.eq("usuario_dono", username)

            res = q.order("created_at", desc=True).execute()
            dados = res.data or []
            return [row["doc"] for row in dados]
        except Exception:
            return []


 
    def salvar_perfis(self):
        # remove perfis duplicados (mesmo nome, mês e usuário)
        perfis_unicos = {}
        for p in self.perfis:
            chave = (p.get("nome",""), p.get("mes",""), p.get("usuario_dono",""))
            perfis_unicos[chave] = p

        self.perfis = list(perfis_unicos.values())

        """
        Salva perfis inteiros como doc JSONB no Supabase.
        """
        rows = []
        for p in (self.perfis or []):
            doc = dict(p)
            doc.setdefault("planilha", [])
            doc.setdefault("observacoes", [])
            doc.setdefault("vouchers", [])
            doc.setdefault("avaliacoes", [])

            dono = doc.get("usuario_dono") or (self.current_user or {}).get("username","")

            rows.append({
                "nome": p.get("nome") or "",
                "mes": p.get("mes") or "",
                "usuario_dono": dono,
                "doc": doc
            })

        if rows:
            supabase.table("perfis").upsert(rows, on_conflict="nome,mes,usuario_dono").execute()


    def limpar_conteudo(self):
        for w in self.conteudo_frame.winfo_children():
            w.destroy()

    def agrupar_por_regional(self, lojas):
        grupos = defaultdict(list)
        for loja in lojas:
            reg = normalizar_regional(loja.get("regional"))
            grupos[reg].append(loja)
        ordenados = []
        for reg in REGIONAIS_ORDENADAS:
            if reg in grupos:
                ordenados.append((reg, grupos[reg]))
        for reg, lista in grupos.items():
            if reg not in REGIONAIS_ORDENADAS:
                ordenados.append((reg, lista))
        return ordenados

    def _bind_hover(self, widget, bg_normal, fg_normal, bg_hover, fg_hover):
        def on_enter(_):
            widget.configure(bg=bg_hover, fg=fg_hover)
        def on_leave(_):
            widget.configure(bg=bg_normal, fg=fg_normal)
        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)


    def criar_legenda_cores(self, parent):
        """
        Renderiza uma legenda com as cores de STATUS_OPCOES, usando CORES_STATUS.
        parent: frame/container onde a legenda será inserida.
        """
        legenda_frame = tk.Frame(parent, bg=BG_DARK)
        legenda_frame.pack(fill="x", padx=10, pady=(0, 6))  # top padding pequeno

        # Wrapper para centralizar tudo dentro da legenda
        wrapper = tk.Frame(legenda_frame, bg=BG_DARK)
        wrapper.pack(anchor="center")  # <-- centraliza o bloco inteiro

       
        tk.Label(
                wrapper,
                text="Legenda de status:",
                font=("Segoe UI", 10, "bold"),
                bg=BG_DARK,
                fg=FG_TEXT,
                justify="center"
            ).pack(anchor="center", padx=4)


        # linha com os itens da legenda
        
        linha = tk.Frame(wrapper, bg=BG_DARK)
        linha.pack(anchor="center", padx=4, pady=(2, 0))


        for status in STATUS_OPCOES:
            cor = CORES_STATUS.get(status, BG_PANEL)
            item = tk.Frame(linha, bg=BG_DARK)
            item.pack(side="left", padx=8, pady=2)

            # quadradinho/bolinha colorida
            swatch = tk.Label(
                item, text="  ",  # largura do quadrado
                bg=cor, width=2, height=1, relief="solid", bd=1
            )
            swatch.pack(side="left")

            tk.Label(
                item, text=f" {status}", 
                font=("Segoe UI", 9), bg=BG_DARK, fg=FG_TEXT
            ).pack(side="left")

    def criar_botao_menu(self, parent, texto, comando, *,
                        bg=BG_PANEL, fg=FG_TEXT,
                        bg_hover="#00c9ff",
                        fg_hover=FG_TEXT,
                        active_bg="#08b4ff", active_fg="#172a38",
                        padx=12, ipady=10):

        btn = tk.Button(
            parent, text=texto, command=comando,
            font=FONT_UI, bg=bg, fg=fg,
            activebackground=active_bg, activeforeground=active_fg,
            relief="flat", bd=0, padx=padx
        )
        self._bind_hover(btn, bg_normal=bg, fg_normal=fg, bg_hover=bg_hover, fg_hover=fg_hover)
        btn.pack(fill="x", pady=10, ipady=ipady)
        return btn

    def _voltar_sem_fechar(self):
        """
        Volta para a tela anterior (menu principal) SEM destruir o root.
        Se houver voltar_callback (e ele apenas troca de tela), usa; senão, vai para a tela_inicial do módulo.
        """
        try:
            if callable(self.voltar_callback):
                # O voltar_callback NO APP PRINCIPAL deve apenas trocar de frame/tela.
                # Ele NÃO pode chamar root.destroy() / master.destroy().
                self.voltar_callback()
                return
        except Exception:
            pass

        # Plano B: volta para a tela inicial do próprio módulo
        try:
            self.tela_inicial()
        except Exception:
            self.limpar_conteudo()

    def tela_inicial(self):
        self.limpar_conteudo()

        tk.Label(
            self.conteudo_frame, text="Controle de Lojas",
            font=("Segoe UI", 14, "bold"), bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=(40, 20))

        btn_menu = tk.Frame(self.conteudo_frame, bg=BG_DARK)
        btn_menu.pack(fill="x", expand=False, padx=40)

        PADX = 12
        IPADY = 10

        self.criar_botao_menu(
            btn_menu, "Painel de Controle", self.tela_controle_lojas,
            bg=BG_PANEL, fg=FG_TEXT,
            bg_hover="#00c9ff", fg_hover=FG_TEXT,    
            active_bg="#08b4ff", active_fg="#172a38", 
            padx=PADX, ipady=IPADY
        )

        tk.Button(
            self.conteudo_frame,
            text="Mapa das Lojas",
            command=self.abrir_mapa_das_lojas,
            font=FONT_UI,
            bg="#007acc", fg=FG_TEXT,
            activebackground="#3399ff", activeforeground="#ffffff",
            relief="flat", bd=0, padx=10, pady=10
        ).pack(fill="x", padx=50, pady=5)

        tk.Button(
            self.conteudo_frame,
            text="Visualização (Minhas Lojas)",
            command=self.abrir_visualizacao_minhas_global,
            font=FONT_UI,
            bg="#007acc", fg=FG_TEXT,
            activebackground="#3399ff", activeforeground="#ffffff",
            relief="flat", bd=0, padx=10, pady=10
        ).pack(fill="x", padx=50, pady=5)

        # botão exclusivo para admin: limpar TODOS os perfis
        role = (self.current_user or {}).get("role", "")

        if role == "admin":
            tk.Button(
                btn_menu,
                text="🗑️ Limpar TODOS os Perfis",
                command=self.limpar_todos_os_perfis,
                font=FONT_UI,
                bg="#8b0000",
                fg=FG_TEXT,
                activebackground="#aa0000",
                activeforeground=FG_TEXT,
                relief="flat",
                bd=0,
                padx=12,
                pady=10
            ).pack(fill="x", pady=10)


        self.criar_botao_menu(
            btn_menu, "Voltar", self._voltar_sem_fechar,
            bg="#8b0000", fg=FG_TEXT,            
            bg_hover="#aa0000", fg_hover=FG_TEXT, 
            active_bg="#aa0000", active_fg=FG_TEXT,
            padx=PADX, ipady=IPADY
        )

    def tela_controle_lojas(self):
        import tkinter as tk

        self.limpar_conteudo()

        # Cabeçalho
        tk.Label(
            self.conteudo_frame, text="Perfis",
            font=("Segoe UI", 12, "bold"),
            bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=(20, 10))

        # --- Aplica filtro por usuário ---
        usuario = (self.current_user or {}).get("username", "") or ""
        role = (self.current_user or {}).get("role", "") or ""

        # Admin vê tudo; demais só seus próprios perfis (p["usuario_dono"] == usuario)
        if role == "admin":
            perfis_para_mostrar = list(self.perfis)
        else:
            perfis_para_mostrar = [
                p for p in self.perfis
                if (p.get("usuario_dono") or "") == usuario
            ]

        # Se não houver perfis conforme o filtro, mostra mensagem de vazio
        if not perfis_para_mostrar:
            msg = (
                "Nenhum perfil encontrado.\n\n"
            )
            box = tk.Frame(self.conteudo_frame, bg=BG_DARK)
            box.pack(fill="x", padx=40, pady=10)
            tk.Label(
                box, text=msg, justify="left",
                bg=BG_DARK, fg="#b0b0b0", font=("Segoe UI", 10)
            ).pack(anchor="w")
        else:
            # --- Área scrollável para perfis ---
            wrap = tk.Frame(self.conteudo_frame, bg=BG_DARK)
            wrap.pack(fill="both", expand=True, padx=30, pady=(0, 10))

            canvas = tk.Canvas(wrap, bg=BG_DARK, highlightthickness=0)
            container = tk.Frame(canvas, bg=BG_DARK)

            # janela interna dentro do Canvas
            canvas.create_window((0, 0), window=container, anchor="nw")

            # ativa atualização da região scrollável
            container.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

            # scroll invisível
            def _on_mousewheel(event):
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

            # só habilita scroll se houver mais de 4 perfis
            if len(perfis_para_mostrar) > 4:
                canvas.bind("<MouseWheel>", _on_mousewheel)

            canvas.pack(fill="both", expand=True)

            # Lista de perfis (cada linha com Abrir / Editar / Excluir)
            for p in perfis_para_mostrar:
                linha = tk.Frame(container, bg=BG_DARK)
                linha.pack(fill="x", padx=40, pady=10)

                rotulo = f"{p.get('nome', 'Sem Nome')} ({p.get('mes', 'sem mês')})"
                tk.Button(
                    linha, text=rotulo,
                    command=lambda perfil=p: self.abrir_janela_planilha(perfil),
                    font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                    activebackground="#444444", activeforeground=FG_ACTIVE,
                    relief="flat", bd=0, padx=10, pady=10
                ).pack(side="left", expand=True, fill="x")

                # Regras de edição/exclusão:
                # - Admin: pode editar/excluir qualquer perfil
                # - Não-admin: só pode editar/excluir perfis do qual é dono
                dono = (p.get("usuario_dono") or "")
                pode_alterar = (role == "admin") or (dono == usuario)

                btn_editar = tk.Button(
                    linha, text="Editar",
                    command=(lambda perfil=p: self.janela_editar_perfil(perfil)) if pode_alterar else None,
                    font=FONT_UI,
                    bg="#007acc" if pode_alterar else "#3a3a3a",
                    fg=FG_TEXT,
                    activebackground="#3399ff" if pode_alterar else "#3a3a3a",
                    activeforeground="#ffffff",
                    relief="flat", bd=0, padx=10, pady=10,
                    state=("normal" if pode_alterar else "disabled")
                )
                btn_editar.pack(side="right")

                btn_excluir = tk.Button(
                    linha, text="Excluir",
                    command=(lambda perfil=p: self.excluir_perfil(perfil)) if pode_alterar else None,
                    font=FONT_UI,
                    bg="#8b0000" if pode_alterar else "#3a3a3a",
                    fg=FG_TEXT,
                    activebackground="#aa0000" if pode_alterar else "#3a3a3a",
                    activeforeground=FG_TEXT,
                    relief="flat", bd=0, padx=10, pady=10,
                    state=("normal" if pode_alterar else "disabled")
                )
                btn_excluir.pack(side="right")

        # Botão: Criar Perfil
        tk.Button(
            self.conteudo_frame, text="Criar Perfil",
            command=self.janela_criar_perfil,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=10, pady=10
        ).pack(fill="x", padx=50, pady=5)

        # Botão: Exportar Perfis (Consolidado)
        def _exportar_filtrado():
            todos = self.perfis
            try:
                if role == "admin":
                    return self.exportar_todos_perfis_excel()
                else:
                    self.perfis = perfis_para_mostrar
                    return self.exportar_todos_perfis_excel()
            finally:
                self.perfis = todos


        role = (self.current_user or {}).get("role", "") or ""
        if role == "admin":
            tk.Button(
                self.conteudo_ri_frame if hasattr(self, "conteudo_ri_frame") else self.conteudo_frame,
                text="Troca de Loja (mês corrente)",
                command=self.abrir_wizard_troca_loja,
                font=FONT_UI, bg="#007acc", fg=FG_TEXT,
                activebackground="#3399ff", activeforeground="#ffffff",
                relief="flat", bd=0, padx=10, pady=10
            ).pack(fill="x", padx=50, pady=5)

     
        # Botão: Voltar
        tk.Button(
            self.conteudo_frame, text="Voltar",
            command=self.tela_inicial,
            font=FONT_UI, bg="#8b0000", fg=FG_TEXT,
            activebackground="#aa0000", activeforeground=FG_TEXT,
            relief="flat", bd=0, padx=10, pady=10
        ).pack(fill="x", padx=50, pady=5)

    def excluir_perfil(self, perfil):
        if messagebox.askyesno("Excluir Perfil", f"Excluir '{perfil['nome']}'?"):
            self.perfis = [p for p in self.perfis if p is not perfil]
            self.salvar_perfis()
            self.tela_controle_lojas()

    # --- Botão: Visualização (Minhas Lojas do mês) ---

    def _lojas_do_usuario_mes(self, username: str, mes_full: str) -> list[str]:
        """
        Retorna lista única de lojas vinculadas ao usuario_dono == username
        nos perfis do mês (formato 'Mês/Ano').
        """
        from tkinter import messagebox
        try:
            res = (supabase
                .table("perfis")
                .select("doc,mes,usuario_dono")
                .eq("mes", mes_full)
                .eq("usuario_dono", username)
                .execute())
            rows = res.data or []
        except Exception as e:
            messagebox.showerror("Visualização", f"Falha ao consultar perfis do usuário:\n{e}")
            return []

        s = set()
        for row in rows:
            doc = row.get("doc") or {}
            for lj in (doc.get("lojas") or []):
                nome = lj.get("loja")
                if nome:
                    s.add(str(nome))

        def _k(x):
            sx = str(x)
            return (0, int(sx)) if sx.isdigit() else (1, sx.lower())
        return sorted(s, key=_k)

    def abrir_visualizacao_minhas_global(self):
        """Abre um modal pedindo Mês/Ano e, em seguida, abre a visualização filtrada pelas MINHAS lojas."""
        import tkinter as tk
        from tkinter import messagebox

        # --- modal ---
        top = tk.Toplevel(self.master)
        top.title("Visualização (Minhas Lojas)")
        top.configure(bg=BG_DARK)
        top.resizable(False, False)
        top.grab_set()

        # Mês/Ano defaults
        from datetime import datetime
        ano_atual = datetime.now().year
        anos = [str(a) for a in range(ano_atual-2, ano_atual+3)]
        mes_atual = MESES_PTBR[datetime.now().month-1]

        mes_var = tk.StringVar(value=mes_atual)
        ano_var = tk.StringVar(value=str(ano_atual))

        frm = tk.Frame(top, bg=BG_DARK); frm.pack(padx=16, pady=16)

        tk.Label(frm, text="Mês:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).grid(row=0, column=0, padx=(0,6), sticky="e")
        mes_menu = tk.OptionMenu(frm, mes_var, *MESES_PTBR)
        mes_menu.configure(bg=BG_PANEL, fg=FG_TEXT, relief="flat", bd=0, highlightthickness=0, activebackground="#444444")
        mes_menu.grid(row=0, column=1, padx=(0,16), sticky="w")

        tk.Label(frm, text="Ano:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).grid(row=0, column=2, padx=(0,6), sticky="e")
        ano_menu = tk.OptionMenu(frm, ano_var, *anos)
        ano_menu.configure(bg=BG_PANEL, fg=FG_TEXT, relief="flat", bd=0, highlightthickness=0, activebackground="#444444")
        ano_menu.grid(row=0, column=3, sticky="w")

        def abrir():
            mes_full = f"{mes_var.get()}/{ano_var.get()}"
            username = (self.current_user or {}).get("username", "")
            lojas = self._lojas_do_usuario_mes(username, mes_full)
            if not lojas:
                messagebox.showinfo("Visualização", "Você não possui lojas vinculadas neste mês.")
                return

            # garante caminho do módulo
            try:
                import sys, importlib, pathlib
                here = pathlib.Path(__file__).parent
                if str(here) not in sys.path:
                    sys.path.insert(0, str(here))
                viz = importlib.import_module("visualizacao_depositos")
                VisualizacaoDepositosWindow = getattr(viz, "VisualizacaoDepositosWindow")
            except Exception as e:
                messagebox.showerror("Visualização", f"Não foi possível carregar a janela de visualização:\n{e}")
                return

            # Abre a janela filtrada
            
            VisualizacaoDepositosWindow(
                master=self.master,
                lojas_filter=lojas,        # lista de lojas vindas do Supabase (usuario_loja)
                mes=mes_var.get(),
                ano=int(ano_var.get()),
                titulo_extra=" • Minhas Lojas"
            )

            top.destroy()

        btns = tk.Frame(top, bg=BG_DARK); btns.pack(pady=(8,12))
        tk.Button(btns, text="Abrir", command=abrir,
                font=FONT_UI, bg="#007acc", fg=FG_TEXT,
                activebackground="#3399ff", activeforeground="#ffffff",
                relief="flat", bd=0, padx=12, pady=8).grid(row=0, column=0, padx=6)
        tk.Button(btns, text="Cancelar", command=top.destroy,
                font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                activebackground="#444444", activeforeground=FG_TEXT,
                relief="flat", bd=0, padx=12, pady=8).grid(row=0, column=1, padx=6)


    def janela_criar_perfil(self):
        import tkinter as tk
        from tkinter import messagebox
        from datetime import datetime

        janela = tk.Toplevel(self.master)
        janela.title("Criar Perfil")
        janela.configure(bg=BG_DARK)
        janela.geometry("680x640")

        # --- Cabeçalho / Nome do Perfil ---
        tk.Label(
            janela, text="Nome do Perfil:",
            font=FONT_UI, bg=BG_DARK, fg=FG_TEXT
        ).pack(anchor="w", padx=20, pady=(20, 6))

        nome_var = tk.StringVar()

        # Nome padrão = username logado (sem importar prisma)
        try:
            if self.current_user:
                nome_var.set(self.current_user.get("username", ""))
        except Exception:
            pass

        tk.Entry(
            janela, textvariable=nome_var,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            insertbackground=FG_TEXT, relief="flat"
        ).pack(fill="x", padx=20)

        # --- Ano/Mês (default para mês/ano atuais) ---
        ano_atual = datetime.now().year
        meses = MESES_PTBR[:]
        anos = [str(a) for a in range(ano_atual - 2, ano_atual + 3)]

        ano_var = tk.StringVar(value=str(ano_atual))
        mes_var = tk.StringVar(value=MESES_PTBR[datetime.now().month - 1])

        linha_mes = tk.Frame(janela, bg=BG_DARK)
        linha_mes.pack(fill="x", padx=20, pady=(10, 0))

        tk.Label(linha_mes, text="Ano:", font=FONT_UI, bg=BG_DARK, fg=FG_TEXT)\
            .grid(row=0, column=0, sticky="w", padx=(0, 8))
        ano_menu = tk.OptionMenu(linha_mes, ano_var, *anos)
        ano_menu.configure(
            bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
            activeforeground=FG_ACTIVE, relief="flat", bd=0, highlightthickness=0
        )
        ano_menu.grid(row=0, column=1, sticky="w", padx=(0, 16))

        tk.Label(linha_mes, text="Mês:", font=FONT_UI, bg=BG_DARK, fg=FG_TEXT)\
            .grid(row=0, column=2, sticky="w", padx=(0, 8))
        mes_menu = tk.OptionMenu(linha_mes, mes_var, *meses)
        mes_menu.configure(
            bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
            activeforeground=FG_ACTIVE, relief="flat", bd=0, highlightthickness=0
        )
        mes_menu.grid(row=0, column=3, sticky="w")

        # --- Lista de lojas agrupadas por regional ---
        tk.Label(
            janela, text="Selecione as lojas:",
            font=FONT_UI, bg=BG_DARK, fg=FG_TEXT
        ).pack(anchor="w", padx=20, pady=(12, 6))

        canvas = tk.Canvas(janela, bg=BG_DARK, highlightthickness=0)
        frame_checks = tk.Frame(canvas, bg=BG_DARK)
        canvas.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        canvas.create_window((0, 0), window=frame_checks, anchor="nw")
        frame_checks.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _on_mousewheel(event):
            delta = event.delta
            if delta == 0:
                return
            canvas.yview_scroll(-1 if delta > 0 else 1, "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)

        selecoes = []
        for reg, lista in self.agrupar_por_regional(self.lojas):
            tk.Label(
                frame_checks, text=f" {reg}",
                font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=FG_ACTIVE
            ).pack(anchor="w", pady=(10, 4))
            for loja in lista:
                var = tk.BooleanVar(value=False)
                tk.Checkbutton(
                    frame_checks, text=loja["loja"], variable=var,
                    font=FONT_UI, bg=BG_DARK, fg=FG_TEXT,
                    activeforeground=FG_ACTIVE, activebackground=BG_DARK,
                    selectcolor=BG_DARK
                ).pack(anchor="w", padx=12, pady=2)
                selecoes.append((var, loja))

        # --- Botões Salvar / Cancelar ---
        def salvar():
            nome = nome_var.get().strip()
            ano = ano_var.get().strip()
            mes_nome = mes_var.get().strip()

            if not nome:
                messagebox.showwarning("Atenção", "Informe o nome do perfil.")
                return
            if not ano.isdigit():
                messagebox.showwarning("Atenção", "Selecione um ano válido.")
                return
            if mes_nome not in MESES_PTBR:
                messagebox.showwarning("Atenção", "Selecione um mês válido.")
                return

            mes_full = f"{mes_nome}/{ano}"

            # Evita duplicar perfil (mesmo nome + mesmo mês)
            if any(
                p["nome"].lower() == nome.lower()
                and str(p.get("mes", "")).lower() == mes_full.lower()
                for p in self.perfis
            ):
                messagebox.showwarning(
                    "Atenção",
                    f"Já existe um perfil '{nome}' para '{mes_full}'."
                )
                return

            lojas_sel = [loja for var, loja in selecoes if var.get()]

            # Dono do perfil = username logado
            dono_username = ""
            try:
                if self.current_user:
                    dono_username = self.current_user.get("username", "")
            except Exception:
                pass

            self.perfis.append({
                "nome": nome,
                "mes": mes_full,
                "lojas": lojas_sel,
                "planilha": [],
                "observacoes": [],
                "vouchers": [],
                "usuario_dono": dono_username,  # vínculo do perfil com o usuário atual
            })

            self.salvar_perfis()
            janela.destroy()
            self.tela_controle_lojas()

        tk.Button(
            janela, text="Salvar", command=salvar,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).pack(pady=12)

        tk.Button(
            janela, text="Cancelar", command=janela.destroy,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).pack()



    def abrir_janela_planilha(self, perfil):
        janela = tk.Toplevel(self.winfo_toplevel())
        janela.title(f"Planilha — {perfil['nome']} ({perfil['mes']})")
        janela.configure(bg=BG_DARK)
        janela.state("zoomed")
        
        janela.update_idletasks()
        janela.minsize(1024, 600)

        self.mapa_celulas = {}   # (loja_nome, dia) -> label

        tk.Label(janela, text=f"Perfil: {perfil['nome']} — {perfil['mes']}",
                font=("Segoe UI", 14, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(pady=(10, 10))

        mes_nome, ano_str = perfil["mes"].split("/")
        ano = int(ano_str)
        mes_num = MESES_PTBR.index(mes_nome) + 1
        dias_mes = calendar.monthrange(ano, mes_num)[1]

        tabela_frame = tk.Frame(janela, bg=BG_DARK)
        tabela_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.criar_legenda_cores(janela)

        tk.Label(tabela_frame, text="Loja", font=FONT_UI, bg=BG_DARK, fg=FG_TEXT, width=10).grid(row=0, column=0, sticky="nsew")
        tk.Label(tabela_frame, text="CNPJ", font=FONT_UI, bg=BG_DARK, fg=FG_TEXT, width=8).grid(row=0, column=1, sticky="nsew")
        
        for d in range(1, dias_mes + 1):
            tk.Label(tabela_frame, text=str(d), font=FONT_UI, bg="#000000", fg=FG_TEXT, width=6, relief="solid", bd=1).grid(row=0, column=d + 1, sticky="nsew")

        if "planilha" not in perfil or not perfil["planilha"]:
            perfil["planilha"] = []
            for loja in perfil["lojas"]:
                perfil["planilha"].append({"loja": loja["loja"], "cnpj": loja["cnpj"], "dias": {}})


        def aplicar_status(status, dia, loja, lbl):
            """
            Registra a ação (com data/hora e origem), mantém compatibilidade com a estrutura atual
            e, no caso de 'Comprovante GoodCard', agenda lembrete de +7.

            >>> NOVO: bloqueio de edição fora da vigência da loja neste perfil.
            """
            from datetime import datetime, timedelta
            # ---------------- BLOQUEIO POR VIGÊNCIA ----------------
            try:
                # 'dia' chega como string; convertemos para int
                d_int = int(dia)
            except Exception:
                d_int = None

            # dias_mes veio do contexto já calculado no início da função
            if d_int is None or not self._dia_dentro_vigencia(loja, d_int, dias_mes):
                try:
                    v = (loja or {}).get("vigencia") or {}
                    ini = v.get("ini", 1)
                    fim = v.get("fim", dias_mes)
                    messagebox.showinfo(
                        "Edição bloqueada",
                        f"Dia {dia} está fora da vigência desta loja neste perfil.\n"
                        f"Vigência: {ini}–{fim}."
                    )
                except Exception:
                    pass
                return
            # -------------------------------------------------------

            # ---------- Garantir estrutura de metadados por loja/dia ----------
            # loja: dict como {"loja": "...", "cnpj": "...", "dias": {...}, "meta": {...}}
            if "meta" not in loja:
                loja["meta"] = {}
            if dia not in loja["meta"]:
                loja["meta"][dia] = {"logs": []}  # cada log: {status, data_hora, origem, auto_goodcard}
            meta_dia = loja["meta"][dia]

            # Horário agora (uma única referência temporal para esta ação manual)
            agora = datetime.now()

            # Função auxiliar para anexar logs de ações
            def _append_log(dia_ref: str, status_ref: str, origem_ref: str, flag_auto: bool = False, ts: datetime = None):
                if "meta" not in loja:
                    loja["meta"] = {}
                if dia_ref not in loja["meta"]:
                    loja["meta"][dia_ref] = {"logs": []}
                loja["meta"][dia_ref]["logs"].append({
                    "status": status_ref,
                    "data_hora": (ts or agora).strftime("%Y-%m-%d %H:%M:%S"),
                    "origem": origem_ref,  # "manual" | "automatica"
                    "auto_goodcard": bool(flag_auto)  # True somente para o +7 automático
                })

            # ---------- Cálculo do limite (fim do dia D + PRAZO_DIAS) ----------
            if not hasattr(self, "PRAZO_FINALIZACAO_DIAS"):
                self.PRAZO_FINALIZACAO_DIAS = 2
            PRAZO_DIAS = int(self.PRAZO_FINALIZACAO_DIAS)
            try:
                dia_int = int(dia)
                data_base = datetime(ano, mes_num, dia_int)  # 'ano' e 'mes_num' já definidos acima
                limite = self.somar_dias_ignorando_domingo(data_base, PRAZO_DIAS)
                limite = limite.replace(hour=23, minute=59, second=59, microsecond=999999)
            except Exception:
                limite = agora

            # ---------- Marcação visual e persistência do status ----------
            loja["dias"][dia] = status
            cor = CORES_STATUS.get(status, BG_PANEL)

            # Mantém o número da loja quando a célula está "vazia" (como já era)
            numero_loja = normalizar_loja_valor(loja["loja"])
            texto_celula = "" if (status and status.strip() != "") else numero_loja
            lbl.configure(bg=cor, text=texto_celula, anchor="center", relief="solid", bd=1)

            # ---------- LOG da ação MANUAL ----------
            _append_log(dia, status, origem_ref="manual", flag_auto=False, ts=agora)

            # Primeira ação manual & pendência no prazo
            if "primeira_acao_manual_ts" not in meta_dia:
                meta_dia["primeira_acao_manual_ts"] = agora.strftime("%Y-%m-%d %H:%M:%S")
                meta_dia["primeira_acao_manual_status"] = status

            # Se for pendência (qualquer status != Finalizado/Domingo/"") dentro do prazo, congela o prazo
            if status not in ("Finalizado", "Domingo", "") and agora <= limite:
                meta_dia["houve_pendencia_no_prazo"] = True

            # Se finalizou agora, registra o timestamp e desativa lembrete
            if status == "Finalizado":
                meta_dia["finalizado_ts"] = agora.strftime("%Y-%m-%d %H:%M:%S")
                try:
                    meta_dia["lembrete_ativo"] = False
                except Exception:
                    pass

            # --- Regra especial: GoodCard --- (não pinta D+7; apenas agenda lembrete)
            if status == "Comprovante GoodCard":
                try:
                    meta_dia["goodcard_ts"] = agora.strftime("%Y-%m-%d %H:%M:%S")
                    prazo_fim = self.somar_dias_ignorando_domingo(data_base, 7)
                    prazo_fim = prazo_fim.replace(hour=23, minute=59, second=59, microsecond=999999)
                    meta_dia["prazo_gc_fim"] = prazo_fim.strftime("%Y-%m-%d %H:%M:%S")
                    meta_dia["lembrete_ativo"] = True
                except Exception:
                    pass

            # Atualiza o painel lateral (já existia)
            atualizar_quadro_resultados()

            # Se a janela de resultados estiver aberta, atualiza os cards em tempo real
            if hasattr(self, "_cards_labels_resultados_ref") and self._cards_labels_resultados_ref:
                try:
                    self.atualizar_cards_resultados(perfil)
                except Exception:
                    pass

        for i, loja in enumerate(perfil["lojas"], start=1):
            tk.Label(tabela_frame, text=loja["loja"], font=FONT_UI, bg=BG_DARK, fg=FG_TEXT).grid(row=i, column=0, sticky="nsew")
            cnpj_curto = cnpj_ultimos4(loja["cnpj"])
            tk.Label(tabela_frame, text=cnpj_curto, font=FONT_UI, bg=BG_DARK, fg=FG_TEXT).grid(row=i, column=1, sticky="nsew")

            dados_loja = next((p for p in perfil["planilha"] if p["loja"] == loja["loja"]), None)
            if not dados_loja:
                dados_loja = {"loja": loja["loja"], "cnpj": loja["cnpj"], "dias": {}}
                perfil["planilha"].append(dados_loja)

            for d in range(1, dias_mes + 1):
                dia_str = str(d)
                valor_real = dados_loja["dias"].get(dia_str, "")


                if (
                    not valor_real
                    and calendar.weekday(ano, mes_num, d) == 6
                    and not self.loja_abre_domingo(loja["loja"])
                ):
                    valor_real = "Domingo"

                # sempre atualizar o valor final da célula
                dados_loja["dias"][dia_str] = valor_real


                cor_inicial = CORES_STATUS.get(valor_real, BG_PANEL)
                numero_loja = normalizar_loja_valor(loja["loja"])
      
                # Se não houver status (string vazia), exibe o número; caso contrário, deixa em branco
                texto_celula = numero_loja if (valor_real == "" or valor_real is None) else ""

                lbl = tk.Label(
                    tabela_frame, text=texto_celula, bg=cor_inicial,
                    width=7, height=1, relief="solid", bd=1,
                    anchor="center", font=("Segoe UI", 7), fg="#c0c0c0"
                )
                lbl.grid(row=i, column=d+1, sticky="nsew")

                def escolher_status(event, dia_ref=dia_str, loja_ref=dados_loja, lbl_ref=lbl):
                    menu = tk.Menu(janela, tearoff=0, bg=BG_PANEL, fg=FG_TEXT)
                    for status in STATUS_OPCOES:
                        menu.add_command(label=status,
                                        command=lambda s=status, dia=dia_ref, loja=loja_ref, lbl=lbl_ref:
                                        aplicar_status(s, dia, loja, lbl))
                    menu.tk_popup(event.x_root, event.y_root)

                lbl.bind("<Button-1>", escolher_status)

                self.mapa_celulas[(loja["loja"], dia_str)] = lbl

        btn_frame = tk.Frame(janela, bg=BG_DARK)
        btn_frame.pack(after=tabela_frame, pady=(4, 4), anchor="center")

        def salvar():
            self.salvar_perfis()
            messagebox.showinfo("Sucesso", "Planilha salva com sucesso!")
            atualizar_quadro_resultados()

        tk.Button(
            btn_frame, text="Salvar", command=salvar,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=0, padx=8)

        tk.Button(
            btn_frame, text="Voucher", command=lambda: self.abrir_voucher(perfil),
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=2, padx=8)

        tk.Button(
            btn_frame, text="Avaliação", command=lambda: self.abrir_avaliacao(perfil),
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=3, padx=8)

        #Resultados movidos para outra área

        #tk.Button(
        #    btn_frame,
        #    text="Resultados",
        #    command=lambda: self.abrir_resultados(perfil),
        #    font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
        #   activebackground="#444444", activeforeground=FG_ACTIVE,
        #    relief="flat", bd=0, padx=12, pady=8
        #).grid(row=0, column=4, padx=8)


        tk.Button(
            btn_frame, text="Voltar", command=janela.destroy,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=5, padx=8)

        conteudo_inferior = tk.Frame(janela, bg=BG_DARK)
        conteudo_inferior.pack(fill="both", expand=True, padx=10, pady=(0, 20))

        painel_esquerdo = tk.Frame(conteudo_inferior, bg=BG_DARK)
        painel_esquerdo.configure(width=800)
        painel_esquerdo.pack(side="left", fill="both", expand=True)

        painel_direito = tk.Frame(conteudo_inferior, bg=BG_DARK)
        painel_direito.configure(width=500)  # largura maior para evitar corte
        painel_direito.pack_propagate(False)
        painel_direito.pack(side="right", fill="y")

        if "observacoes" not in perfil:
            perfil["observacoes"] = []

        obs_canvas = tk.Canvas(painel_esquerdo, bg=BG_DARK, highlightthickness=0, width=780)
        scrollbar = tk.Scrollbar(painel_esquerdo, orient="vertical", command=obs_canvas.yview)
        obs_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack_forget()  

        obs_frame = tk.Frame(obs_canvas, bg=BG_DARK)
        obs_canvas.pack(side="left", fill="both", expand=True)
        obs_canvas.create_window((0, 0), window=obs_frame, anchor="nw")

        obs_frame.bind("<Configure>", lambda e: obs_canvas.configure(scrollregion=obs_canvas.bbox("all")))
        obs_canvas.bind("<MouseWheel>", lambda e: obs_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

        tk.Label(obs_frame, text="Adicionar Observação:",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT, anchor="w").pack(anchor="w")

        linha_obs = tk.Frame(obs_frame, bg=BG_DARK)
        linha_obs.pack(fill="x", pady=6)


        # --- Observações: Campo LOJA (robusto a lista vazia) ---
        tk.Label(linha_obs, text="Loja:", bg=BG_DARK, fg=FG_TEXT).grid(row=0, column=0, padx=5)

        loja_var = tk.StringVar()

        # Nomes das lojas deste perfil (pode estar vazio, dependendo da troca)
        lojas_nomes = [l["loja"] for l in perfil["lojas"]]

        if lojas_nomes:
            # Define o valor inicial e cria o OptionMenu com a lista
            loja_var.set(lojas_nomes[0])
            loja_menu = tk.OptionMenu(linha_obs, loja_var, *lojas_nomes)
        else:
            # Placeholder para evitar TypeError e sinalizar que não há lojas no perfil
            placeholder = "(sem lojas)"
            loja_var.set(placeholder)
            loja_menu = tk.OptionMenu(linha_obs, loja_var, placeholder)
            loja_menu.configure(state="disabled")  # desabilitado quando vazio

        loja_menu.configure(bg=BG_PANEL, fg=FG_TEXT, relief="flat")
        loja_menu.grid(row=0, column=1, padx=5)


        tk.Label(linha_obs, text="Dia:", bg=BG_DARK, fg=FG_TEXT).grid(row=0, column=2, padx=5)
        dia_var = tk.StringVar()
        dias_lista = [str(d) for d in range(1, dias_mes+1)]
        dia_menu = tk.OptionMenu(linha_obs, dia_var, *dias_lista)
        dia_menu.configure(bg=BG_PANEL, fg=FG_TEXT, relief="flat")
        dia_menu.grid(row=0, column=3, padx=5)

        tk.Label(linha_obs, text="O que aconteceu:", bg=BG_DARK, fg=FG_TEXT).grid(row=0, column=4, padx=5)
        texto_box = tk.Text(linha_obs, width=60, height=3,
                            bg=BG_PANEL, fg=FG_TEXT,
                            insertbackground=FG_TEXT, relief="flat", wrap="word")
        texto_box.grid(row=0, column=5, padx=5)

        def adicionar_obs():
            loja = loja_var.get()
            dia = dia_var.get()
            texto = texto_box.get("1.0", "end").strip()
            if loja and dia and texto:
                ja_existe = any(obs["loja"] == loja and obs["dia"] == dia and obs["texto"] == texto for obs in perfil["observacoes"])
                if not ja_existe:
                    perfil["observacoes"].append({"loja": loja, "dia": dia, "texto": texto})
                    self.salvar_perfis()
                    texto_box.delete("1.0", "end")
                    atualizar_tabela_obs()
                    atualizar_quadro_resultados()
                else:
                    messagebox.showinfo("Aviso", "Essa observação já foi registrada.")

        tk.Button(linha_obs, text="Adicionar", command=adicionar_obs,
                font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                activebackground="#444444", activeforeground=FG_ACTIVE,
                relief="flat", bd=0, padx=12, pady=6).grid(row=0, column=6, padx=5)

        # --- FILTRO DE OBSERVAÇÕES (VISUAL) ---
        filtro_obs_frame = tk.Frame(obs_frame, bg=BG_DARK)
        filtro_obs_frame.pack(fill="x", pady=(10, 6), anchor="w")

        tk.Label(
            filtro_obs_frame,
            text="Observações – Filtrar por loja:",
            bg=BG_DARK,
            fg=FG_TEXT,
            font=("Segoe UI", 9, "bold")
        ).pack(side="left")

        obs_loja_filtro_var = tk.StringVar(value="Todas")

        cb_obs_loja_filtro = ttk.Combobox(
            filtro_obs_frame,
            textvariable=obs_loja_filtro_var,
            state="readonly",
            width=22
        )
        cb_obs_loja_filtro.pack(side="left", padx=6)

        obs_table = tk.Frame(obs_frame, bg=BG_DARK)
        obs_table.pack(fill="x", pady=(10, 0), anchor="w")


        def atualizar_tabela_obs():
            for w in obs_table.winfo_children():
                w.destroy()

            loja_filtro = obs_loja_filtro_var.get()

            observacoes = perfil.get("observacoes", [])
            linha = 1
            encontrou = False

            for obs in observacoes:
                nome_loja = obs.get("loja", "")

                if loja_filtro != "Todas" and nome_loja != loja_filtro:
                    continue

                encontrou = True
                texto = f"Loja {nome_loja} - Dia {obs['dia']}: {obs['texto']}"

                tk.Label(
                    obs_table,
                    text=texto,
                    bg=BG_DARK,
                    fg=FG_TEXT,
                    anchor="w",
                    justify="left",
                    wraplength=720,
                    font=("Segoe UI", 9)
                ).grid(row=linha, column=0, sticky="w", padx=10, pady=2)

                def excluir_obs(index=observacoes.index(obs)):
                    if messagebox.askyesno("Excluir", "Deseja excluir esta observação?"):
                        perfil["observacoes"].pop(index)
                        self.salvar_perfis()
                        atualizar_tabela_obs()
                        atualizar_quadro_resultados()

                tk.Button(
                    obs_table,
                    text="Excluir",
                    command=excluir_obs,
                    font=FONT_UI,
                    bg="#8b0000",
                    fg=FG_TEXT,
                    activebackground="#aa0000",
                    activeforeground=FG_ACTIVE,
                    relief="flat",
                    bd=0,
                    padx=8,
                    pady=2,
                    width=8
                ).grid(row=linha, column=1, padx=6, pady=2)

                linha += 1

            if not encontrou:
                tk.Label(
                    obs_table,
                    text="Nenhuma observação para esta loja.",
                    bg=BG_DARK,
                    fg="#b0b0b0",
                    anchor="w",
                    font=("Segoe UI", 9)
                ).grid(row=1, column=0, sticky="w", padx=10, pady=6)

        def atualizar_filtro_obs():
            lojas = sorted({obs["loja"] for obs in perfil.get("observacoes", [])})
            valores = ["Todas"] + lojas

            cb_obs_loja_filtro["values"] = valores

            if obs_loja_filtro_var.get() not in valores:
                obs_loja_filtro_var.set("Todas")

        
        cb_obs_loja_filtro.bind(
            "<<ComboboxSelected>>",
            lambda e: atualizar_tabela_obs()
        )
                
        atualizar_filtro_obs()
        atualizar_tabela_obs()

        quadro_resultados = tk.Frame(painel_direito, bg=BG_DARK)
        quadro_resultados.pack(anchor="n", padx=10, pady=20)




        def atualizar_quadro_resultados():
            # limpa o painel
            for w in quadro_resultados.winfo_children():
                w.destroy()

            tk.Label(
                quadro_resultados, text="Quadro de Resultados:",
                font=("Segoe UI", 8, "bold"),
                bg=BG_DARK, fg=FG_TEXT
            ).pack(anchor="w", pady=(0, 6))

            # Garantias básicas
            if "planilha" not in perfil or perfil["planilha"] is None:
                perfil["planilha"] = []

            # Garante que a planilha tenha uma entrada por loja do perfil
            nomes_lojas = {l["loja"] for l in perfil["lojas"]}
            existentes = {p["loja"] for p in perfil["planilha"]}
            for loja in perfil["lojas"]:
                if loja["loja"] not in existentes:
                    perfil["planilha"].append({"loja": loja["loja"], "cnpj": loja["cnpj"], "dias": {}})
            perfil["planilha"] = [p for p in perfil["planilha"] if p["loja"] in nomes_lojas]

            total_finalizados = 0
            total_dias_validos = 0
            linhas_texto = []

            # >>> Contexto do mês (já calculado acima; reusamos variáveis locais)
            # ano, mes_num e dias_mes foram definidos no começo de abrir_janela_planilha
            # (se não estiverem no escopo, recalcule como no restante do arquivo)
            import calendar

            for loja in perfil["planilha"]:
                dias_validos = []
                dias_finalizados = []

                for d in range(1, dias_mes + 1):
                    dia_str = str(d)

                    # >>> NOVO: respeitar vigência da loja neste perfil
                    if not self._dia_dentro_vigencia(loja, d, dias_mes):
                        continue  # fora da vigência não entra no denominador

                    status = loja["dias"].get(dia_str, "")

                    # Regras já existentes: domingo não conta (a menos que Finalizado)
                    if calendar.weekday(ano, mes_num, d) == 6 and status != "Finalizado":
                        continue
                    if status == "Domingo":
                        continue

                    dias_validos.append(d)
                    if status == "Finalizado":
                        dias_finalizados.append(d)

                perc = (len(dias_finalizados) / len(dias_validos)) * 100 if dias_validos else 0.0
                total_finalizados += len(dias_finalizados)
                total_dias_validos += len(dias_validos)

                linhas_texto.append(
                    f"Loja {loja['loja']}: {len(dias_finalizados)} de {len(dias_validos)} dias — {perc:.1f}% Finalizado"
                )

            # Montagem visual (duas colunas, como estava)
            resumo_final = ""
            if total_dias_validos:
                geral = (total_finalizados / total_dias_validos) * 100
                resumo_final = f"Total no mês: {total_finalizados} de {total_dias_validos} dias — {geral:.1f}% Finalizado"

            n = len(linhas_texto)
            if n == 0:
                if resumo_final:
                    tk.Label(
                        quadro_resultados, text=resumo_final,
                        font=("Segoe UI", 10, "bold"), bg=BG_DARK, fg=FG_TEXT,
                        justify="left", anchor="w"
                    ).pack(anchor="w", pady=(6, 0))
                return

            import math
            itens_por_coluna = math.ceil(n / 2)
            cols_frame = tk.Frame(quadro_resultados, bg=BG_DARK)
            cols_frame.pack(fill="x", expand=False, anchor="w")

            col_esq = tk.Frame(cols_frame, bg=BG_DARK)
            col_esq.grid(row=0, column=0, sticky="nw", padx=(0, 24))
            col_dir = tk.Frame(cols_frame, bg=BG_DARK)
            col_dir.grid(row=0, column=1, sticky="nw")

            for i in range(itens_por_coluna):
                tk.Label(col_esq, text=linhas_texto[i], bg=BG_DARK, fg=FG_TEXT,
                        anchor="w", justify="left").pack(anchor="w")
            for i in range(itens_por_coluna, n):
                tk.Label(col_dir, text=linhas_texto[i], bg=BG_DARK, fg=FG_TEXT,
                        anchor="w", justify="left").pack(anchor="w")

            if resumo_final:
                tk.Label(
                    quadro_resultados, text=resumo_final,
                    font=("Segoe UI", 8, "bold"), bg=BG_DARK, fg=FG_TEXT,
                    wraplength=480, justify="left", anchor="w"
                ).pack(anchor="w", pady=(8, 0))


        atualizar_quadro_resultados()

                # --- Iniciar watcher de prazos (GoodCard) para este perfil ---
        self._deadline_stop_flag = False
        self._deadline_watcher(perfil, janela_ref=janela, intervalo_ms=3600000)  # 60 s (ajustável)

        
        def _on_close():
            try:
                self._deadline_stop_flag = True
            except Exception:
                pass
            janela.destroy()

        janela.protocol("WM_DELETE_WINDOW", _on_close)

    def somar_dias_ignorando_domingo(self, data_inicial, dias):
        """
        Soma 'dias' sem contar domingos.
        Ex: se dias=2 e cai domingo no meio, ignora domingo.
        """
        from datetime import timedelta

        data = data_inicial
        adicionados = 0

        while adicionados < dias:
            data += timedelta(days=1)
            # weekday(): 0=segunda ... 6=domingo
            if data.weekday() != 6:   # ignora domingo
                adicionados += 1

        return data

    def calcular_metricas_resultados(self, perfil):
        """
        Calcula as quatro porcentagens:
        - finalizados no prazo
        - finalizados fora do prazo
        - sem pendência (finalizados no prazo sem pendência)
        - com pendência fora do prazo

        Usa:
        loja["meta"][dia]["primeira_acao_manual_ts"]
        loja["meta"][dia]["primeira_acao_manual_status"]
        loja["meta"][dia]["houve_pendencia_no_prazo"]
        loja["meta"][dia]["finalizado_ts"]
        loja["meta"][dia]["logs"]

        IGNORA GoodCard +7 (auto_goodcard=True)

        >>> NOVO: respeita a vigência por loja no perfil (self._dia_dentro_vigencia),
            para não penalizar auditores após troca de loja.
        """
        from datetime import datetime
        import calendar

        # Se não houver planilha, retorna tudo zerado
        if "planilha" not in perfil:
            return {
                "sem_pend_dentro": 0,
                "sem_pend_fora": 0,
                "com_pend_dentro": 0,
                "com_pend_fora": 0,
            }

        # Contexto do mês
        mes_nome, ano_str = perfil["mes"].split("/")
        ano = int(ano_str)
        mes_num = MESES_PTBR.index(mes_nome) + 1
        dias_mes = calendar.monthrange(ano, mes_num)[1]

        PRAZO = getattr(self, "PRAZO_FINALIZACAO_DIAS", 2)

        # Contadores
        tot_finalizados = 0
        c_sem_pend_dentro = 0
        c_sem_pend_fora = 0
        c_com_pend_dentro = 0
        c_com_pend_fora = 0

        def ts_parse(x):
            try:
                return datetime.strptime(x["data_hora"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return datetime.now()

        for loja in perfil["planilha"]:
            dias_loja = loja.get("dias", {})
            meta_loja = loja.get("meta", {})

            for d in range(1, dias_mes + 1):
                dia_str = str(d)

                # >>> BLOQUEIO POR VIGÊNCIA (NOVIDADE)
                if not self._dia_dentro_vigencia(loja, d, dias_mes):
                    continue

                status_final = dias_loja.get(dia_str, "")

                # Ignorar domingos não finalizados
                if calendar.weekday(ano, mes_num, d) == 6 and status_final != "Finalizado":
                    continue
                if status_final == "Domingo":
                    continue

                meta_dia = meta_loja.get(dia_str, {})
                logs = meta_dia.get("logs", [])

                # Apenas logs manuais contam para primeira ação
                logs_manuais = [l for l in logs if l.get("origem") == "manual"]
                logs_manuais.sort(key=ts_parse)

                primeira_ts, primeira_status = None, ""
                if logs_manuais:
                    primeira_log = logs_manuais[0]
                    primeira_ts = ts_parse(primeira_log)
                    primeira_status = primeira_log.get("status", "")

                # Cálculo do limite do prazo (D + PRAZO ignorando domingos)
                base = datetime(ano, mes_num, d)
                limite = self.somar_dias_ignorando_domingo(base, PRAZO).replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )

                # Determinar se o dia finalizou
                finalizado_ts = None
                if status_final == "Finalizado":
                    ts_text = meta_dia.get("finalizado_ts")
                    if ts_text:
                        try:
                            finalizado_ts = datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            finalizado_ts = None

                # Se não finalizou → não entra no denominador
                if finalizado_ts is None:
                    continue

                # Houve pendência manual ANTES do limite?
                houve_pendencia_previa = False
                for l in logs_manuais:
                    st = l.get("status", "")
                    if st not in ("Finalizado", "Domingo", ""):
                        if ts_parse(l) <= limite:
                            houve_pendencia_previa = True
                            break

                tot_finalizados += 1

                # SEM pendência
                if not houve_pendencia_previa and primeira_status == "Finalizado":
                    if primeira_ts and primeira_ts <= limite:
                        c_sem_pend_dentro += 1
                    else:
                        c_sem_pend_fora += 1

                # COM pendência
                else:
                    if finalizado_ts <= limite:
                        c_com_pend_dentro += 1
                    else:
                        c_com_pend_fora += 1

        def pct(v):
            return 0 if tot_finalizados == 0 else round((v / tot_finalizados) * 100)

        return {
            "sem_pend_dentro": pct(c_sem_pend_dentro),
            "sem_pend_fora": pct(c_sem_pend_fora),
            "com_pend_dentro": pct(c_com_pend_dentro),
            "com_pend_fora": pct(c_com_pend_fora),
        }


    def atualizar_cards_resultados(self, perfil, janela=None):
        """
        Atualiza os 4 cards da janela 'Resultados' com as porcentagens calculadas.
        Requer que a janela tenha o atributo _cards_labels_resultados (criado em abrir_resultados).
        """
        try:
            metr = self.calcular_metricas_resultados(perfil)
        except Exception as e:
            messagebox.showerror("Resultados", f"Falha ao calcular métricas: {e}")
            return

        # Localiza o dicionário de labels
        labels = None
        if janela is not None and hasattr(janela, "_cards_labels_resultados"):
            labels = janela._cards_labels_resultados
        elif hasattr(self, "_cards_labels_resultados_ref"):
            labels = self._cards_labels_resultados_ref

        if not labels:
            return

        def fmt(p):  # exibe só a porcentagem, arredondada para inteiro
            try:
                return f"{int(round(p))}%"
            except Exception:
                return "0%"


        labels["sem_pend_dentro"].configure(text=fmt(metr.get("sem_pend_dentro", 0)))
        labels["sem_pend_fora"].configure(text=fmt(metr.get("sem_pend_fora", 0)))
        labels["com_pend_dentro"].configure(text=fmt(metr.get("com_pend_dentro", 0)))
        labels["com_pend_fora"].configure(text=fmt(metr.get("com_pend_fora", 0)))

    def _is_finalizado(self, loja_dict, dia_str) -> bool:
        """Retorna True se o status do dia for Finalizado."""
        try:
            return loja_dict.get("dias", {}).get(dia_str, "") == "Finalizado"
        except Exception:
            return False

    def _deadline_items_vencidos(self, perfil):
        """
        Varre o perfil e retorna lista de (loja_nome, dia_str, prazo_fim_dt)
        para os casos com GoodCard cujo prazo já estourou e o lembrete está ativo.
        """
        from datetime import datetime
        vencidos = []
        # contexto do mês
        mes_nome, ano_str = perfil["mes"].split("/")
        ano = int(ano_str)
        mes_num = MESES_PTBR.index(mes_nome) + 1

        for loja in perfil.get("planilha", []):
            meta = loja.get("meta", {})
            for dia_str, info in meta.items():
                try:
                    # só considera se há lembrete ativo
                    if not info.get("lembrete_ativo", False):
                        continue
                    # não precisa lembrar se já finalizou
                    if self._is_finalizado(loja, dia_str):
                        continue
                    prazo_txt = info.get("prazo_gc_fim")
                    if not prazo_txt:
                        continue
                    prazo_dt = datetime.strptime(prazo_txt, "%Y-%m-%d %H:%M:%S")
                    agora = datetime.now()
                    if agora > prazo_dt:
                        # prazo estourou
                        vencidos.append((loja.get("loja", ""), dia_str, prazo_dt))
                except Exception:
                    continue
        return vencidos

    def _deadline_watcher(self, perfil, janela_ref=None, intervalo_ms=3600000):
        """
        Checador recorrente: a cada 'intervalo_ms' verifica prazos vencidos
        e exibe popup até que os dias sejam finalizados.
        """
        try:
            # se a janela foi fechada ou a flag global desligada, pare
            if getattr(self, "_deadline_stop_flag", False):
                return
            # se quiser parar quando a janela do perfil for fechada
            if janela_ref is not None and not janela_ref.winfo_exists():
                return

            # coleta vencidos
            vencidos = self._deadline_items_vencidos(perfil)
            if vencidos:
                # monta mensagem única para evitar "tempestade" de popups
                linhas = []
                for loja_nome, dia_str, prazo_dt in vencidos:
                    linhas.append(f"Loja {loja_nome} — Dia {dia_str} (prazo: {prazo_dt.strftime('%d/%m/%Y')})")
                msg = (
                    "⏰ Prazo encerrado para GoodCard:\n\n"
                    + "\n".join(linhas) +
                    "\n\nFinalize o(s) dia(s) para parar estes avisos."
                )
                # Mostra popup (não bloqueia a repetição futura; repete enquanto não finalizado)
                try:
                    messagebox.showwarning("Prazo Encerrado", msg)
                except Exception:
                    pass
            # reagenda nova checagem
            self.after(intervalo_ms, lambda: self._deadline_watcher(perfil, janela_ref, intervalo_ms))
        except Exception:
            # em qualquer erro, tente reagendar
            try:
                self.after(intervalo_ms, lambda: self._deadline_watcher(perfil, janela_ref, intervalo_ms))
            except Exception:
                pass

# --- cole abaixo de atualizar_cards_resultados(...) ou em local equivalente dentro da classe ---

    def _contar_metricas_por_perfil(self, perfil):
        """
        Replica a lógica de calcular_metricas_resultados, mas retornando CONTAGENS,
        para podermos agregar entre vários perfis (resultado mensal).
        ► NOVO: respeita a vigência por loja no perfil (self._dia_dentro_vigencia).
        Retorna (tot_finalizados, c_sem_pend_dentro, c_sem_pend_fora,
                c_com_pend_dentro, c_com_pend_fora, total_dias_validos).
        """
        from datetime import datetime
        import calendar
        if "planilha" not in perfil:
            return (0, 0, 0, 0, 0, 0)

        # Contexto do mês (formato "Mês/Ano", ex.: "Junho/2025")
        try:
            mes_nome, ano_str = perfil["mes"].split("/")
            ano = int(ano_str)
            mes_num = MESES_PTBR.index(mes_nome) + 1
        except Exception:
            return (0, 0, 0, 0, 0, 0)

        dias_mes = calendar.monthrange(ano, mes_num)[1]
        PRAZO = getattr(self, "PRAZO_FINALIZACAO_DIAS", 2)

        tot_finalizados = 0
        c_sem_pend_dentro = c_sem_pend_fora = 0
        c_com_pend_dentro = c_com_pend_fora = 0
        total_dias_validos = 0  # << NOVO: denominador agregado

        def ts_parse(x):
            try:
                return datetime.strptime(x["data_hora"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return datetime.now()

        for loja in perfil.get("planilha", []):
            for d in range(1, dias_mes + 1):
                dia = str(d)

                # ► NOVO: respeitar vigência
                if not self._dia_dentro_vigencia(loja, d, dias_mes):
                    continue

                status_final = loja.get("dias", {}).get(dia, "")

                # Domingos não contam, a menos que estejam Finalizados
                if calendar.weekday(ano, mes_num, d) == 6 and status_final != "Finalizado":
                    continue
                if status_final == "Domingo":
                    continue

                # ► NOVO: este dia conta no denominador (válido)
                total_dias_validos += 1

                meta_dia = loja.get("meta", {}).get(dia, {})
                logs = meta_dia.get("logs", [])

                # Só logs manuais (GoodCard +7 automático não entra)
                logs_manuais = [l for l in logs if l.get("origem") == "manual"]
                logs_manuais.sort(key=ts_parse)

                primeira_ts, primeira_status = None, ""
                if logs_manuais:
                    primeira = logs_manuais[0]
                    primeira_ts = ts_parse(primeira)
                    primeira_status = primeira.get("status", "")

                # Limite do prazo (D + PRAZO, ignorando domingos) até 23:59:59
                base = datetime(ano, mes_num, int(dia))
                limite = self.somar_dias_ignorando_domingo(base, PRAZO).replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )

                finalizado_ts = None
                if status_final == "Finalizado":
                    ts_text = meta_dia.get("finalizado_ts")
                    if ts_text:
                        try:
                            finalizado_ts = datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            finalizado_ts = None

                # Não finalizou → não entra no denominador de “finalizados”
                if finalizado_ts is None:
                    continue

                # Houve alguma pendência (manual) antes do limite?
                houve_pendencia_previa = False
                for l in logs_manuais:
                    st = l.get("status", "")
                    if st not in ("Finalizado", "Domingo", "") and ts_parse(l) <= limite:
                        houve_pendencia_previa = True
                        break

                tot_finalizados += 1

                if not houve_pendencia_previa and primeira_status == "Finalizado":
                    # Sem pendência
                    if primeira_ts and primeira_ts <= limite:
                        c_sem_pend_dentro += 1
                    else:
                        c_sem_pend_fora += 1
                else:
                    # Com pendência
                    if finalizado_ts <= limite:
                        c_com_pend_dentro += 1
                    else:
                        c_com_pend_fora += 1

        return (tot_finalizados, c_sem_pend_dentro, c_sem_pend_fora,
                c_com_pend_dentro, c_com_pend_fora, total_dias_validos)

    def calcular_metricas_resultados_aggregadas(self, perfis_docs):
        """
        Soma as contagens em vários perfis e retorna percentuais agregados.
        ► NOVO: agrega total_dias_validos (denominador) e calcula pct_finalizados_geral.
        """
        tot = spd = spf = cpd = cpf = 0
        total_dias_validos = 0  # << NOVO

        for p in (perfis_docs or []):
            t, a, b, c, d, denom = self._contar_metricas_por_perfil(p)  # << NOVO
            tot += t
            spd += a
            spf += b
            cpd += c
            cpf += d
            total_dias_validos += denom  # << NOVO

        def pct(v):
            return 0 if tot == 0 else round((v / tot) * 100)

        return {
            "sem_pend_dentro": pct(spd),
            "sem_pend_fora": pct(spf),
            "com_pend_dentro": pct(cpd),
            "com_pend_fora": pct(cpf),
            "base_finalizados": tot,
            "total_dias_validos": total_dias_validos,                            # << NOVO
            "pct_finalizados_geral": (0 if total_dias_validos == 0              # << NOVO
                                    else round((tot/total_dias_validos)*100))
        }


    def _coletar_avaliacoes_mes(self, mes_full: str):
        """
        Busca no Supabase todos os perfis do mês/ano informado (mes_full = 'Mês/Ano')
        e extrai as avaliações em um formato flat por ENTRADA de avaliação.

        Retorno: lista de dicts:
        { 'loja': str, 'regional': str, 'media_entrada': float, 'qtd_criterios': int }
        """
        from tkinter import messagebox
        try:
            # Consulta TODOS os perfis do mês/ano (sem filtrar por role)
            res = supabase.table("perfis").select("doc,mes,usuario_dono").eq("mes", mes_full).execute()
            rows = res.data or []
        except Exception as e:
            messagebox.showerror("Avaliação de Lojas", f"Falha ao consultar perfis do banco:\n{e}")
            return []

        # Mapa loja -> regional a partir do cadastro de lojas (self.lojas)
        def _reg(loja_nome: str) -> str:
            try:
                for l in (self.lojas or []):
                    if l.get("loja") == loja_nome:
                        return normalizar_regional(l.get("regional"))
            except Exception:
                pass
            return "OUTROS"

        registros = []
        for row in rows:
            doc = row.get("doc") or {}
            avals = doc.get("avaliacoes") or []
            for a in avals:
                loja = a.get("loja")
                notas = a.get("notas") or {}
                vals = [v for v in notas.values() if isinstance(v, (int, float))]
                if not (loja and vals):
                    continue
                media = sum(vals) / len(vals)
                registros.append({
                    "loja": loja,
                    "regional": _reg(loja),
                    "media_entrada": float(media),
                    "qtd_criterios": len(vals),
                })
        return registros


    def _agregar_avaliacoes(self, registros: list):
        """
        A partir de 'registros' flat (cada ENTRADA de avaliação), consolida:
        - df_lojas: média por loja (se uma loja aparecer em mais de um perfil, faz média das entradas)
        - df_reg: média por regional (média das lojas daquela regional)

        Retorna (df_lojas, df_reg, df_top5, df_bottom5)
        """
        import pandas as pd

        if not registros:
            # DataFrames vazios com colunas previstas
            cols_loja = ["Loja", "Regional", "Media_Loja", "Entradas_Avaliacao"]
            cols_reg = ["Regional", "Media_Regional", "Qtd_Lojas"]
            empty_lojas = pd.DataFrame(columns=cols_loja)
            empty_reg = pd.DataFrame(columns=cols_reg)
            return empty_lojas, empty_reg, empty_lojas.head(0), empty_lojas.head(0)

        df = pd.DataFrame(registros)
        # 1) média por loja (média das entradas da loja no mês/ano)
        g_loja = df.groupby(["loja", "regional"], as_index=False).agg(
            Media_Loja=("media_entrada", "mean"),
            Entradas_Avaliacao=("media_entrada", "count"),
        )
        g_loja = g_loja.rename(columns={"loja": "Loja", "regional": "Regional"})
        g_loja["Media_Loja"] = g_loja["Media_Loja"].round(2)

        # 2) média por regional (média das médias por loja)
        g_reg = g_loja.groupby("Regional", as_index=False).agg(
            Media_Regional=("Media_Loja", "mean"),
            Qtd_Lojas=("Loja", "count"),
        )
        g_reg["Media_Regional"] = g_reg["Media_Regional"].round(2)

        # 3) rankings
        df_top5 = g_loja.sort_values(["Media_Loja", "Loja"], ascending=[False, True]).head(5)
        df_bottom5 = g_loja.sort_values(["Media_Loja", "Loja"], ascending=[True, True]).head(5)

        return g_loja, g_reg, df_top5, df_bottom5

    def abrir_mapa_das_lojas(self):
        import tkinter as tk
        from tkinter import ttk, messagebox
        from datetime import datetime

        win = tk.Toplevel(self.master)
        win.title("Mapa das Lojas")
        win.configure(bg=BG_DARK)
        win.state("zoomed")

        # Título
        tk.Label(
            win, text="🗺️ Mapa das Lojas",
            font=("Segoe UI", 14, "bold"), bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=(10, 6))

        # ===== Barra de filtros (Mês/Ano) =====
        top = tk.Frame(win, bg=BG_DARK)
        top.pack(fill="x", padx=20, pady=(0, 10))

        ano_atual = datetime.now().year
        anos = [str(a) for a in range(ano_atual - 2, ano_atual + 3)]
        mes_atual = MESES_PTBR[datetime.now().month - 1]  # constante existente
        mes_var = tk.StringVar(value=mes_atual)
        ano_var = tk.StringVar(value=str(ano_atual))

        tk.Label(top, text="Mês:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).pack(side="left")
        mes_menu = tk.OptionMenu(top, mes_var, *MESES_PTBR)
        mes_menu.configure(
            bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
            activeforeground=FG_TEXT, relief="flat", bd=0, highlightthickness=0
        )
        mes_menu.pack(side="left", padx=(6, 16))

        tk.Label(top, text="Ano:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).pack(side="left")
        ano_menu = tk.OptionMenu(top, ano_var, *anos)
        ano_menu.configure(
            bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
            activeforeground=FG_TEXT, relief="flat", bd=0, highlightthickness=0
        )
        ano_menu.pack(side="left", padx=(6, 16))

        info_lbl = tk.Label(top, text="", bg=BG_DARK, fg="#b0b0b0", font=FONT_UI)
        info_lbl.pack(side="left", padx=10)
    
        # ===== Área rolável com seções por regional =====

        # ===== Área fixa (sem scroll) com grade 4 colunas =====
        area = tk.Frame(win, bg=BG_DARK)
        area.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        grid = tk.Frame(area, bg=BG_DARK)
        grid.pack(fill="both", expand=True)

        # 4 colunas uniformes
        for c in range(4):
            grid.columnconfigure(c, weight=1, uniform="col")

        # Criamos frames de coluna para organizar verticalmente os cards
        col_frames = []
        for c in range(4):
            colf = tk.Frame(grid, bg=BG_DARK)
            colf.grid(row=0, column=c, sticky="nsew", padx=8)
            col_frames.append(colf)

        # ===== Utilitários de dados =====
        def _mapa_loja_para_regional():
            """Cria um dict: nome_loja -> regional normalizada (ou 'OUTROS')."""
            mapa = {}
            try:
                for l in (self.lojas or []):
                    nome = l.get("loja")
                    reg = normalizar_regional(l.get("regional"))
                    if nome:
                        mapa[nome] = reg
            except Exception:
                pass
            return mapa

        def _coletar_mapa_lojas(mes_full: str):
            """
            Lê perfis do mês no Supabase e consolida:
                regional -> { loja -> set(usuarios_dono) }
            """
            grupos = {}
            try:
                # consulta perfis pelo mês: seleciona doc (com lojas), mes e usuario_dono
                res = supabase.table("perfis").select("doc,mes,usuario_dono").eq("mes", mes_full).execute()
                rows = res.data or []
            except Exception as e:
                messagebox.showerror("Mapa das Lojas", f"Falha ao consultar perfis do banco:\n{e}")
                return {}

            # index auxiliar: loja -> regional
            idx_reg = _mapa_loja_para_regional()

            # acumula loja -> {usuarios}
            loja2users = {}
            for row in rows:
                doc = row.get("doc") or {}
                dono = row.get("usuario_dono") or (doc.get("usuario_dono") or doc.get("nome") or "")
                for lj in (doc.get("lojas") or []):
                    nome_loja = lj.get("loja")
                    if not nome_loja:
                        continue
                    s = loja2users.setdefault(nome_loja, set())
                    if dono:
                        s.add(dono)

            # agora, quebra por regional
            for loja, users in loja2users.items():
                reg = idx_reg.get(loja, "OUTROS")
                grupos.setdefault(reg, {})[loja] = sorted(users, key=str.lower)

            return grupos

        # ===== Render =====
        def render():
            # limpa colunas
            for colf in col_frames:
                for w in colf.winfo_children():
                    w.destroy()

            mes_full = f"{mes_var.get()}/{ano_var.get()}"
            grupos = _coletar_mapa_lojas(mes_full)  # { regional: {loja: [usuarios...] } }

            total_regs = len(grupos.keys())
            total_lojas = sum(len(v) for v in grupos.values()) if grupos else 0
            info_lbl.configure(text=f"Mês/Ano: {mes_full} • Regiões: {total_regs} • Lojas mapeadas: {total_lojas}")

            # Paleta e helpers de UI
            CARD_BG      = "#1f1f1f"
            CARD_HEAD_BG = "#1a1a1a"
            CARD_BORDER  = "#333333"
            TXT_TITLE    = "#00bfff"
            TXT_BODY     = "#dddddd"
            TXT_MUTED    = "#b0b0b0"

            def _ordenar_loja(lj):
                try:
                    return (0, int(lj))
                except:
                    return (1, str(lj).lower())

            def _montar_card(parent, reg, data_dict):
                """
                parent: frame de coluna
                reg: sigla (AM, PA, ...)
                data_dict: { loja: [usuarios...] }
                """
                card = tk.Frame(parent, bg=CARD_BG, bd=0, highlightthickness=1, highlightbackground=CARD_BORDER)
                card.pack(fill="x", pady=10)

                head = tk.Frame(card, bg=CARD_HEAD_BG)
                head.pack(fill="x")
                tk.Label(
                    head, text=f" {reg}",
                    font=("Segoe UI", 11, "bold"), bg=CARD_HEAD_BG, fg=TXT_TITLE
                ).pack(anchor="w", padx=8, pady=6)

                body = tk.Frame(card, bg=CARD_BG)
                body.pack(fill="x", padx=10, pady=(6, 10))

                # Cabeçalhos
                tk.Label(body, text="Loja", bg=CARD_BG, fg=TXT_MUTED,
                        font=("Segoe UI", 9, "bold"), anchor="w", width=10).grid(row=0, column=0, sticky="w")
                tk.Label(body, text="Auditor", bg=CARD_BG, fg=TXT_MUTED,
                        font=("Segoe UI", 9, "bold"), anchor="w").grid(row=0, column=1, sticky="w", padx=(10, 0))

                # Linhas
                lojas = sorted(list(data_dict.keys()), key=_ordenar_loja)
                for i, loja in enumerate(lojas, start=1):
                    users = ", ".join(data_dict[loja]) if data_dict[loja] else "--"
                    tk.Label(body, text=str(loja), bg=CARD_BG, fg=TXT_BODY,
                            font=("Segoe UI", 9), anchor="w", width=10).grid(row=i, column=0, sticky="w", pady=1)
                    tk.Label(body, text=users, bg=CARD_BG, fg="#bbbbbb",
                            font=("Segoe UI", 9), anchor="w").grid(row=i, column=1, sticky="w", pady=1)

            # --- Distribuição de regionais nas 4 colunas ---
            # 1) Coluna 0 = AM (se houver)
            # 2) Coluna 1 = PA (se houver)
            # 3) Coluna 2 = duas regionais (ex.: AP e RR)
            # 4) Coluna 3 = duas regionais (ex.: MA e MT)

            major = ["AM", "PA"]
            minor_pref = ["AP", "RR", "MA", "MT"]

            # Desenha AM e PA (colunas 0 e 1)
            for idx, reg in enumerate(major):
                if reg in grupos:
                    _montar_card(col_frames[idx], reg, grupos[reg])
                else:
                    # Se quiser mostrar “sem dados”, descomente:
                    # _montar_card(col_frames[idx], reg, {})
                    pass

            # Monta a lista das minor realmente disponíveis
            minors = [r for r in minor_pref if r in grupos]

            # Divide as minor em duas pilhas (coluna 2 e coluna 3), no máximo 2 por coluna
            left_stack  = minors[:2]   # coluna 2
            right_stack = minors[2:4]  # coluna 3

            for reg in left_stack:
                _montar_card(col_frames[2], reg, grupos[reg])
            for reg in right_stack:
                _montar_card(col_frames[3], reg, grupos[reg])




        # primeira renderização
        render()
    
        footer = tk.Frame(win, bg=BG_DARK)
        footer.pack(fill="x", padx=14, pady=(8, 14))  # leve espaço após o grid


        btn_viz = tk.Button(
            footer,
            text="Visualização (Minhas Lojas do mês)",
            command=abrir_visualizacao_minhas,   # função definida dentro do mesmo método
            font=FONT_UI,
            bg="#007acc", fg=FG_TEXT,
            activebackground="#3399ff", activeforeground="#ffffff",
            relief="flat", bd=0, padx=12, pady=10
        )
        btn_viz.pack(fill="x")


    def abrir_avaliacao_lojas(self):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        from datetime import datetime
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        import pandas as pd

        win = tk.Toplevel(self.master)
        win.title("Avaliação de Lojas")
        win.configure(bg=BG_DARK)
        win.state("zoomed")

        # --- Título
        tk.Label(
            win, text="📝 Avaliação de Lojas",
            font=("Segoe UI", 14, "bold"), bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=(10, 6))

        # --- Barra de filtros Mês/Ano (como no Resultado Mensal)
        top = tk.Frame(win, bg=BG_DARK); top.pack(fill="x", padx=20, pady=(0, 10))
        ano_atual = datetime.now().year
        anos = [str(a) for a in range(ano_atual - 2, ano_atual + 3)]
        mes_atual = MESES_PTBR[datetime.now().month - 1]

        tk.Label(top, text="Mês:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).pack(side="left")
        mes_var = tk.StringVar(value=mes_atual)
        mes_menu = tk.OptionMenu(top, mes_var, *MESES_PTBR)
        mes_menu.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                        activeforeground=FG_TEXT, relief="flat", bd=0, highlightthickness=0)
        mes_menu.pack(side="left", padx=(6, 16))

        tk.Label(top, text="Ano:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).pack(side="left")
        ano_var = tk.StringVar(value=str(ano_atual))
        ano_menu = tk.OptionMenu(top, ano_var, *anos)
        ano_menu.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                        activeforeground=FG_TEXT, relief="flat", bd=0, highlightthickness=0)
        ano_menu.pack(side="left", padx=(6, 16))

        info_lbl = tk.Label(top, text="", bg=BG_DARK, fg="#b0b0b0", font=FONT_UI)
        info_lbl.pack(side="left", padx=10)

        # --- Seção: Gráfico Média por Região
        sec_graf = tk.Frame(win, bg=BG_DARK); sec_graf.pack(fill="x", padx=16, pady=(4, 8))
        tk.Label(sec_graf, text="Média por Região (Geral)", font=("Segoe UI", 12, "bold"),
                bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", pady=(0, 6))

        fig = Figure(figsize=(7.8, 3.6), dpi=100)
        ax = fig.add_subplot(111)
        canvas = FigureCanvasTkAgg(fig, master=sec_graf)
        canvas_widget = canvas.get_tk_widget()
        canvas_widget.pack(fill="x", expand=False)

        # --- Tabela Média por Região
        sec_tab_reg = tk.Frame(win, bg=BG_DARK); sec_tab_reg.pack(fill="x", padx=16, pady=(6, 12))
        tk.Label(sec_tab_reg, text="Tabela — Média por Região", font=("Segoe UI", 11, "bold"),
                bg=BG_DARK, fg=FG_TEXT).pack(anchor="w")

        cols_reg = ("Regional", "Média", "Qtd Lojas")
        tree_reg = ttk.Treeview(sec_tab_reg, columns=cols_reg, show="headings", height=6)
        for col in cols_reg:
            tree_reg.heading(col, text=col); tree_reg.column(col, width=140, anchor="center")
        tree_reg.pack(fill="x", pady=6)

        # --- Rankings: Top 5 / Bottom 5
        sec_rank = tk.Frame(win, bg=BG_DARK); sec_rank.pack(fill="x", padx=16, pady=(4, 8))

        bloco_top = tk.Frame(sec_rank, bg=BG_DARK)
        bloco_top.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(bloco_top, text="🏆 Top 5 Lojas", font=("Segoe UI", 11, "bold"),
                bg=BG_DARK, fg=FG_TEXT).pack(anchor="w")

        cols_loja = ("Loja", "Regional", "Média")
        tree_top = ttk.Treeview(bloco_top, columns=cols_loja, show="headings", height=7)
        for col in cols_loja:
            tree_top.heading(col, text=col); tree_top.column(col, width=180 if col == "Loja" else 120, anchor="center")
        tree_top.pack(fill="both", expand=True, pady=6)

        bloco_bottom = tk.Frame(sec_rank, bg=BG_DARK)
        bloco_bottom.pack(side="left", fill="both", expand=True, padx=(8, 0))
        tk.Label(bloco_bottom, text="📉 Bottom 5 Lojas", font=("Segoe UI", 11, "bold"),
                bg=BG_DARK, fg=FG_TEXT).pack(anchor="w")

        tree_bottom = ttk.Treeview(bloco_bottom, columns=cols_loja, show="headings", height=7)
        for col in cols_loja:
            tree_bottom.heading(col, text=col); tree_bottom.column(col, width=180 if col == "Loja" else 120, anchor="center")
        tree_bottom.pack(fill="both", expand=True, pady=6)

        # --- Rodapé com Export e Fechar
        rodape = tk.Frame(win, bg=BG_DARK); rodape.pack(fill="x", padx=16, pady=(8, 16))

        def exportar_excel(df_lojas, df_reg, df_top5, df_bottom5, mes_full):
            path = filedialog.asksaveasfilename(
                title="Salvar resultado de avaliações",
                defaultextension=".xlsx",
                filetypes=[("Excel", "*.xlsx")],
                initialfile=f"avaliacao_lojas_{mes_full.replace('/','-')}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            )
            if not path:
                return
            try:
                df_por_reg = df_lojas.sort_values(["Regional", "Media_Loja"], ascending=[True, False])
                with pd.ExcelWriter(path, engine="openpyxl") as writer:
                    df_reg.sort_values("Media_Regional", ascending=False).to_excel(writer, sheet_name="Resumo_Regional", index=False)
                    df_lojas.sort_values("Media_Loja", ascending=False).to_excel(writer, sheet_name="Lojas_Detalhe", index=False)
                    df_top5.to_excel(writer, sheet_name="Top5", index=False)
                    df_bottom5.to_excel(writer, sheet_name="Bottom5", index=False)
                    df_por_reg.to_excel(writer, sheet_name="Lojas_por_Regional", index=False)
                messagebox.showinfo("Exportar", f"Arquivo salvo com sucesso em:\n{path}")
            except Exception as e:
                messagebox.showerror("Exportar", f"Falha ao exportar Excel:\n{e}")

        btn_export = tk.Button(
            rodape, text="Exportar Excel (todas as lojas)",
            font=FONT_UI, bg="#31a252", fg=FG_TEXT, relief="flat", padx=12, pady=8
        )
        btn_export.pack(side="left")

        tk.Button(
            rodape, text="Fechar", command=win.destroy,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT, relief="flat", padx=12, pady=8
        ).pack(side="right")

        # --- Estado local e função de render
        state = {"df_lojas": None, "df_reg": None, "df_top5": None, "df_bottom5": None}

        def render():
            mes_full = f"{mes_var.get()}/{ano_var.get()}"

            # coleta + agrega
            registros = self._coletar_avaliacoes_mes(mes_full)
            df_lojas, df_reg, df_top5, df_bottom5 = self._agregar_avaliacoes(registros)
            state.update(df_lojas=df_lojas, df_reg=df_reg, df_top5=df_top5, df_bottom5=df_bottom5)

            # info
            qtd_lojas = 0 if df_lojas is None or df_lojas.empty else len(df_lojas.index)
            qtd_reg = 0 if df_reg is None or df_reg.empty else len(df_reg.index)
            info_lbl.configure(text=f"Mês/Ano: {mes_full} • Regiões: {qtd_reg} • Lojas avaliadas: {qtd_lojas}")

            # gráfico
            ax.clear()
            if df_reg is None or df_reg.empty:
                ax.text(0.5, 0.5, "Sem dados", ha="center", va="center")
                ax.set_axis_off()
            else:
                df_plot = df_reg.sort_values("Media_Regional", ascending=False)
                ax.bar(df_plot["Regional"], df_plot["Media_Regional"], color="#2b8a3e")
                ax.set_xlabel("Regional"); ax.set_ylabel("Média"); ax.set_ylim(0, 5)
                ax.set_title("Média de Avaliações por Regional")
                for i, v in enumerate(df_plot["Media_Regional"]):
                    ax.text(i, v + 0.05, f"{v:.2f}", ha="center", va="bottom", fontsize=8)
                ax.grid(axis="y", linestyle=":", alpha=0.4)
                ax.set_axisbelow(True)
            canvas.draw()

            # tabela regional
            for i in tree_reg.get_children():
                tree_reg.delete(i)
            if df_reg is not None and not df_reg.empty:
                for _, row in df_reg.sort_values("Media_Regional", ascending=False).iterrows():
                    tree_reg.insert("", "end", values=(row["Regional"], f"{row['Media_Regional']:.2f}", int(row["Qtd_Lojas"])))

            # rankings
            for i in tree_top.get_children(): tree_top.delete(i)
            for i in tree_bottom.get_children(): tree_bottom.delete(i)

            if df_top5 is not None and not df_top5.empty:
                for _, row in df_top5.iterrows():
                    tree_top.insert("", "end", values=(row["Loja"], row["Regional"], f"{row['Media_Loja']:.2f}"))
            if df_bottom5 is not None and not df_bottom5.empty:
                for _, row in df_bottom5.iterrows():
                    tree_bottom.insert("", "end", values=(row["Loja"], row["Regional"], f"{row['Media_Loja']:.2f}"))

            # (re)ligar botão export com dados atuais
            def _do_export():
                exportar_excel(state["df_lojas"], state["df_reg"], state["df_top5"], state["df_bottom5"], mes_full)
            btn_export.configure(command=_do_export)

            # alerta leve se não houver dados
            if (df_lojas is None or df_lojas.empty) and (df_reg is None or df_reg.empty):
                messagebox.showinfo("Avaliação de Lojas", "Não há avaliações registradas para este mês/ano.")

        # Atualização automática ao trocar Mês/Ano (correção padrão: trace_add)
        def _on_change(*_): render()
        mes_var.trace_add("write", _on_change)
        ano_var.trace_add("write", _on_change)

        # primeira renderização
        render()


    def abrir_resultado_mensal(self):
        """
        Abre janela com duas abas:
        1) Resultado Mensal (cards) — visão geral + seleção por usuário.
        2) Análise Gráfica (visão anual) — barras empilhadas 100% (12 meses).
        Observa regras de acesso iguais às já usadas no app.
        """
        import tkinter as tk
        from tkinter import ttk, messagebox
        from datetime import datetime
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        import numpy as np

        # -------- janela (Toplevel) --------
        win = tk.Toplevel(self.master)
        win.title("Resultado Mensal")
        win.configure(bg=BG_DARK)
        win.state("zoomed")

        # Título geral da janela
        tk.Label(
            win, text="📊 Resultado Mensal",
            font=("Segoe UI", 14, "bold"), bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=(10, 6))

        # -------- Notebook (abas) --------
        # Estilo dark para abas
        style = ttk.Style()
        try:
            style.theme_use("default")
        except Exception:
            pass
        style.configure("TNotebook", background=BG_DARK, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_Panel, foreground=FG_TEXT) if False else style.configure("TNotebook.Tab", background=BG_PANEL, foreground=FG_TEXT)
        style.map("TNotebook.Tab",
                background=[("selected", "#444444")],
                foreground=[("selected", FG_TEXT)])
        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        # === ABA 1: RESULTADO MENSAL ==================================================
        tab_rm = tk.Frame(notebook, bg=BG_DARK)
        notebook.add(tab_rm, text="Análise Geral")

        # Barra de filtros (Mês/Ano) da aba Resultado Mensal
        top_rm = tk.Frame(tab_rm, bg=BG_DARK)
        top_rm.pack(fill="x", padx=20, pady=(8, 10))

        ano_atual = datetime.now().year
        anos = [str(a) for a in range(ano_atual - 2, ano_atual + 3)]
        mes_atual_nome = MESES_PTBR[datetime.now().month - 1]

        tk.Label(top_rm, text="Mês:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).pack(side="left")
        mes_var = tk.StringVar(value=mes_atual_nome)
        mes_menu = tk.OptionMenu(top_rm, mes_var, *MESES_PTBR)
        mes_menu.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                        activeforeground=FG_TEXT, relief="flat")
        mes_menu.pack(side="left", padx=(6, 16))

        tk.Label(top_rm, text="Ano:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).pack(side="left")
        ano_var = tk.StringVar(value=str(ano_atual))
        ano_menu = tk.OptionMenu(top_rm, ano_var, *anos)
        ano_menu.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                        activeforeground=FG_TEXT, relief="flat")
        ano_menu.pack(side="left", padx=(6, 16))

        info_lbl_geral = tk.Label(top_rm, text="", bg=BG_DARK, fg="#b0b0b0", font=FONT_UI)
        info_lbl_geral.pack(side="left", padx=10)

        # --- CONTEXTO DE SESSÃO ---
        username_atual = (self.current_user or {}).get("username", "") or ""
        role_atual = (self.current_user or {}).get("role", "") or ""

        # Título da seção GERAL
        tk.Label(
            tab_rm, text="Geral (todos os perfis do mês/ano)",
            font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT
        ).pack(anchor="w", padx=20, pady=(4, 0))

        # Grid de cards (GERAL)
        grid_geral = tk.Frame(tab_rm, bg=BG_DARK)
        grid_geral.pack(anchor="n")
        def make_card(parent, titulo, cor_borda="#444444", valor_placeholder="--", dica=""):
            card = tk.Frame(parent, bg=BG_DARK, highlightbackground=cor_borda, highlightthickness=1, bd=0)
            card.configure(width=320, height=120)
            card.pack_propagate(False)
            tk.Label(card, text=titulo, font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=FG_TEXT)\
                .pack(anchor="w", padx=12, pady=(10, 0))
            lbl_val = tk.Label(card, text=valor_placeholder, font=("Segoe UI", 18, "bold"), bg=BG_DARK, fg=FG_TEXT)
            lbl_val.pack(anchor="w", padx=12, pady=(2, 2))
            if dica:
                tk.Label(card, text=dica, font=("Segoe UI", 9), bg=BG_DARK, fg="#b0b0b0", wraplength=280, justify="left")\
                    .pack(anchor="w", padx=12, pady=(0, 10))
            return card, lbl_val

        for c in range(2):
            grid_geral.grid_columnconfigure(c, weight=1, pad=12)
        for r in range(2):
            grid_geral.grid_rowconfigure(r, weight=1, pad=12)

        g1, g1_lbl = make_card(grid_geral, "Sem pendência (dentro do prazo)", cor_borda="#2b5c7a",
                            dica="Primeira ação = Finalizado, dentro do limite.")
        g1.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        g2, g2_lbl = make_card(grid_geral, "Sem pendência (fora do prazo)", cor_borda="#7a2b2b",
                            dica="Primeira ação = Finalizado, porém após o limite.")
        g2.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        g3, g3_lbl = make_card(grid_geral, "Com pendência (dentro do prazo)", cor_borda="#2f6f3e",
                            dica="Houve pendência e a finalização ocorreu no prazo.")
        g3.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        g4, g4_lbl = make_card(grid_geral, "Com pendência (fora do prazo)", cor_borda="#7a5c2b",
                            dica="Houve pendência e a finalização ocorreu após o limite.")
        g4.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)

        # --- Seção POR USUÁRIO ---
        area_user = tk.Frame(tab_rm, bg=BG_DARK)
        area_user.pack(fill="x", padx=20, pady=(8, 0))

        tk.Label(
            area_user, text="Por usuário (selecione para ver os resultados dele)",
            font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        user_line = tk.Frame(area_user, bg=BG_DARK); user_line.grid(row=1, column=0, sticky="w")
        tk.Label(user_line, text="Usuário:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).pack(side="left", padx=(0, 6))
        user_var = tk.StringVar(value="(selecione)")
        user_combo = ttk.Combobox(user_line, textvariable=user_var, state="readonly", width=28)
        user_combo.pack(side="left")
        info_lbl_user = tk.Label(user_line, text="", bg=BG_DARK, fg="#b0b0b0", font=FONT_UI)
        info_lbl_user.pack(side="left", padx=12)

        # Grid de cards (POR USUÁRIO)
        grid_user = tk.Frame(tab_rm, bg=BG_DARK)
        grid_user.pack(anchor="n", pady=(6, 0))
        for c in range(2):
            grid_user.grid_columnconfigure(c, weight=1, pad=12)
        for r in range(2):
            grid_user.grid_rowconfigure(r, weight=1, pad=12)

        u1, u1_lbl = make_card(grid_user, "Sem pendência (dentro do prazo)", cor_borda="#2b5c7a")
        u1.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        u2, u2_lbl = make_card(grid_user, "Sem pendência (fora do prazo)", cor_borda="#7a2b2b")
        u2.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        u3, u3_lbl = make_card(grid_user, "Com pendência (dentro do prazo)", cor_borda="#2f6f3e")
        u3.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        u4, u4_lbl = make_card(grid_user, "Com pendência (fora do prazo)", cor_borda="#7a5c2b")
        u4.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)

        # Rodapé Aba 1
        rodape_rm = tk.Frame(tab_rm, bg=BG_DARK); rodape_rm.pack(side="bottom", fill="x", pady=16)
        tk.Button(
            rodape_rm, text="Fechar", command=win.destroy,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT, relief="flat", padx=12, pady=8
        ).pack(side="right", padx=8)

        def _fmt(p):
            try:
                return f"{int(round(p))}%"
            except Exception:
                return "0%"

        # Estado local dessa aba
        state_rm = {"rows": [], "usuarios": []}

        # Helpers de atualização (Aba Resultado Mensal)
        def atualizar_cards_geral(perfis_docs, mes_full):
            metr = self.calcular_metricas_resultados_aggregadas(perfis_docs)
            g1_lbl.configure(text=_fmt(metr.get("sem_pend_dentro", 0)))
            g2_lbl.configure(text=_fmt(metr.get("sem_pend_fora", 0)))
            g3_lbl.configure(text=_fmt(metr.get("com_pend_dentro", 0)))
            g4_lbl.configure(text=_fmt(metr.get("com_pend_fora", 0)))

            base = int(metr.get("base_finalizados", 0))
            denom = int(metr.get("total_dias_validos", 0))
            pctf = int(metr.get("pct_finalizados_geral", 0))
            info_lbl_geral.configure(
                text=f"Mês/Ano: {mes_full} • Perfis agregados: {len(perfis_docs)} • "
                    f"Dias finalizados (base): {base} • "
                    f"% finalizados: {pctf}% ( {base}/{denom} )"
            )

        def atualizar_cards_usuario(username_selecionado):
            # restrição: não-admin só pode ver o próprio
            if role_atual != "admin" and username_selecionado not in ("", "(selecione)"):
                if username_selecionado != username_atual:
                    for lbl in (u1_lbl, u2_lbl, u3_lbl, u4_lbl):
                        lbl.configure(text="--")
                    info_lbl_user.configure(text="Acesso negado: você só pode ver seus próprios resultados.")
                    return

            if not username_selecionado or username_selecionado == "(selecione)":
                for lbl in (u1_lbl, u2_lbl, u3_lbl, u4_lbl):
                    lbl.configure(text="--")
                info_lbl_user.configure(text="Selecione um usuário para ver os resultados.")
                return

            perfis_user = [
                (row.get("doc") or {})
                for row in state_rm["rows"]
                if (row.get("usuario_dono") or "") == username_selecionado
            ]
            metr = self.calcular_metricas_resultados_aggregadas(perfis_user)
            u1_lbl.configure(text=_fmt(metr.get("sem_pend_dentro", 0)))
            u2_lbl.configure(text=_fmt(metr.get("sem_pend_fora", 0)))
            u3_lbl.configure(text=_fmt(metr.get("com_pend_dentro", 0)))
            u4_lbl.configure(text=_fmt(metr.get("com_pend_fora", 0)))

            base = int(metr.get("base_finalizados", 0))
            denom = int(metr.get("total_dias_validos", 0))
            pctf = int(metr.get("pct_finalizados_geral", 0))
            info_lbl_user.configure(
                text=f"Usuário: {username_selecionado} • Perfis agregados: {len(perfis_user)} • "
                    f"Dias finalizados (base): {base} • "
                    f"% finalizados: {pctf}% ( {base}/{denom} )"
            )

        def recarregar_rm(*_):
            mes_full = f"{mes_var.get()}/{ano_var.get()}"
            try:
                q = supabase.table("perfis").select("doc,mes,usuario_dono").eq("mes", mes_full)
                res = q.execute()
                rows = res.data or []
            except Exception as e:
                messagebox.showerror("Resultado Mensal", f"Falha ao consultar perfis do banco:\n{e}")
                rows = []

            state_rm["rows"] = rows
            perfis_docs_geral = [r.get("doc") for r in rows if isinstance(r.get("doc"), dict)]
            atualizar_cards_geral(perfis_docs_geral, mes_full)

            todos_usuarios = sorted({(r.get("usuario_dono") or "") for r in rows if r.get("usuario_dono")})
            if role_atual == "admin":
                usuarios = todos_usuarios
                user_combo["values"] = ["(selecione)"] + usuarios
                info_lbl_user.configure(text="(Selecione um usuário para ver os resultados individuais)")
                user_var.set("(selecione)")
                user_combo.configure(state="readonly")
            else:
                usuarios = [u for u in todos_usuarios if u == username_atual]
                user_combo["values"] = usuarios if usuarios else ["(selecione)"]
                if usuarios:
                    user_var.set(username_atual)
                    user_combo.configure(state="disabled")
                    info_lbl_user.configure(text="(Somente seus próprios resultados)")
                    atualizar_cards_usuario(username_atual)
                else:
                    user_var.set("(selecione)")
                    for lbl in (u1_lbl, u2_lbl, u3_lbl, u4_lbl):
                        lbl.configure(text="--")
                    info_lbl_user.configure(text="Nenhum perfil seu encontrado para este mês/ano.")

            if not rows:
                messagebox.showinfo("Resultado Mensal", "Nenhum perfil encontrado para o mês/ano selecionados.")

        def _on_mes_ano_change(*_):
            recarregar_rm()

        mes_var.trace_add("write", _on_mes_ano_change)
        ano_var.trace_add("write", _on_mes_ano_change)
        user_combo.bind("<<ComboboxSelected>>", lambda e: atualizar_cards_usuario(user_var.get()))

        # Primeira carga da Aba 1
        recarregar_rm()

        # === ABA 2: ANÁLISE GRÁFICA (VISÃO ANUAL) ====================================
        tab_ag = tk.Frame(notebook, bg=BG_DARK)
        notebook.add(tab_ag, text="Análise Gráfica")

        # Barra de controle (Ano) — independente da Aba 1
        top_ag = tk.Frame(tab_ag, bg=BG_DARK)
        top_ag.pack(fill="x", padx=20, pady=(8, 10))

        tk.Label(top_ag, text="Ano:", bg=BG_DARK, fg=FG_TEXT, font=FONT_UI).pack(side="left")
        ano_ag_var = tk.StringVar(value=str(datetime.now().year))
        ano_menu_ag = tk.OptionMenu(top_ag, ano_ag_var, *anos)
        ano_menu_ag.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                            activeforeground=FG_TEXT, relief="flat")
        ano_menu_ag.pack(side="left", padx=(6, 16))

        info_lbl_ag = tk.Label(top_ag, text="", bg=BG_DARK, fg="#b0b0b0", font=FONT_UI)
        info_lbl_ag.pack(side="left", padx=10)

        # Área de gráficos (dois blocos)
        area_ag = tk.Frame(tab_ag, bg=BG_DARK)
        area_ag.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        # --- Bloco GERAL (título + gráfico) ---
        bloco_g = tk.Frame(area_ag, bg=BG_DARK); bloco_g.pack(fill="both", expand=True, pady=(2, 10))
        tk.Label(bloco_g, text="Geral — Percentual mensal (100%)",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(anchor="w")
        fig_g = Figure(figsize=(12, 3.8), dpi=100)
        ax_g = fig_g.add_subplot(111)
        canvas_g = FigureCanvasTkAgg(fig_g, master=bloco_g)
        cw_g = canvas_g.get_tk_widget(); cw_g.configure(bg=BG_DARK, highlightthickness=0)
        cw_g.pack(fill="x", expand=False, pady=(2, 8))

        # --- Bloco INDIVIDUAL (título + gráfico) ---
        bloco_u = tk.Frame(area_ag, bg=BG_DARK); bloco_u.pack(fill="both", expand=True, pady=(6, 0))
        tk.Label(bloco_u, text="Individual — Percentual mensal (100%)",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(anchor="w")
        fig_u = Figure(figsize=(12, 3.8), dpi=100)
        ax_u = fig_u.add_subplot(111)
        canvas_u = FigureCanvasTkAgg(fig_u, master=bloco_u)
        cw_u = canvas_u.get_tk_widget(); cw_u.configure(bg=BG_DARK, highlightthickness=0)
        cw_u.pack(fill="x", expand=False, pady=(2, 8))

        # Rodapé da Aba 2 (apenas info/fechar na janela, botão fechar fica na Aba 1)
        footer_ag = tk.Frame(tab_ag, bg=BG_DARK); footer_ag.pack(fill="x", pady=(0, 6))

        # --- Helpers: consulta, cálculo e desenho para a ABA 2 ---
        def mes_full(m, a): return f"{m}/{a}"
        xlabels = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]

        def consultar_perfis_mes(mes_full_str: str):
            try:
                res = supabase.table("perfis").select("doc,mes,usuario_dono").eq("mes", mes_full_str).execute()
                return res.data or []
            except Exception as e:
                messagebox.showerror("Análise Gráfica", f"Falha ao consultar {mes_full_str}:\n{e}")
                return []

        def metricas_por_mes(mes_full_str: str):
            rows = consultar_perfis_mes(mes_full_str)
            docs_geral = [r.get("doc") for r in rows if isinstance(r.get("doc"), dict)]
            metr_geral = self.calcular_metricas_resultados_aggregadas(docs_geral)

            # regra de acesso igual à Aba 1 (se admin, usa user_var; senão, username_atual)
            if role_atual != "admin":
                user_target = username_atual
            else:
                user_target = user_var.get() if user_var.get() not in ("", "(selecione)") else None

            if user_target:
                docs_user = [(r.get("doc") or {}) for r in rows if (r.get("usuario_dono") or "") == user_target]
                metr_user = self.calcular_metricas_resultados_aggregadas(docs_user)
            else:
                metr_user = None
            return metr_geral, metr_user

        def desenhar(fig, ax, series, xlabels):
            # cores das categorias (iguais aos cards)
            C1 = "#2b5c7a"; C2 = "#7a2b2b"; C3 = "#2f6f3e"; C4 = "#7a5c2b"
            # fundo e margens
            fig.patch.set_facecolor(BG_DARK)
            ax.set_facecolor(BG_DARK)
            fig.subplots_adjust(top=0.86, bottom=0.28, left=0.06, right=0.985)
            # dados
            spd = series["sem_pend_dentro"]; spf = series["sem_pend_fora"]
            cpd = series["com_pend_dentro"]; cpf = series["com_pend_fora"]
            x = np.arange(len(xlabels))
            ax.clear()
            ax.bar(x, spd, color=C1, label="Sem pendência (dentro do prazo)")
            ax.bar(x, spf, bottom=spd, color=C2, label="Sem pendência (fora do prazo)")
            ax.bar(x, cpd, bottom=[a+b for a,b in zip(spd, spf)], color=C3, label="Com pendência (dentro do prazo)")
            ax.bar(x, cpf, bottom=[a+b+c for a,b,c in zip(spd, spf, cpd)], color=C4, label="Com pendência (fora do prazo)")

            # eixos e grade
            ax.set_xticks(x); ax.set_xticklabels(xlabels, color=FG_TEXT)
            ax.set_ylim(0, 100); ax.set_ylabel("%", color=FG_TEXT, fontsize=10)
            ax.grid(axis="y", linestyle=":", alpha=0.35); ax.set_axisbelow(True)
            for t in ax.get_yticklabels(): t.set_color(FG_TEXT)
            for spine in ax.spines.values(): spine.set_color("#555555")

            # legenda abaixo
            leg = ax.legend(loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, -0.22), fontsize=9)
            for txt in leg.get_texts(): txt.set_color(FG_TEXT)

            # 100% no topo
            for xi in x:
                ax.text(xi, 100.5, "100%", ha="center", va="bottom", color="#b0b0b0", fontsize=8)

        def render_ag(*_):
            try:
                ano = int(ano_ag_var.get())
            except Exception:
                messagebox.showwarning("Análise Gráfica", "Selecione um ano válido.")
                return

            geral_spd, geral_spf, geral_cpd, geral_cpf = [], [], [], []
            user_spd, user_spf, user_cpd, user_cpf = [], [], [], []
            base_geral, base_user = 0, 0

            for m_idx in range(12):
                m_nome = MESES_PTBR[m_idx]
                m_full = mes_full(m_nome, ano)
                metr_g, metr_u = metricas_por_mes(m_full)

                geral_spd.append(metr_g.get("sem_pend_dentro", 0))
                geral_spf.append(metr_g.get("sem_pend_fora", 0))
                geral_cpd.append(metr_g.get("com_pend_dentro", 0))
                geral_cpf.append(metr_g.get("com_pend_fora", 0))
                base_geral = metr_g.get("base_finalizados", 0)

                if metr_u is not None:
                    user_spd.append(metr_u.get("sem_pend_dentro", 0))
                    user_spf.append(metr_u.get("sem_pend_fora", 0))
                    user_cpd.append(metr_u.get("com_pend_dentro", 0))
                    user_cpf.append(metr_u.get("com_pend_fora", 0))
                    base_user = metr_u.get("base_finalizados", 0)
                else:
                    user_spd.append(0); user_spf.append(0); user_cpd.append(0); user_cpf.append(0)

            # info
            if role_atual == "admin":
                ind_base_txt = base_user if user_var.get() not in ("", "(selecione)") else 0
            else:
                ind_base_txt = base_user
            info_lbl_ag.configure(text=f"Ano: {ano} • Base (último mês consultado): Geral={base_geral} • Individual={ind_base_txt}")

            # desenhar
            desenhar(fig_g, ax_g, {
                "sem_pend_dentro": geral_spd,
                "sem_pend_fora": geral_spf,
                "com_pend_dentro": geral_cpd,
                "com_pend_fora": geral_cpf
            }, xlabels)
            canvas_g.draw()

            desenhar(fig_u, ax_u, {
                "sem_pend_dentro": user_spd,
                "sem_pend_fora": user_spf,
                "com_pend_dentro": user_cpd,
                "com_pend_fora": user_cpf
            }, xlabels)
            canvas_u.draw()

        # Binds da Aba 2
        ano_ag_var.trace_add("write", render_ag)
        # Quando o admin trocar de usuário na Aba 1, atualiza a Aba 2
        user_var.trace_add("write", render_ag)

        # Primeira renderização da Aba 2
        render_ag()



    def abrir_resultados(self, perfil):
        """
        Abre a janela 'Resultados' (layout em cards), com placeholders.
        Futuramente, esta janela exibirá:
        - Finalizados no prazo
        - Finalizados fora do prazo
        - Lojas finalizadas sem pendência (dentro do prazo)
        - Lojas finalizadas com pendência (fora do prazo)
        - Ver Log de Lançamentos
        """
        import tkinter as tk

        janela = tk.Toplevel(self.master)
        janela.title("Resultados e Métricas")
        janela.configure(bg=BG_DARK)
        janela.state("zoomed")

        # Cabeçalho: título
        tk.Label(
            janela,
            text=f"📊 Resultados — {perfil.get('nome','')} ({perfil.get('mes','')})",
            font=("Segoe UI", 14, "bold"),
            bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=(10, 6))

        # Subtítulo com contexto do perfil
        qtd_lojas = len(perfil.get("lojas", []))
        mes_nome, ano_str = (perfil.get("mes","/") or "/").split("/") if "/" in perfil.get("mes","/") else ("", "")
        info_txt = f"Lojas no perfil: {qtd_lojas}   •   Mês/Ano: {perfil.get('mes','')}"
        tk.Label(
            janela, text=info_txt, font=("Segoe UI", 10),
            bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=(0, 10))

        # Container principal
        container = tk.Frame(janela, bg=BG_DARK)
        container.pack(fill="both", expand=True, padx=20, pady=10)

        # === GRID de CARDS (Layout B) ===
        # 2 linhas x 2 colunas de cards (+ um botão central de log)
        grid = tk.Frame(container, bg=BG_DARK)
        grid.pack(anchor="n")

        def make_card(parent, titulo, cor_borda="#444444", valor_placeholder="--", dica=""):
            """
            Cria um 'card' simples: título + valor grande + (opcional) dica.
            Retorna (frame_card, label_valor) para futura atualização.
            """
            card = tk.Frame(parent, bg=BG_DARK, highlightbackground=cor_borda,
                            highlightthickness=1, bd=0)
            card.configure(width=320, height=120)
            card.pack_propagate(False)

            tk.Label(card, text=titulo, font=("Segoe UI", 11, "bold"),
                    bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", padx=12, pady=(10, 0))

            lbl_val = tk.Label(card, text=valor_placeholder,
                            font=("Segoe UI", 18, "bold"),
                            bg=BG_DARK, fg=FG_TEXT)
            lbl_val.pack(anchor="w", padx=12, pady=(2, 2))

            if dica:
                tk.Label(card, text=dica, font=("Segoe UI", 9),
                        bg=BG_DARK, fg="#b0b0b0", wraplength=280, justify="left")\
                .pack(anchor="w", padx=12, pady=(0, 10))

            return card, lbl_val

        # Configurar grid responsivo
        for c in range(2):
            grid.grid_columnconfigure(c, weight=1, pad=12)
        for r in range(2):
            grid.grid_rowconfigure(r, weight=1, pad=12)


        # Linha 1
        card1, lbl1 = make_card(
            grid,
            "Sem pendência (dentro do prazo)",
            cor_borda="#2b5c7a",
            valor_placeholder="--",
            dica="Primeira ação = Finalizado, e dentro do limite."
        )
        card1.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        card2, lbl2 = make_card(
            grid,
            "Sem pendência (fora do prazo)",
            cor_borda="#7a2b2b",
            valor_placeholder="--",
            dica="Primeira ação = Finalizado, porém após o limite."
        )
        card2.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

        # Linha 2
        card3, lbl3 = make_card(
            grid,
            "Com pendência (dentro do prazo)",
            cor_borda="#2f6f3e",
            valor_placeholder="--",
            dica="Houve pendência dentro do prazo e a finalização também ocorreu no prazo."
        )
        card3.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        card4, lbl4 = make_card(
            grid,
            "Com pendência (fora do prazo)",
            cor_borda="#7a5c2b",
            valor_placeholder="--",
            dica="Houve pendência dentro do prazo, e a finalização ocorreu após o limite."
        )
        card4.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)


        # Espaço para botões inferiores
        rodape = tk.Frame(janela, bg=BG_DARK)
        rodape.pack(side="bottom", fill="x", pady=16)

        # Botão para abrir LOG (por enquanto, placeholder sem dados)
        def abrir_log():
            import tkinter as tk
            from tkinter import ttk

            top = tk.Toplevel(janela)
            top.title("Log de Lançamentos")
            top.configure(bg=BG_DARK)
            top.geometry("900x500")

            tk.Label(
                top,
                text="📄 Log de Lançamentos",
                font=("Segoe UI", 12, "bold"),
                bg=BG_DARK,
                fg=FG_TEXT
            ).pack(pady=10)

            frame = tk.Frame(top, bg=BG_DARK)
            frame.pack(fill="both", expand=True, padx=10, pady=10)

            colunas = ("loja", "dia", "status", "data_hora", "origem")
            tree = ttk.Treeview(frame, columns=colunas, show="headings", height=20)

            # cabeçalhos
            for col in colunas:
                tree.heading(col, text=col.capitalize())
                tree.column(col, width=140)

            vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)

            tree.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")

            # === ler logs de todo o perfil ===
            logs_extraidos = []

            for loja in perfil.get("planilha", []):
                nome_loja = loja.get("loja")
                metas = loja.get("meta", {})

                for dia, meta_dia in metas.items():
                    for item in meta_dia.get("logs", []):
                        logs_extraidos.append({
                            "loja": nome_loja,
                            "dia": dia,
                            "status": item.get("status", ""),
                            "data_hora": item.get("data_hora", ""),
                            "origem": item.get("origem", ""),
                        })

            # ordenar do mais recente para mais antigo
            logs_extraidos.sort(key=lambda x: x["data_hora"], reverse=True)

            # inserir na tabela
            for log in logs_extraidos:
                tree.insert("", "end", values=(
                    log["loja"],
                    log["dia"],
                    log["status"],
                    log["data_hora"],
                    log["origem"]
                ))

            if not logs_extraidos:
                tk.Label(
                    top,
                    text="Nenhum log encontrado para este perfil.",
                    bg=BG_DARK,
                    fg=FG_TEXT
                ).pack(pady=10)

            tk.Button(
                top,
                text="Fechar",
                command=top.destroy,
                font=FONT_UI,
                bg=BG_PANEL,
                fg=FG_TEXT,
                relief="flat",
                padx=12,
                pady=6
            ).pack(pady=10)


        tk.Button(
            rodape, text="Ver Log de Lançamentos",
            command=abrir_log,
            font=FONT_UI, bg="#007acc", fg=FG_TEXT,
            activebackground="#3399ff", activeforeground="#ffffff",
            relief="flat", bd=0, padx=12, pady=8
        ).pack(side="left", padx=8)

        tk.Button(
            rodape, text="Recalcular",
            command=lambda: self.atualizar_cards_resultados(perfil, janela),
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).pack(side="left", padx=8)

        tk.Button(
            rodape, text="Fechar",
            command=janela.destroy,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).pack(side="right", padx=8)

        # Guardar referências das labels para atualização futura (quando plugarmos os cálculos)
        janela._cards_labels_resultados = {
            "sem_pend_dentro": lbl1,
            "sem_pend_fora":   lbl2,
            "com_pend_dentro": lbl3,
            "com_pend_fora":   lbl4
        }


        # Guarda referência (útil para atualizar de outros pontos) e atualiza na abertura
        self._cards_labels_resultados_ref = janela._cards_labels_resultados
        self.atualizar_cards_resultados(perfil, janela)

    def abrir_voucher(self, perfil):
        janela_voucher = tk.Toplevel(self.master)
        janela_voucher.title("Gerenciar Vouchers")
        janela_voucher.configure(bg=BG_DARK)
        janela_voucher.geometry("600x400")

        tk.Label(janela_voucher, text="Adicionar Voucher",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(pady=10)

        form_frame = tk.Frame(janela_voucher, bg=BG_DARK)
        form_frame.pack(pady=10)

        tk.Label(form_frame, text="Dia/Mês:", bg=BG_DARK, fg=FG_TEXT).grid(row=0, column=0, padx=5)
        data_var = tk.StringVar()
        tk.Entry(form_frame, textvariable=data_var, width=10,
                bg=BG_PANEL, fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat").grid(row=0, column=1, padx=5)

        tk.Label(form_frame, text="Número:", bg=BG_DARK, fg=FG_TEXT).grid(row=0, column=2, padx=5)
        numero_var = tk.StringVar()
        tk.Entry(form_frame, textvariable=numero_var, width=20,
                bg=BG_PANEL, fg=FG_TEXT, insertbackground=FG_TEXT, relief="flat").grid(row=0, column=3, padx=5)

        if "vouchers" not in perfil:
            perfil["vouchers"] = []

        def adicionar_voucher():
            data = data_var.get().strip()
            numero = numero_var.get().strip()
            if data and numero:
                perfil["vouchers"].append({"data": data, "numero": numero})
                self.salvar_perfis()
                atualizar_tabela_vouchers()
                data_var.set("")
                numero_var.set("")

        tk.Button(form_frame, text="Adicionar", command=adicionar_voucher,
                bg=BG_PANEL, fg=FG_TEXT, relief="flat").grid(row=0, column=4, padx=5)

        tabela_voucher = tk.Frame(janela_voucher, bg=BG_DARK)
        tabela_voucher.pack(fill="both", expand=True, padx=10, pady=10)

        def atualizar_tabela_vouchers():
            for w in tabela_voucher.winfo_children():
                w.destroy()
            tk.Label(tabela_voucher, text="Vouchers Registrados:",
                    font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(anchor="w")
            for i, v in enumerate(perfil["vouchers"]):
                linha = tk.Frame(tabela_voucher, bg=BG_DARK)
                linha.pack(fill="x", pady=2)
                tk.Label(linha, text=f"{v['data']} — Nº {v['numero']}",
                        bg=BG_DARK, fg=FG_TEXT, anchor="w").pack(side="left", padx=10)

                def excluir_voucher(index=i):
                    if messagebox.askyesno("Excluir", "Deseja excluir este voucher?"):
                        perfil["vouchers"].pop(index)
                        self.salvar_perfis()
                        atualizar_tabela_vouchers()

                tk.Button(linha, text="Excluir", command=excluir_voucher,
                        font=FONT_UI, bg="#8b0000", fg=FG_TEXT,
                        activebackground="#aa0000", activeforeground=FG_ACTIVE,
                        relief="flat", bd=0, padx=8, pady=4).pack(side="right", padx=6)

        atualizar_tabela_vouchers()

    def janela_editar_perfil(self, perfil):
        janela = tk.Toplevel(self.master)
        janela.title(f"Editar Perfil — {perfil['nome']}")
        janela.configure(bg=BG_DARK)
        janela.geometry("680x640")

        tk.Label(janela, text="Nome do Perfil:",
                font=FONT_UI, bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", padx=20, pady=(20, 6))
        nome_var = tk.StringVar(value=perfil["nome"])
        tk.Entry(janela, textvariable=nome_var,
                font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                insertbackground=FG_TEXT, relief="flat").pack(fill="x", padx=20)

        mes_nome, ano_str = perfil["mes"].split("/")
        ano_atual = datetime.now().year
        anos = [str(a) for a in range(ano_atual - 2, ano_atual + 3)]
        meses = MESES_PTBR[:]

        ano_var = tk.StringVar(value=ano_str)
        mes_var = tk.StringVar(value=mes_nome)

        linha_mes = tk.Frame(janela, bg=BG_DARK)
        linha_mes.pack(fill="x", padx=20, pady=(10, 0))

        tk.Label(linha_mes, text="Ano:", font=FONT_UI, bg=BG_DARK, fg=FG_TEXT)\
            .grid(row=0, column=0, sticky="w", padx=(0, 8))
        ano_menu = tk.OptionMenu(linha_mes, ano_var, *anos)
        ano_menu.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                        activeforeground=FG_ACTIVE, relief="flat", bd=0, highlightthickness=0)
        ano_menu.grid(row=0, column=1, sticky="w", padx=(0, 16))

        tk.Label(linha_mes, text="Mês:", font=FONT_UI, bg=BG_DARK, fg=FG_TEXT)\
            .grid(row=0, column=2, sticky="w", padx=(0, 8))
        mes_menu = tk.OptionMenu(linha_mes, mes_var, *meses)
        mes_menu.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                        activeforeground=FG_ACTIVE, relief="flat", bd=0, highlightthickness=0)
        mes_menu.grid(row=0, column=3, sticky="w")

        tk.Label(janela, text="Selecione as lojas:",
                font=FONT_UI, bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", padx=20, pady=(12, 6))

        canvas = tk.Canvas(janela, bg=BG_DARK, highlightthickness=0)
        frame_checks = tk.Frame(canvas, bg=BG_DARK)
        canvas.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        canvas.create_window((0, 0), window=frame_checks, anchor="nw")
        frame_checks.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

        selecoes = []
        lojas_atual = {l["loja"] for l in perfil["lojas"]}

        for reg, lista in self.agrupar_por_regional(self.lojas):
            tk.Label(frame_checks, text=f" {reg}",
                    font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=FG_ACTIVE)\
                .pack(anchor="w", pady=(10, 4))
            for loja in lista:
                var = tk.BooleanVar(value=loja["loja"] in lojas_atual)
                tk.Checkbutton(frame_checks, text=loja["loja"],
                            variable=var,
                            font=FONT_UI, bg=BG_DARK, fg=FG_TEXT,
                            activeforeground=FG_ACTIVE, activebackground=BG_DARK,
                            selectcolor=BG_DARK).pack(anchor="w", padx=12, pady=2)
                selecoes.append((var, loja))

        def salvar():
            nome = nome_var.get().strip()
            ano = ano_var.get().strip()
            mes_nome_sel = mes_var.get().strip()
            if not nome:
                messagebox.showwarning("Atenção", "Informe o nome do perfil.")
                return
            if not ano.isdigit():
                messagebox.showwarning("Atenção", "Selecione um ano válido.")
                return
            if mes_nome_sel not in MESES_PTBR:
                messagebox.showwarning("Atenção", "Selecione um mês válido.")
                return

            mes_full = f"{mes_nome_sel}/{ano}"
            lojas_sel = [loja for var, loja in selecoes if var.get()]

            perfil["nome"] = nome
            perfil["mes"] = mes_full
            perfil["lojas"] = lojas_sel

            self.salvar_perfis()
            janela.destroy()
            self.tela_controle_lojas()

        tk.Button(janela, text="Salvar", command=salvar,
                font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                activebackground="#444444", activeforeground=FG_ACTIVE,
                relief="flat", bd=0, padx=12, pady=8).pack(pady=12)

        tk.Button(janela, text="Cancelar", command=janela.destroy,
                font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                activebackground="#444444", activeforeground=FG_ACTIVE,
                relief="flat", bd=0, padx=12, pady=8).pack()

    def abrir_avaliacao(self, perfil):
        janela_avaliacao = tk.Toplevel(self.master)
        janela_avaliacao.title("Avaliação das Lojas")
        janela_avaliacao.configure(bg=BG_DARK)
        janela_avaliacao.state("zoomed")

        criterios = ["Comunicação", "Anexo de Documentação", "Procedimento Errado", "Retorno", "Pendência"]

        if "avaliacoes" not in perfil:
            perfil["avaliacoes"] = []

        tk.Label(janela_avaliacao, text=f"Avaliação — {perfil['nome']} ({perfil['mes']})",
                font=("Segoe UI", 14, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(pady=10)

        tabela_frame = tk.Frame(janela_avaliacao, bg=BG_DARK)
        tabela_frame.pack(fill="both", expand=True, padx=10, pady=10)

        tk.Label(tabela_frame, text="Loja", font=FONT_UI, bg=BG_DARK, fg=FG_TEXT, width=12)\
            .grid(row=0, column=0, sticky="nsew")
        for i, crit in enumerate(criterios, start=1):
            tk.Label(tabela_frame, text=crit, font=FONT_UI, bg="#444444", fg=FG_TEXT, width=20)\
                .grid(row=0, column=i, sticky="nsew")

        def cor_por_nota(nota):
            if nota in [1, 2]:
                return "#ff0000"   
            elif nota == 3:
                return "#ffcc00"   
            elif nota in [4, 5]:
                return "#1ca61c"   
            return "#666666"       

        for r, loja in enumerate(perfil["lojas"], start=1):
            tk.Label(tabela_frame, text=loja["loja"], font=FONT_UI, bg=BG_DARK, fg=FG_TEXT)\
                .grid(row=r, column=0, sticky="nsew")

            dados_loja = next((a for a in perfil["avaliacoes"] if a["loja"] == loja["loja"]), None)
            if not dados_loja:
                dados_loja = {"loja": loja["loja"], "notas": {}}
                perfil["avaliacoes"].append(dados_loja)

            for c, crit in enumerate(criterios, start=1):
                nota_atual = dados_loja["notas"].get(crit, "")
                cor_inicial = cor_por_nota(nota_atual) if nota_atual else "#666666"
                lbl = tk.Label(tabela_frame, text=str(nota_atual) if nota_atual else "˅",
                            bg=cor_inicial, width=7, height=1, relief="flat",
                            anchor="center", font=("Segoe UI", 10), fg=FG_TEXT)
                lbl.grid(row=r, column=c, sticky="nsew")

                def escolher_nota(event, loja_ref=dados_loja, crit_ref=crit, lbl_ref=lbl):
                    menu = tk.Menu(janela_avaliacao, tearoff=0, bg=BG_PANEL, fg=FG_TEXT)
                    for nota in range(1, 6):
                        menu.add_command(label=str(nota),
                            command=lambda n=nota: aplicar_nota(loja_ref, crit_ref, n, lbl_ref))
                    menu.tk_popup(event.x_root, event.y_root)

                lbl.bind("<Button-1>", escolher_nota)

        def aplicar_nota(loja, criterio, nota, lbl):
            loja["notas"][criterio] = nota
            lbl.configure(text=str(nota), bg=cor_por_nota(nota))
            self.salvar_perfis()

        btn_frame = tk.Frame(janela_avaliacao, bg=BG_DARK)
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="Salvar", command=self.salvar_perfis,
                font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                activebackground="#444444", activeforeground=FG_ACTIVE,
                relief="flat", bd=0, padx=12, pady=8).grid(row=0, column=0, padx=8)

        tk.Button(btn_frame, text="Voltar", command=janela_avaliacao.destroy,
                font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                activebackground="#444444", activeforeground=FG_ACTIVE,
                relief="flat", bd=0, padx=12, pady=8).grid(row=0, column=1, padx=8)
               

    def _consolidar_perfil(self, perfil):
        import pandas as pd
        import calendar

        # Extrai mês/ano e calcula número de dias
        mes_nome, ano_str = perfil["mes"].split("/")
        ano = int(ano_str)
        mes_num = MESES_PTBR.index(mes_nome) + 1
        dias_mes = calendar.monthrange(ano, mes_num)[1]

        # Garante estrutura da planilha (uma entrada por loja do perfil)
        if "planilha" not in perfil or perfil["planilha"] is None:
            perfil["planilha"] = []
        nomes_lojas = {l["loja"] for l in perfil.get("lojas", [])}
        existentes = {p["loja"] for p in perfil["planilha"]}
        for loja in perfil.get("lojas", []):
            if loja["loja"] not in existentes:
                perfil["planilha"].append({"loja": loja["loja"], "cnpj": loja["cnpj"], "dias": {}})
        perfil["planilha"] = [p for p in perfil["planilha"] if p["loja"] in nomes_lojas]

        # Garante estrutura das avaliações
        criterios = ["Comunicação", "Anexo de Documentação", "Procedimento Errado", "Retorno", "Pendência"]
        if "avaliacoes" not in perfil or perfil["avaliacoes"] is None:
            perfil["avaliacoes"] = []
        aval_por_loja = {a.get("loja"): a for a in perfil["avaliacoes"]}
        nome_auditor = perfil.get("nome", "")
        mes_perfil = perfil.get("mes", "")

        def regional_da_loja(nome_loja):
            info = next((l for l in self.lojas if l["loja"] == nome_loja), None)
            return normalizar_regional(info.get("regional") if info else "OUTROS")

        linhas = []
        total_finalizados = 0
        total_validos = 0

        for loja in perfil["planilha"]:
            # Calcula dias válidos/finalizados (domingo só conta se Finalizado)
            dias_validos, dias_finalizados = [], []
            for d in range(1, dias_mes + 1):
                dia_str = str(d)

                # >>> NOVO: respeitar vigência da loja neste perfil
                if not self._dia_dentro_vigencia(loja, d, dias_mes):
                    continue

                status = loja["dias"].get(dia_str, "")
                if calendar.weekday(ano, mes_num, d) == 6 and status != "Finalizado":
                    continue
                if status == "Domingo":
                    continue

                dias_validos.append(d)
                if status == "Finalizado":
                    dias_finalizados.append(d)

            perc = (len(dias_finalizados) / len(dias_validos)) * 100 if dias_validos else 0.0
            total_finalizados += len(dias_finalizados)
            total_validos += len(dias_validos)

            # Coleta notas e média (se existirem)
            notas_dict = {}
            media_avaliacao = 0.0
            a_loja = aval_por_loja.get(loja["loja"])
            if a_loja and a_loja.get("notas"):
                notas = a_loja["notas"]
                for crit in criterios:
                    notas_dict[crit] = notas.get(crit, "")
                if len(notas) > 0:
                    media_avaliacao = round(sum(notas.values()) / len(notas), 2)

            # Linha única por loja no perfil
            linha = {
                "Perfil": nome_auditor,  # auditor = nome do perfil
                "Mês": mes_perfil,       # mês do perfil
                "Loja": loja["loja"],
                "CNPJ (últimos 4)": cnpj_ultimos4(loja["cnpj"]),
                "Regional": regional_da_loja(loja["loja"]),
                "Dias Finalizados": len(dias_finalizados),
                "Dias Válidos": len(dias_validos),
                "% Finalizado": round(perc, 1),
                "Média": media_avaliacao,
            }
            for crit in criterios:
                linha[crit] = notas_dict.get(crit, "")
            linhas.append(linha)

        df_consolidado = pd.DataFrame(linhas)
        df_resumo = pd.DataFrame([{
            "Perfil": nome_auditor,
            "Mês": mes_perfil,
            "Total Finalizados": total_finalizados,
            "Total Dias Válidos": total_validos,
            "% Geral Finalizado": round((total_finalizados / total_validos) * 100, 1) if total_validos else 0.0,
        }])

        return df_consolidado, df_resumo


    def exportar_todos_perfis_excel(self):
        import pandas as pd
        from datetime import datetime
        import re

        if not self.perfis:
            messagebox.showinfo("Exportação", "Nenhum perfil cadastrado para exportar.")
            return

        def safe_sheet_name(name: str) -> str:
            # remove caracteres inválidos e limita a 31 (limite do Excel)
            name = re.sub(r'[\[\]\*\?/\\:]', '', name)
            return name[:31]

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        nome_arquivo = f"resultados_todos_perfis_{ts}.xlsx"

        df_geral_consol = []
        df_geral_resumo = []

        with pd.ExcelWriter(nome_arquivo, engine="openpyxl") as writer:
            for perfil in self.perfis:
                # consolida cada perfil
                try:
                    df_consol, df_resumo = self._consolidar_perfil(perfil)
                except Exception as e:
                    # evita travar exportação inteira caso algum perfil tenha mês inválido
                    df_consol = pd.DataFrame()
                    df_resumo = pd.DataFrame([{
                        "Perfil": perfil.get("nome", ""),
                        "Mês": perfil.get("mes", ""),
                        "Erro": f"Falha ao consolidar: {e}",
                    }])

                # guarda para geral
                if not df_consol.empty:
                    df_geral_consol.append(df_consol)
                if not df_resumo.empty:
                    df_geral_resumo.append(df_resumo)

                # nomes das abas (seguros)
                perfil_nome = perfil.get("nome", "")
                perfil_mes = perfil.get("mes", "")
                aba_consol = safe_sheet_name(f"Consolidado - {perfil_nome} ({perfil_mes})")
                aba_resumo = safe_sheet_name(f"Resumo - {perfil_nome}")

                # escreve as abas do perfil
                (df_consol if not df_consol.empty else pd.DataFrame([{"Info": "Sem dados"}])) \
                    .to_excel(writer, index=False, sheet_name=aba_consol)
                df_resumo.to_excel(writer, index=False, sheet_name=aba_resumo)

            # Abas gerais
            if df_geral_consol:
                df_consol_geral = pd.concat(df_geral_consol, ignore_index=True)
            else:
                df_consol_geral = pd.DataFrame([{"Info": "Nenhum dado consolidado"}])

            if df_geral_resumo:
                df_resumo_geral = pd.concat(df_geral_resumo, ignore_index=True)
                # totais agregados:
                try:
                    total_finalizados = int(df_resumo_geral["Total Finalizados"].fillna(0).sum())
                    total_validos = int(df_resumo_geral["Total Dias Válidos"].fillna(0).sum())
                    perc_geral = round((total_finalizados / total_validos) * 100, 1) if total_validos else 0.0
                    df_resumo_agregado = pd.DataFrame([{
                        "Total Finalizados (Geral)": total_finalizados,
                        "Total Dias Válidos (Geral)": total_validos,
                        "% Geral Finalizado": perc_geral,
                        "Perfis Exportados": len(self.perfis),
                        "Data Exportação": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    }])
                except Exception:
                    df_resumo_agregado = pd.DataFrame([{"Info": "Não foi possível calcular o resumo geral"}])
            else:
                df_resumo_geral = pd.DataFrame([{"Info": "Nenhum resumo por perfil"}])
                df_resumo_agregado = pd.DataFrame([{"Info": "Sem dados para agregação"}])

            df_consol_geral.to_excel(writer, index=False, sheet_name="Consolidado Geral")
            df_resumo_geral.to_excel(writer, index=False, sheet_name="Resumos por Perfil")
            df_resumo_agregado.to_excel(writer, index=False, sheet_name="Resumo Geral")

        messagebox.showinfo("Exportação", f"Arquivo '{nome_arquivo}' gerado com sucesso!")

    def importar_resultados_excel(self):
        from tkinter import filedialog
        import pandas as pd

        caminho = filedialog.askopenfilename(
            title="Selecione o arquivo Excel",
            filetypes=[("Arquivos Excel", "*.xlsx")]
        )
        if not caminho:
            return

        try:
            df = pd.read_excel(caminho, engine="openpyxl")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível ler o arquivo: {e}")
            return
            
        if "Loja" not in df.columns:
            messagebox.showerror("Erro", "Arquivo não contém a coluna 'Loja'.")
            return

        regionais = []
        for loja_val in df["Loja"]:
            loja_norm = normalizar_loja_valor(loja_val)
            info = next((l for l in self.lojas if normalizar_loja_valor(l["loja"]) == loja_norm), None)
            regional = normalizar_regional(info["regional"]) if info else "OUTROS"
            regionais.append(regional)

        df["Regional"] = regionais

        if not hasattr(self, "resultados_importados"):
            self.resultados_importados = []
        self.resultados_importados.append(df)

        self._atualizar_analise_geral()

    def _atualizar_analise_geral(self):
        for w in self.frame_resultados.winfo_children():
            w.destroy()

        if not getattr(self, "resultados_importados", []):
            tk.Label(self.frame_resultados, text="Nenhum resultado importado.",
                    bg=BG_DARK, fg=FG_TEXT).pack()
            return

        import pandas as pd
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        plt.rcParams['font.family'] = 'Segoe UI'

        df_total = pd.concat(self.resultados_importados, ignore_index=True)

        todas_medias = df_total["Média"].tolist()
        lojas_medias = df_total.groupby("Loja")["Média"].mean().to_dict()
        media_por_estado = df_total.groupby("Regional")["Média"].mean().to_dict()

        media_geral = sum(todas_medias) / len(todas_medias) if todas_medias else 0.0
        cor_geral = "#ff4d4d" if media_geral < 2.5 else "#ffcc00" if media_geral < 4 else "#1ca61c"

        self.frame_resultados.grid_columnconfigure(0, weight=1)
        self.frame_resultados.grid_columnconfigure(1, weight=1)

        col_esq = tk.Frame(self.frame_resultados, bg=BG_DARK)
        col_esq.grid(row=0, column=0, sticky="nsew", padx=20, pady=10)

        col_dir = tk.Frame(self.frame_resultados, bg=BG_DARK)
        col_dir.grid(row=0, column=1, sticky="nsew", padx=20, pady=10)

        tk.Label(col_esq, text=f"Média Geral: {media_geral:.2f}",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=cor_geral).pack(anchor="w", pady=(10, 10))

        verdes_total = (df_total["Média"] >= 4).sum()
        amarelas_total = (((df_total["Média"] >= 2.5) & (df_total["Média"] < 4))).sum()
        vermelhas_total = (df_total["Média"] < 2.5).sum()

        tk.Label(col_esq, text=f"Positivas: {verdes_total}\nRegulares: {amarelas_total}\nProblemáticas: {vermelhas_total}",
                font=("Segoe UI", 11), bg=BG_DARK, fg=FG_TEXT, justify="left").pack(anchor="w", pady=(5, 10))

        tk.Label(col_esq, text="Quantidade por Região:",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", pady=(10, 4))
        for reg in sorted(df_total["Regional"].unique()):
            subset = df_total[df_total["Regional"] == reg]
            verdes = (subset["Média"] >= 4).sum()
            amarelas = (((subset["Média"] >= 2.5) & (subset["Média"] < 4))).sum()
            vermelhas = (subset["Média"] < 2.5).sum()
            tk.Label(col_esq, text=f"{reg}: Positivas {verdes} | Regulares {amarelas} | Problemáticas {vermelhas}",
                    bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", padx=20)

        tk.Label(col_esq, text="Top 5 Melhores Lojas:",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", pady=(10, 4))
        for loja, media in sorted(lojas_medias.items(), key=lambda x: x[1], reverse=True)[:5]:
            cor = "#ff4d4d" if media < 2.5 else "#ffcc00" if media < 4 else "#1ca61c"
            tk.Label(col_esq, text=f"{normalizar_loja_valor(loja)}: Média {media:.2f}",
                    bg=BG_DARK, fg=cor).pack(anchor="w", padx=20)

        tk.Label(col_esq, text="Top 5 Piores Lojas:",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", pady=(10, 4))
        for loja, media in sorted(lojas_medias.items(), key=lambda x: x[1])[:5]:
            cor = "#ff4d4d" if media < 2.5 else "#ffcc00" if media < 4 else "#1ca61c"
            tk.Label(col_esq, text=f"{normalizar_loja_valor(loja)}: Média {media:.2f}",
                    bg=BG_DARK, fg=cor).pack(anchor="w", padx=20)

        tk.Label(col_esq, text="Média por Estado:",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", pady=(10, 4))
        for reg, media in media_por_estado.items():
            cor_reg = "#ff4d4d" if media < 2.5 else "#ffcc00" if media < 4 else "#1ca61c"
            tk.Label(col_esq, text=f"{reg}: Média {media:.2f}", bg=BG_DARK, fg=cor_reg).pack(anchor="w", padx=20)

        vermelhas = df_total[df_total["Média"] < 2.5].groupby("Regional").size()
        verdes = df_total[df_total["Média"] >= 4].groupby("Regional").size()

        cores_regionais = {
            "AM": "#1ca61c",
            "AP": "#00bfff",
            "MA": "#ffcc00",
            "MT": "#ff8c00",
            "PA": "#6a0dad",
            "RR": "#ff4d4d",
            "OUTROS": "#999999"
        }

        fig1, ax1 = plt.subplots(figsize=(3.75,3.75), facecolor=BG_DARK)
        ax1.set_facecolor(BG_DARK)
        if not vermelhas.empty:
            cores_v = [cores_regionais.get(reg, "#999999") for reg in vermelhas.index]
            ax1.pie(vermelhas, labels=vermelhas.index, autopct='%1.1f%%', startangle=90,
                    colors=cores_v, textprops={'color': 'white'})
        else:
            ax1.text(0.5, 0.5, "Sem dados", ha="center", va="center", color="white")
        ax1.set_title("Lojas Problemáticas (por região)", fontsize=11, color='white')
        for spine in ax1.spines.values():
            spine.set_visible(False)
        canvas1 = FigureCanvasTkAgg(fig1, master=col_dir)
        canvas1.get_tk_widget().pack(side="top", pady=10)

        fig2, ax2 = plt.subplots(figsize=(3.75,3.75), facecolor=BG_DARK)
        ax2.set_facecolor(BG_DARK)
        if not verdes.empty:
            cores_g = [cores_regionais.get(reg, "#999999") for reg in verdes.index]
            ax2.pie(verdes, labels=verdes.index, autopct='%1.1f%%', startangle=90,
                    colors=cores_g, textprops={'color': 'white'})
        else:
            ax2.text(0.5, 0.5, "Sem dados", ha="center", va="center", color="white")
        ax2.set_title("Lojas Positivas (por região)", fontsize=11, color='white')
        for spine in ax2.spines.values():
            spine.set_visible(False)
        canvas2 = FigureCanvasTkAgg(fig2, master=col_dir)
        canvas2.get_tk_widget().pack(side="top", pady=10)

    def abrir_analise_geral(self):
        janela = tk.Toplevel(self.master)
        janela.title("Análise Geral")
        janela.configure(bg=BG_DARK)
        janela.state("zoomed")

        tk.Label(janela, text="📈 Análise Geral de Resultados Importados",
                font=("Segoe UI", 14, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(pady=10)

        self.frame_resultados = tk.Frame(janela, bg=BG_DARK)
        self.frame_resultados.pack(fill="both", expand=True, padx=20, pady=20)

        self.resultados_importados = []

        btn_frame = tk.Frame(janela, bg=BG_DARK)
        btn_frame.pack(side="bottom", fill="x", pady=20)

        tk.Button(btn_frame, text="Importar Resultados",
                command=self.importar_resultados_excel,
                font=FONT_UI, bg="#31a252", fg=FG_TEXT,
                activebackground="#6ecf42", activeforeground="#172a38",
                relief="flat", bd=0, padx=12, pady=8).grid(row=0, column=0, padx=8)

        tk.Button(btn_frame, text="Fechar", command=janela.destroy,
                font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
                activebackground="#444444", activeforeground=FG_ACTIVE,
                relief="flat", bd=0, padx=12, pady=8).grid(row=0, column=1, padx=8)
    

    def abrir_analise_vales(self):
        import re
        import tkinter as tk
        from tkinter import messagebox
        from datetime import datetime

        # -------------------------------------------------------------
        # Helpers locais
        # -------------------------------------------------------------
        def parse_moeda_br(s: str) -> float:
            """Converte entradas como '1.234,56' ou '1234.56' em float 1234.56."""
            if s is None:
                return 0.0
            s = str(s).strip()
            if not s:
                return 0.0
            s = re.sub(r"[^0-9,\.]", "", s)
            if "," in s and "." in s:
                s = s.replace(".", "")
            s = s.replace(",", ".")
            try:
                return float(s)
            except ValueError:
                return 0.0

        def fmt_moeda_br(v: float) -> str:
            try:
                txt = f"{v:,.2f}"
                return "R$ " + txt.replace(",", "X").replace(".", ",").replace("X", ".")
            except Exception:
                return f"R$ {v:.2f}"

        def contar_filiais_por_regional():
            """Retorna {'AM': n, 'AP': n, ...} contando lojas por regional."""
            cont = {reg: 0 for reg in REGIONAIS_VALES}
            for loja in self.lojas:
                r = normalizar_regional(loja.get("regional"))
                if r in cont:
                    cont[r] += 1
            return cont

        def calcular_total_vales(valor_total: float, ticket_medio: float) -> float:
            """Quantidade estimada de vales = Valor Total / Ticket Médio."""
            if ticket_medio is None or ticket_medio <= 0:
                return 0.0
            return valor_total / ticket_medio

        def calcular_percentual_filiais(filiais_com_vale: int, total_filiais_regional: int) -> float:
            """% de filiais com vale = filiais_com_vale / total_filiais_regional."""
            if total_filiais_regional is None or total_filiais_regional <= 0:
                return 0.0
            return (filiais_com_vale / total_filiais_regional) * 100.0

        def chave_mes():
            return f"{mes_var.get()}/{ano_var.get()}"

        # -------------------------------------------------------------
        # Janela
        # -------------------------------------------------------------
        janela = tk.Toplevel(self.master)
        janela.title("Análise de Vales por Regional")
        janela.configure(bg=BG_DARK)
        janela.state("zoomed")

        tk.Label(
            janela,
            text="💳 Análise de Vales por Regional",
            font=("Segoe UI", 14, "bold"),
            bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=10)

        # -------------------------------------------------------------
        # Seleção de Ano/Mês
        # -------------------------------------------------------------
        topo = tk.Frame(janela, bg=BG_DARK)
        topo.pack(fill="x", padx=20, pady=(0, 10))

        ano_atual = datetime.now().year
        anos = [str(a) for a in range(ano_atual - 2, ano_atual + 3)]
        meses = MESES_PTBR[:]

        tk.Label(topo, text="Ano:", font=("Segoe UI", 10), bg=BG_DARK, fg=FG_TEXT)\
            .grid(row=0, column=0, sticky="w", padx=(0, 6))
        ano_var = tk.StringVar(value=str(ano_atual))
        ano_menu = tk.OptionMenu(topo, ano_var, *anos)
        ano_menu.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                        activeforeground=FG_ACTIVE, relief="flat", bd=0, highlightthickness=0)
        ano_menu.grid(row=0, column=1, sticky="w", padx=(0, 18))

        tk.Label(topo, text="Mês:", font=("Segoe UI", 10), bg=BG_DARK, fg=FG_TEXT)\
            .grid(row=0, column=2, sticky="w", padx=(0, 6))
        mes_var = tk.StringVar(value=MESES_PTBR[datetime.now().month - 1])
        mes_menu = tk.OptionMenu(topo, mes_var, *meses)
        mes_menu.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                        activeforeground=FG_ACTIVE, relief="flat", bd=0, highlightthickness=0)
        mes_menu.grid(row=0, column=3, sticky="w")

        # -------------------------------------------------------------
        # Tabela (cabeçalho + corpo) com largura física por coluna
        # -------------------------------------------------------------
        tabela_frame = tk.Frame(janela, bg=BG_DARK)
        tabela_frame.pack(fill="x", padx=20)

        cab = tk.Frame(tabela_frame, bg=BG_DARK)
        cab.pack(fill="x")

        grid = tk.Frame(tabela_frame, bg=BG_DARK)
        grid.pack(fill="x", pady=(6, 10))

        # --- Larguras físicas por coluna (em pixels) ---
        # Ajuste os números conforme necessário para seu layout/monitor.
        COLW = {
            0: 120,  # Regional
            1: 170,  # Valor Total (R$)
            2: 170,  # Ticket Médio (R$)
            3: 170,  # Filiais com Vales
            4: 140,  # Total de Filiais
            5: 140,  # Total de Vales
            6: 170,  # % Filiais com Vale
        }
        for c, w in COLW.items():
            cab.grid_columnconfigure(c, minsize=w, uniform="vales_cols_header")
            grid.grid_columnconfigure(c, minsize=w, uniform="vales_cols_body")

        # Cabeçalhos (centralizados e ocupando toda a coluna)
        tk.Label(cab, text="Regional",           font=("Segoe UI", 10, "bold"),
                bg=BG_DARK, fg=FG_TEXT, anchor="center")\
            .grid(row=0, column=0, sticky="nsew")
        tk.Label(cab, text="Valor Total (R$)",   font=("Segoe UI", 10, "bold"),
                bg=BG_DARK, fg=FG_TEXT, anchor="center")\
            .grid(row=0, column=1, sticky="nsew")
        tk.Label(cab, text="Ticket Médio (R$)",  font=("Segoe UI", 10, "bold"),
                bg=BG_DARK, fg=FG_TEXT, anchor="center")\
            .grid(row=0, column=2, sticky="nsew")
        tk.Label(cab, text="Filiais com Vales",  font=("Segoe UI", 10, "bold"),
                bg=BG_DARK, fg=FG_TEXT, anchor="center")\
            .grid(row=0, column=3, sticky="nsew")
        tk.Label(cab, text="Total de Filiais",   font=("Segoe UI", 10, "bold"),
                bg=BG_DARK, fg=FG_TEXT, anchor="center")\
            .grid(row=0, column=4, sticky="nsew")
        tk.Label(cab, text="Total de Vales",     font=("Segoe UI", 10, "bold"),
                bg=BG_DARK, fg=FG_TEXT, anchor="center")\
            .grid(row=0, column=5, sticky="nsew")
        tk.Label(cab, text="% Filiais com Vale", font=("Segoe UI", 10, "bold"),
                bg=BG_DARK, fg=FG_TEXT, anchor="center")\
            .grid(row=0, column=6, sticky="nsew")

        linhas_vars = {}
        totais_reg = contar_filiais_por_regional()

        # -------------------------------------------------------------
        # Atualiza cálculos por linha
        # -------------------------------------------------------------
        def atualizar_linha(reg: str):
            v = parse_moeda_br(linhas_vars[reg]["valor_var"].get())
            t = parse_moeda_br(linhas_vars[reg]["ticket_var"].get())
            try:
                f = int((linhas_vars[reg]["filiais_var"].get() or "0").strip())
            except ValueError:
                f = 0

            total_fix = totais_reg.get(reg, 0)
            total_vales = calcular_total_vales(v, t)
            perc = calcular_percentual_filiais(f, total_fix)

            linhas_vars[reg]["lbl_total_vales"].config(text=f"{int(round(total_vales))}")
            linhas_vars[reg]["lbl_perc_filiais"].config(text=f"{perc:.1f}%".replace(".", ","))

        # -------------------------------------------------------------
        # Resumo geral
        # -------------------------------------------------------------
        resumo_frame = tk.Frame(janela, bg=BG_DARK)
        resumo_frame.pack(fill="x", padx=20, pady=(0, 10))

        lbl_res_total_valor = tk.Label(resumo_frame, text="Total Valor (R$): R$ 0,00",
                                    font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=FG_TEXT)
        lbl_res_total_valor.pack(anchor="w")

        lbl_res_total_filiais_vale = tk.Label(resumo_frame, text="Filiais com Vale (mês): 0 de 0 (0,0%)",
                                            font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=FG_TEXT)
        lbl_res_total_filiais_vale.pack(anchor="w")

        lbl_res_ticket_geral = tk.Label(resumo_frame, text="Ticket Médio Geral: R$ 0,00",
                                        font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=FG_TEXT)
        lbl_res_ticket_geral.pack(anchor="w")

        def atualizar_resumo():
            total_valor = 0.0
            total_filiais_vale = 0

            # recalcula totais fixos (caso haja alterações no cadastro de lojas)
            totais_fix = contar_filiais_por_regional()
            total_filiais_fix = sum(totais_fix.values())

            for reg in REGIONAIS_VALES:
                v = parse_moeda_br(linhas_vars[reg]["valor_var"].get())
                try:
                    f = int((linhas_vars[reg]["filiais_var"].get() or "0").strip())
                except ValueError:
                    f = 0
                total_valor += v
                total_filiais_vale += f

            ticket_geral = (total_valor / total_filiais_vale) if total_filiais_vale > 0 else 0.0
            perc_filiais_ativas = (total_filiais_vale / total_filiais_fix * 100.0) if total_filiais_fix > 0 else 0.0

            lbl_res_total_valor.configure(text=f"Total Valor (R$): {fmt_moeda_br(total_valor)}")
            lbl_res_ticket_geral.configure(text=f"Ticket Médio Geral: {fmt_moeda_br(ticket_geral)}")
            lbl_res_total_filiais_vale.configure(
                text=f"Filiais com Vale (mês): {total_filiais_vale} de {total_filiais_fix} "
                    f"({perc_filiais_ativas:.1f}%)".replace(".", ",")
            )

        # -------------------------------------------------------------
        # Monta linhas por regional (centralizado; mesma largura por coluna)
        # -------------------------------------------------------------
        for i, reg in enumerate(REGIONAIS_VALES, start=1):
            # Regional (Label)
            tk.Label(grid, text=reg, font=("Segoe UI", 10),
                    bg=BG_DARK, fg=FG_TEXT, anchor="center")\
                .grid(row=i, column=0, sticky="nsew", pady=3)

            # Valor Total (Entry centralizado)
            v_var = tk.StringVar()
            e_valor = tk.Entry(grid, textvariable=v_var, font=("Segoe UI", 10),
                            bg=BG_PANEL, fg=FG_TEXT, insertbackground=FG_TEXT,
                            relief="flat", justify="center")
            e_valor.grid(row=i, column=1, sticky="nsew", pady=3)

            # Ticket Médio (Entry centralizado)
            t_var = tk.StringVar()
            e_ticket = tk.Entry(grid, textvariable=t_var, font=("Segoe UI", 10),
                                bg=BG_PANEL, fg=FG_TEXT, insertbackground=FG_TEXT,
                                relief="flat", justify="center")
            e_ticket.grid(row=i, column=2, sticky="nsew", pady=3)

            # Filiais com Vales (Entry centralizado)
            f_var = tk.StringVar()
            e_filiais = tk.Entry(grid, textvariable=f_var, font=("Segoe UI", 10),
                                bg=BG_PANEL, fg=FG_TEXT, insertbackground=FG_TEXT,
                                relief="flat", justify="center")
            e_filiais.grid(row=i, column=3, sticky="nsew", pady=3)

            # Total de Filiais (Label somente leitura)
            lbl_total_fix = tk.Label(grid, text=str(totais_reg.get(reg, 0)), font=("Segoe UI", 10),
                                    bg=BG_DARK, fg=FG_TEXT, anchor="center")
            lbl_total_fix.grid(row=i, column=4, sticky="nsew", pady=3)

            # Total de Vales (Label somente leitura)
            lbl_total_vales = tk.Label(grid, text="0", font=("Segoe UI", 10),
                                    bg=BG_DARK, fg=FG_TEXT, anchor="center")
            lbl_total_vales.grid(row=i, column=5, sticky="nsew", pady=3)

            # % Filiais com Vale (Label somente leitura)
            lbl_perc_filiais = tk.Label(grid, text="0,0%", font=("Segoe UI", 10),
                                        bg=BG_DARK, fg=FG_TEXT, anchor="center")
            lbl_perc_filiais.grid(row=i, column=6, sticky="nsew", pady=3)

            # Registro das variáveis/labels para atualizações
            linhas_vars[reg] = {
                "valor_var": v_var,
                "ticket_var": t_var,
                "filiais_var": f_var,
                "lbl_total_vales": lbl_total_vales,
                "lbl_perc_filiais": lbl_perc_filiais,
                # Se quiser usar o total fixo depois, pode registrar também:
                # "lbl_total_fix": lbl_total_fix
            }

            # Atualização automática (linha + resumo)
            v_var.trace_add("write", lambda *a, r=reg: (atualizar_linha(r), atualizar_resumo()))
            t_var.trace_add("write", lambda *a, r=reg: (atualizar_linha(r), atualizar_resumo()))
            f_var.trace_add("write", lambda *a, r=reg: (atualizar_linha(r), atualizar_resumo()))

        # -------------------------------------------------------------
        # Rodapé de ações
        # -------------------------------------------------------------
        rodape = tk.Frame(janela, bg=BG_DARK)
        rodape.pack(fill="x", pady=16)

        def carregar_mes():
            """Carrega dados salvos (valor_total, ticket_medio, filiais) para o mês atual."""
            dados = self.analise_vales.get(chave_mes(), {})
            for reg in REGIONAIS_VALES:
                info = dados.get(reg, {"valor_total": 0.0, "ticket_medio": 0.0, "filiais": 0})

                v = float(info.get("valor_total", 0.0) or 0.0)
                linhas_vars[reg]["valor_var"].set("" if v == 0 else f"{v:.2f}".replace(".", ","))

                t = float(info.get("ticket_medio", 0.0) or 0.0)
                linhas_vars[reg]["ticket_var"].set("" if t == 0 else f"{t:.2f}".replace(".", ","))

                f = int(info.get("filiais", 0) or 0)
                linhas_vars[reg]["filiais_var"].set("" if f == 0 else str(f))

                atualizar_linha(reg)

            atualizar_resumo()

        def salvar_mes():
            """Valida e salva os números digitados para o mês atual."""
            bloco = {}
            for reg in REGIONAIS_VALES:
                v = parse_moeda_br(linhas_vars[reg]["valor_var"].get())
                t = parse_moeda_br(linhas_vars[reg]["ticket_var"].get())
                try:
                    f = int((linhas_vars[reg]["filiais_var"].get() or "0").strip())
                except ValueError:
                    messagebox.showwarning("Atenção", f"Informe número válido de 'Filiais com Vales' para {reg}.")
                    return

                bloco[reg] = {
                    "valor_total": round(v, 2),
                    "ticket_medio": round(t, 2),
                    "filiais": max(0, f),
                }

            self.analise_vales[chave_mes()] = bloco
            self.salvar_analise_vales()
            messagebox.showinfo("Sucesso", f"Dados salvos para {chave_mes()}.")

        tk.Button(
            rodape, text="Carregar Mês", command=carregar_mes,
            font=("Segoe UI", 10), bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=6
        ).grid(row=0, column=0, padx=8)

        tk.Button(
            rodape, text="Salvar Mês", command=salvar_mes,
            font=("Segoe UI", 10), bg="#31a252", fg=FG_TEXT,
            activebackground="#6ecf42", activeforeground="#172a38",
            relief="flat", bd=0, padx=12, pady=6
        ).grid(row=0, column=1, padx=8)

        tk.Button(
            rodape, text="Fechar", command=janela.destroy,
            font=("Segoe UI", 10), bg="#8b0000", fg=FG_TEXT,
            activebackground="#aa0000", activeforeground=FG_TEXT,
            relief="flat", bd=0, padx=12, pady=6
        ).grid(row=0, column=2, padx=8)

        # -------------------------------------------------------------
        # Botão abaixo dos botões principais: Comparar 3 meses
        # -------------------------------------------------------------
        comparativo_frame = tk.Frame(janela, bg=BG_DARK)
        comparativo_frame.pack(fill="x", pady=(0, 12))

        tk.Button(
            comparativo_frame,
            text="Comparar 3 meses",
            command=self.abrir_comparativo_vales_3m,
            font=("Segoe UI", 10),
            bg="#007acc", fg=FG_TEXT,
            activebackground="#3399ff", activeforeground="#ffffff",
            relief="flat", bd=0, padx=12, pady=6
        ).pack(anchor="center")

        # Abre com o mês atual carregado
        carregar_mes()


        def limpar_mes():
            for reg in REGIONAIS_VALES:
                linhas_vars[reg] = {
                    "valor_var": v_var,
                    "ticket_var": t_var,
                    "filiais_var": f_var
                }

            atualizar_resumo()

        def exportar_excel():
            """Exporta APENAS o mês atual para XLSX."""
            from datetime import datetime as _dt
            import pandas as _pd

            chave = mes_full(mes_var.get(), ano_var.get())
            dados = self.analise_vales.get(chave, {})
            # monta DF
            linhas = []
            total_valor, total_filiais = 0.0, 0
            for reg in REGIONAIS_VALES:
                info = dados.get(reg, {"valor_total": 0.0, "filiais": 0})
                v = float(info.get("valor_total", 0.0))
                f = int(info.get("filiais", 0))
                linhas.append({"Regional": reg, "Valor Total (R$)": v, "Filiais com Vales": f})
                total_valor += v
                total_filiais += f
            df = _pd.DataFrame(linhas)
            media = (total_valor / total_filiais) if total_filiais > 0 else 0.0

            caminho = filedialog.asksaveasfilename(
                title="Salvar Excel",
                defaultextension=".xlsx",
                initialfile=f"vales_{chave.replace('/','-')}.xlsx",
                filetypes=[("Excel", "*.xlsx")]
            )
            if not caminho:
                return
            try:
                with _pd.ExcelWriter(caminho, engine="openpyxl") as writer:
                    df.to_excel(writer, index=False, sheet_name="Vales por Regional")
                    _pd.DataFrame([{
                        "Mês": chave,
                        "Total Valor (R$)": round(total_valor, 2),
                        "Total Filiais": total_filiais,
                        "Média por Filial (R$)": round(media, 2)
                    }]).to_excel(writer, index=False, sheet_name="Resumo")
                messagebox.showinfo("Exportação", f"Arquivo salvo em:\n{caminho}")
            except Exception as e:
                messagebox.showerror("Erro", f"Falha ao exportar: {e}")

        btn_carregar = tk.Button(
            rodape, text="Carregar Mês", command=carregar_mes,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        )
        btn_carregar.grid(row=0, column=0, padx=8)

        btn_salvar = tk.Button(
            rodape, text="Salvar Mês", command=salvar_mes,
            font=FONT_UI, bg="#31a252", fg=FG_TEXT,
            activebackground="#6ecf42", activeforeground="#172a38",
            relief="flat", bd=0, padx=12, pady=8
        )
        btn_salvar.grid(row=0, column=1, padx=8)

        btn_limpar = tk.Button(
            rodape, text="Limpar Mês", command=limpar_mes,
            font=FONT_UI, bg="#8b0000", fg=FG_TEXT,
            activebackground="#aa0000", activeforeground=FG_TEXT,
            relief="flat", bd=0, padx=12, pady=8
        )
        btn_limpar.grid(row=0, column=2, padx=8)

        btn_fechar = tk.Button(
            rodape, text="Fechar", command=janela.destroy,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        )
        btn_fechar.grid(row=0, column=3, padx=8)
        
        # liga alterações nos campos ao resumo
        for reg in REGIONAIS_VALES:
            linhas_vars[reg]["valor_var"].trace_add("write", lambda *args: atualizar_resumo())
            linhas_vars[reg]["filiais_var"].trace_add("write", lambda *args: atualizar_resumo())

        # inicializa com o mês/ano atuais
        carregar_mes()



    def abrir_comparativo_vales_3m(self):
        import tkinter as tk
        from tkinter import ttk, messagebox

        # -----------------------------
        # FORMATADORES E FUNÇÕES BASE
        # -----------------------------
        def fmt_moeda_br(v):
            """R$ 1.234,56"""
            try:
                txt = f"{float(v):,.2f}"
                return "R$ " + txt.replace(",", "X").replace(".", ",").replace("X", ".")
            except:
                return f"R$ {v:.2f}"

        def contar_filiais_por_regional():
            cont = {reg: 0 for reg in REGIONAIS_VALES}
            for loja in self.lojas:
                r = normalizar_regional(loja.get("regional"))
                if r in cont:
                    cont[r] += 1
            return cont

        def calcular_total_vales(valor_total, ticket_medio):
            # qtd estimada de vales = Valor Total / Ticket Médio
            if ticket_medio is None or ticket_medio <= 0:
                return 0.0
            return valor_total / ticket_medio

        def calcular_percentual_filiais(f_ativas, total_fix):
            # % filiais com vale
            if total_fix <= 0:
                return 0.0
            return (f_ativas / total_fix) * 100.0

        def ordenar_meses(lista):
            def key(x):
                try:
                    m, y = x.split("/")
                    return (int(y), MESES_PTBR.index(m) + 1)
                except:
                    return (0, 0)
            return sorted(lista, key=key)

        def obter_por_regional(mes_key):
            dados_mes = self.analise_vales.get(mes_key, {})
            totais_fix = contar_filiais_por_regional()
            out = {}
            for reg in REGIONAIS_VALES:
                info = dados_mes.get(reg, {"valor_total": 0.0, "ticket_medio": 0.0, "filiais": 0})
                v = float(info.get("valor_total", 0) or 0)
                t = float(info.get("ticket_medio", 0) or 0)
                f = int(info.get("filiais", 0) or 0)
                fix = int(totais_fix.get(reg, 0) or 0)
                out[reg] = {
                    "valor_total": v,
                    "ticket_medio": t,
                    "filiais": f,
                    "total_vales": calcular_total_vales(v, t),
                    "perc_filiais": calcular_percentual_filiais(f, fix),
                    "total_fix": fix
                }
            return out
        
        def gerar_pdf(m1_key, m2_key, m3_key):
            """
            Gera um PDF (paisagem A4) com:
            1) Resumo geral dos 3 meses
            2) Uma página por métrica (tabelas segmentadas por regional)
            """
            try:
                # 1) Obter dados brutos por regional para cada mês
                dados_m1 = obter_por_regional(m1_key)  # {'AM': {...}, 'AP': {...}, ...}
                dados_m2 = obter_por_regional(m2_key)
                dados_m3 = obter_por_regional(m3_key)

                # contagem fixa de filiais por regional (para % filiais com vale)
                totais_fix = contar_filiais_por_regional()

                # 2) Preparar destino (Save As)
                caminho = filedialog.asksaveasfilename(
                    title="Salvar PDF do comparativo (3 meses)",
                    defaultextension=".pdf",
                    initialfile=f"comparativo_vales_{m1_key.replace('/','-')}_{m2_key.replace('/','-')}_{m3_key.replace('/','-')}.pdf",
                    filetypes=[("PDF", "*.pdf")]
                )
                if not caminho:
                    return

                # 3) Documento e estilos
                doc = SimpleDocTemplate(
                    caminho,
                    pagesize=landscape(A4),
                    leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24
                )
                story = []
                styles = getSampleStyleSheet()
                H1 = styles["Heading1"]
                H2 = styles["Heading2"]
                H3 = styles["Heading3"]
                Body = styles["BodyText"]

                # Helpers de formatação (reuso do que você já usa na UI)
                def fmt_moeda(v):
                    try:
                        txt = f"{float(v):,.2f}"
                        return "R$ " + txt.replace(",", "X").replace(".", ",").replace("X", ".")
                    except:
                        return f"R$ {v}"

                def fmt_pp(v):
                    try:
                        return f"{float(v):.2f}%".replace(".", ",")
                    except:
                        return f"{v}%"

                def fmt_int(v):
                    try:
                        return str(int(round(float(v))))
                    except:
                        return str(v)

                def pct_rel(b, a):
                    try:
                        a = float(a); b = float(b)
                    except:
                        return None
                    if a == 0:
                        return 0.0 if b == 0 else None
                    return (b - a) / abs(a) * 100.0

                def delta_texto(v_ant, v_atual, is_percent, is_money, is_integer):
                    try:
                        a = float(v_ant or 0.0)
                        b = float(v_atual or 0.0)
                    except:
                        a = b = 0.0

                    if is_integer:
                        a_i, b_i = int(round(a)), int(round(b))
                        d = b_i - a_i
                        abs_txt = f"{d:+d}"
                        pr = pct_rel(b_i, a_i)
                    else:
                        d = b - a
                        if is_percent:
                            abs_txt = f"{d:+.2f} p.p.".replace(".", ",")
                        elif is_money:
                            base = f"R$ {abs(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                            abs_txt = f"+ {base}" if d >= 0 else f"- {base}"
                        else:
                            abs_txt = f"{d:+.2f}".replace(".", ",")
                        pr = pct_rel(b, a)

                    perc_txt = "—" if pr is None else f"{pr:+.2f}%".replace(".", ",")
                    return f"{abs_txt} / {perc_txt}"

                # 4) RESUMO GERAL (primeira página)
                story.append(Paragraph("Análise de Vales — Comparativo de 3 meses", H1))
                story.append(Paragraph(f"Meses selecionados: {m1_key} • {m2_key} • {m3_key}", Body))
                story.append(Spacer(1, 8))

                # Agregados por mês (somatórios) para construir o resumo
                def agregados(d):
                    total_valor = sum((d[r].get("valor_total", 0.0) or 0.0) for r in REGIONAIS_VALES)
                    total_filiais_ativas = int(sum((d[r].get("filiais", 0) or 0) for r in REGIONAIS_VALES))
                    total_fix = int(sum((totais_fix.get(r, 0) or 0) for r in REGIONAIS_VALES))
                    # total de vales: somatório das regiões (estimativa já calculada por região)
                    total_vales = sum((d[r].get("total_vales", 0.0) or 0.0) for r in REGIONAIS_VALES)
                    # ticket médio geral (ponderado por filiais ativas)
                    ticket_geral = (total_valor / total_filiais_ativas) if total_filiais_ativas > 0 else 0.0
                    perc_filiais_ativas = (total_filiais_ativas / total_fix * 100.0) if total_fix > 0 else 0.0
                    return {
                        "valor_total": total_valor,
                        "filiais_ativas": total_filiais_ativas,
                        "total_vales": total_vales,
                        "ticket_geral": ticket_geral,
                        "perc_filiais": perc_filiais_ativas
                    }

                agg1 = agregados(dados_m1)
                agg2 = agregados(dados_m2)
                agg3 = agregados(dados_m3)

                # Monta tabela de resumo geral
                resumo_data = [
                    ["Métrica", m1_key, m2_key, m3_key, f"Δ {m2_key} − {m1_key}", f"Δ {m3_key} − {m2_key}"],
                    ["Valor Total (R$)",
                    fmt_moeda(agg1["valor_total"]), fmt_moeda(agg2["valor_total"]), fmt_moeda(agg3["valor_total"]),
                    delta_texto(agg1["valor_total"], agg2["valor_total"], False, True, False),
                    delta_texto(agg2["valor_total"], agg3["valor_total"], False, True, False)],
                    ["Filiais com Vales",
                    fmt_int(agg1["filiais_ativas"]), fmt_int(agg2["filiais_ativas"]), fmt_int(agg3["filiais_ativas"]),
                    delta_texto(agg1["filiais_ativas"], agg2["filiais_ativas"], False, False, True),
                    delta_texto(agg2["filiais_ativas"], agg3["filiais_ativas"], False, False, True)],
                    ["Total de Vales (qtd)",
                    fmt_int(agg1["total_vales"]), fmt_int(agg2["total_vales"]), fmt_int(agg3["total_vales"]),
                    delta_texto(agg1["total_vales"], agg2["total_vales"], False, False, True),
                    delta_texto(agg2["total_vales"], agg3["total_vales"], False, False, True)],
                    ["Ticket Médio Geral (R$)",
                    fmt_moeda(agg1["ticket_geral"]), fmt_moeda(agg2["ticket_geral"]), fmt_moeda(agg3["ticket_geral"]),
                    delta_texto(agg1["ticket_geral"], agg2["ticket_geral"], False, True, False),
                    delta_texto(agg2["ticket_geral"], agg3["ticket_geral"], False, True, False)],
                    ["% Filiais com Vale",
                    fmt_pp(agg1["perc_filiais"]), fmt_pp(agg2["perc_filiais"]), fmt_pp(agg3["perc_filiais"]),
                    delta_texto(agg1["perc_filiais"], agg2["perc_filiais"], True, False, False),
                    delta_texto(agg2["perc_filiais"], agg3["perc_filiais"], True, False, False)],
                ]

                tbl_resumo = Table(resumo_data, repeatRows=1)
                tbl_resumo.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#444444")),
                    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                    ("ALIGN", (1,1), (-1,-1), "RIGHT"),
                    ("ALIGN", (0,0), (0,-1), "LEFT"),
                    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                    ("FONTSIZE", (0,0), (-1,-1), 9),
                    ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#666666")),
                    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#1e1e1e"), colors.HexColor("#2a2a2a")]),
                    ("TEXTCOLOR", (0,1), (-1,-1), colors.white),
                ]))
                story.append(Paragraph("Resumo geral", H2))
                story.append(Spacer(1, 6))
                story.append(tbl_resumo)
                story.append(PageBreak())

                # 5) Páginas por MÉTRICA (2 seções por página, empilhadas)
                SECOES_POR_PAG = 2   # ajuste aqui se quiser 3 por página, por exemplo
                sec_count = 0

                # METRICAS: (titulo, chave, fmt, is_money, is_percent, is_integer)
                for idx, (titulo, chave, _, is_money, is_percent, is_integer) in enumerate(METRICAS):
                    # Cabeçalhos
                    story.append(Paragraph(titulo, H2))
                    story.append(Paragraph(f"{m1_key} • {m2_key} • {m3_key}", H3))
                    story.append(Spacer(1, 6))

                    # Monta dados da tabela
                    data = [["Regional", m1_key, m2_key, m3_key,
                            f"Δ {m2_key} − {m1_key}", f"Δ {m3_key} − {m2_key}"]]

                    for reg in REGIONAIS_VALES:
                        v1 = (dados_m1.get(reg, {}) or {}).get(chave, 0)
                        v2 = (dados_m2.get(reg, {}) or {}).get(chave, 0)
                        v3 = (dados_m3.get(reg, {}) or {}).get(chave, 0)

                        # formatação por tipo
                        if is_integer:
                            f1, f2, f3 = fmt_int(v1), fmt_int(v2), fmt_int(v3)
                        elif is_money:
                            f1, f2, f3 = fmt_moeda(v1), fmt_moeda(v2), fmt_moeda(v3)
                        elif is_percent:
                            f1, f2, f3 = fmt_pp(v1), fmt_pp(v2), fmt_pp(v3)
                        else:
                            f1 = f"{v1}"
                            f2 = f"{v2}"
                            f3 = f"{v3}"

                        d21 = delta_texto(v1, v2, is_percent, is_money, is_integer)
                        d32 = delta_texto(v2, v3, is_percent, is_money, is_integer)

                        data.append([reg, f1, f2, f3, d21, d32])

                    # Linha TOTAL para métricas somáveis
                    if chave in ("valor_total", "filiais", "total_vales"):
                        def soma(d, k): return sum((d[r].get(k, 0) or 0) for r in REGIONAIS_VALES)
                        sv1, sv2, sv3 = soma(dados_m1, chave), soma(dados_m2, chave), soma(dados_m3, chave)
                        if is_integer:
                            s1, s2, s3 = fmt_int(sv1), fmt_int(sv2), fmt_int(sv3)
                        elif is_money:
                            s1, s2, s3 = fmt_moeda(sv1), fmt_moeda(sv2), fmt_moeda(sv3)
                        else:
                            s1, s2, s3 = f"{sv1}", f"{sv2}", f"{sv3}"
                        d21 = delta_texto(sv1, sv2, is_percent, is_money, is_integer)
                        d32 = delta_texto(sv2, sv3, is_percent, is_money, is_integer)
                        data.append(["TOTAL", s1, s2, s3, d21, d32])

                    tbl = Table(data, repeatRows=1)
                    tbl.setStyle(TableStyle([
                        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#444444")),
                        ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
                        ("ALIGN",       (1,1), (-1,-1), "RIGHT"),
                        ("ALIGN",       (0,0), (0,-1), "LEFT"),
                        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
                        ("FONTSIZE",    (0,0), (-1,-1), 9),             # se quiser caber mais, reduza p/ 8
                        ("GRID",        (0,0), (-1,-1), 0.25, colors.HexColor("#666666")),
                        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#1e1e1e"), colors.HexColor("#2a2a2a")]),
                        ("TEXTCOLOR",   (0,1), (-1,-1), colors.white),
                    ]))

                    story.append(tbl)
                    sec_count += 1

                    # Entre seções da mesma página: um espaçador
                    # Quebra de página só depois de SECOES_POR_PAG seções
                    is_last = (idx == len(METRICAS) - 1)
                    if (sec_count % SECOES_POR_PAG == 0) and not is_last:
                        story.append(PageBreak())
                    else:
                        # pequeno respiro para a próxima seção na mesma página
                        story.append(Spacer(1, 10))


                # 6) Construir PDF
                doc.build(story)
                messagebox.showinfo("PDF", f"Arquivo gerado com sucesso!\n{caminho}")

            except Exception as e:
                messagebox.showerror("PDF", f"Falha ao gerar PDF: {e}")


        # -----------------------------
        # VARIAÇÕES (Δ absoluto + % relativa)
        # -----------------------------
        def pct_rel(b, a):
            """% relativa de a->b (com sinal). Se a==0: 0->0 => 0%; senão => None (indefinido)."""
            try:
                a = float(a)
                b = float(b)
            except:
                return None
            if a == 0:
                return 0.0 if b == 0 else None
            return (b - a) / abs(a) * 100.0

        def cor_delta(v):
            try:
                v = float(v)
            except:
                return FG_TEXT
            if v > 0:
                return "#ff4d4d"   # positivo -> vermelho
            if v < 0:
                return "#1ca61c"   # negativo -> verde
            return FG_TEXT        # neutro

        def delta_texto(v_ant, v_atual, is_percent_metric, is_money, is_integer):
            """
            Retorna (texto, d):
            - texto = "Δ absoluto / % relativa"
            - d     = delta numérico (para cor)
            Regras:
            - monetário: "+ R$ 1.234,56" (formato A) com centavos
            - percentual: "+1,23 p.p."
            - quantidade: inteiro (sem casas)
            """
            try:
                a = float(v_ant or 0)
                b = float(v_atual or 0)
            except:
                a, b = 0.0, 0.0

            if is_integer:
                a_int = int(round(a))
                b_int = int(round(b))
                d = b_int - a_int
                abs_txt = f"{d:+d}"
                pr = pct_rel(b_int, a_int)
            else:
                d = b - a
                if is_percent_metric:
                    abs_txt = f"{d:+.2f} p.p.".replace(".", ",")
                elif is_money:
                    base = f"R$ {abs(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    abs_txt = f"+ {base}" if d >= 0 else f"- {base}"
                else:
                    abs_txt = f"{d:+.2f}".replace(".", ",")
                pr = pct_rel(b, a)

            perc_txt = "—" if pr is None else f"{pr:+.2f}%".replace(".", ",")
            return f"{abs_txt} / {perc_txt}", d

        # -----------------------------
        # JANELA
        # -----------------------------
        janela = tk.Toplevel(self.master)
        janela.title("Análise Comparativa de Vales (3 meses)")
        janela.configure(bg=BG_DARK)
        janela.state("zoomed")

        tk.Label(
            janela,
            text="📊 Comparativo de Vales — Seleção de 3 meses",
            font=("Segoe UI", 14, "bold"),
            bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=10)

        meses_disponiveis = ordenar_meses(list(self.analise_vales.keys()))
        if len(meses_disponiveis) < 3:
            messagebox.showinfo("Comparativo", "É necessário ter ao menos 3 meses salvos.")
            return

        # ---------- Seleção (dark) ----------
        topo = tk.Frame(janela, bg=BG_DARK)
        topo.pack(fill="x", padx=20, pady=(0, 8))

        style = ttk.Style()
        style.theme_use("default")
        # Notebook dark
        style.configure("TNotebook", background=BG_DARK, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_PANEL, foreground=FG_TEXT)
        style.map("TNotebook.Tab", background=[("selected", "#444444")])
        # Combobox dark
        style.configure(
            "Dark.TCombobox",
            fieldbackground=BG_PANEL, background=BG_PANEL,
            foreground=FG_TEXT, arrowcolor=FG_TEXT
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", BG_PANEL), ("!disabled", BG_PANEL)],
            foreground=[("readonly", FG_TEXT), ("!disabled", FG_TEXT)],
            background=[("readonly", BG_PANEL), ("!disabled", BG_PANEL)]
        )

        tk.Label(topo, text="Mês 1:", bg=BG_DARK, fg=FG_TEXT).grid(row=0, column=0, sticky="w")
        mes1_var = tk.StringVar(value=meses_disponiveis[-3])
        cb1 = ttk.Combobox(topo, textvariable=mes1_var, values=meses_disponiveis,
                        state="readonly", style="Dark.TCombobox", width=20)
        cb1.grid(row=0, column=1, sticky="w", padx=(6, 16))

        tk.Label(topo, text="Mês 2:", bg=BG_DARK, fg=FG_TEXT).grid(row=0, column=2, sticky="w")
        mes2_var = tk.StringVar(value=meses_disponiveis[-2])
        cb2 = ttk.Combobox(topo, textvariable=mes2_var, values=meses_disponiveis,
                        state="readonly", style="Dark.TCombobox", width=20)
        cb2.grid(row=0, column=3, sticky="w", padx=(6, 16))

        tk.Label(topo, text="Mês 3:", bg=BG_DARK, fg=FG_TEXT).grid(row=0, column=4, sticky="w")
        mes3_var = tk.StringVar(value=meses_disponiveis[-1])
        cb3 = ttk.Combobox(topo, textvariable=mes3_var, values=meses_disponiveis,
                        state="readonly", style="Dark.TCombobox", width=20)
        cb3.grid(row=0, column=5, sticky="w", padx=(6, 0))

        # ---------- Notebook ----------
        notebook = ttk.Notebook(janela)
        notebook.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        METRICAS = [
            #  titulo                chave            fmt                     is_money is_percent is_integer
            ("Valor Total (R$)",     "valor_total",   fmt_moeda_br,           True,    False,     False),
            ("Ticket Médio (R$)",    "ticket_medio",  fmt_moeda_br,           True,    False,     False),
            ("Filiais com Vales",    "filiais",       lambda x: str(int(x)),  False,   False,     True),
            ("Total de Vales (qtd)", "total_vales",   lambda x: str(int(round(x))), False, False, True),
            ("% Filiais com Vale",   "perc_filiais",  lambda x: f"{x:.2f}%".replace('.', ','), False, True, False),
        ]
        abas = []

        def montar_cabecalho(tbl, m1, m2, m3):
            headers = ("Regional", m1, m2, m3, f"Δ {m2} − {m1}", f"Δ {m3} − {m2}")
            for c, h in enumerate(headers):
                tk.Label(
                    tbl, text=h, font=("Segoe UI", 10, "bold"),
                    bg="#444444", fg=FG_TEXT, anchor="center", padx=6, pady=4
                ).grid(row=0, column=c, sticky="nsew", padx=1, pady=1)
                tbl.grid_columnconfigure(c, weight=1)

        def criar_linha(tbl, r, reg, m1, m2, m3, d21_txt, d21_val, d32_txt, d32_val):
            # colunas neutras
            tk.Label(tbl, text=reg, bg=BG_DARK, fg=FG_TEXT, anchor="w", padx=6)\
                .grid(row=r, column=0, sticky="nsew", padx=1, pady=1)
            tk.Label(tbl, text=m1,  bg=BG_DARK, fg=FG_TEXT, anchor="e", padx=6)\
                .grid(row=r, column=1, sticky="nsew", padx=1, pady=1)
            tk.Label(tbl, text=m2,  bg=BG_DARK, fg=FG_TEXT, anchor="e", padx=6)\
                .grid(row=r, column=2, sticky="nsew", padx=1, pady=1)
            tk.Label(tbl, text=m3,  bg=BG_DARK, fg=FG_TEXT, anchor="e", padx=6)\
                .grid(row=r, column=3, sticky="nsew", padx=1, pady=1)
            # colunas Δ (coloridas)
            tk.Label(tbl, text=d21_txt, bg=BG_DARK, fg=cor_delta(d21_val), anchor="e", padx=6)\
                .grid(row=r, column=4, sticky="nsew", padx=1, pady=1)
            tk.Label(tbl, text=d32_txt, bg=BG_DARK, fg=cor_delta(d32_val), anchor="e", padx=6)\
                .grid(row=r, column=5, sticky="nsew", padx=1, pady=1)

        # criar abas
        for titulo, chave, fmt, is_money, is_percent_metric, is_integer in METRICAS:
            f = tk.Frame(notebook, bg=BG_DARK)
            notebook.add(f, text=titulo)
            abas.append((f, titulo, chave, fmt, is_money, is_percent_metric, is_integer))

        # ---------- RESUMO (dark + % coloridas) ----------
        resumo = tk.Frame(janela, bg=BG_DARK)
        resumo.pack(fill="x", padx=20, pady=(0, 10))

        lbl_resumo = tk.Label(resumo, bg=BG_DARK, fg=FG_TEXT, font=("Segoe UI", 11, "bold"))
        lbl_resumo.pack(anchor="w")

        def make_txt(parent):
            t = tk.Text(parent, bg=BG_DARK, fg=FG_TEXT, relief="flat", height=1, font=("Segoe UI", 11))
            t.tag_configure("pos", foreground="#ff4d4d")
            t.tag_configure("neg", foreground="#1ca61c")
            t.tag_configure("neu", foreground=FG_TEXT)
            t.configure(state="disabled")
            t.pack(anchor="w", fill="x")
            return t

        txt_valor  = make_txt(resumo)
        txt_filial = make_txt(resumo)
        txt_totalv = make_txt(resumo)
        txt_ticket = make_txt(resumo)
        txt_perc   = make_txt(resumo)

        def write_line(widget, parts):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            for txt, tag in parts:
                widget.insert("end", txt, tag if tag else None)
            widget.configure(state="disabled")

        # -----------------------------
        # RENDER (expande, corrige agregados e resumo)
        # -----------------------------
        def render():
            m1 = mes1_var.get()
            m2 = mes2_var.get()
            m3 = mes3_var.get()

            if len({m1, m2, m3}) < 3:
                messagebox.showwarning("Seleção", "Escolha 3 meses diferentes.")
                return

            D1 = obter_por_regional(m1)
            D2 = obter_por_regional(m2)
            D3 = obter_por_regional(m3)

            # ---- TABELAS POR MÉTRICA (ocupar largura total) ----
            for frame, titulo, chave, fmt, is_money, is_percent_metric, is_integer in abas:
                for w in frame.winfo_children():
                    w.destroy()

                container = tk.Frame(frame, bg=BG_DARK)
                container.pack(fill="both", expand=True)

                tbl = tk.Frame(container, bg=BG_DARK, highlightbackground="#555555", highlightthickness=1)
                tbl.pack(fill="both", expand=True, padx=10, pady=10)

                montar_cabecalho(tbl, m1, m2, m3)

                for i, reg in enumerate(REGIONAIS_VALES, start=1):
                    v1 = D1.get(reg, {}).get(chave, 0) or 0
                    v2 = D2.get(reg, {}).get(chave, 0) or 0
                    v3 = D3.get(reg, {}).get(chave, 0) or 0

                    t1 = fmt(v1)
                    t2 = fmt(v2)
                    t3 = fmt(v3)

                    d21_txt, d21_val = delta_texto(v1, v2, is_percent_metric, is_money, is_integer)
                    d32_txt, d32_val = delta_texto(v2, v3, is_percent_metric, is_money, is_integer)

                    criar_linha(tbl, i, reg, t1, t2, t3, d21_txt, d21_val, d32_txt, d32_val)

            # ---- RESUMO (agregado correto) ----
            lbl_resumo.configure(text=f"Resumo agregado — {m1} vs {m2} vs {m3}")

            def agregados(D):
                tot_valor = sum((D[r].get("valor_total", 0) or 0) for r in REGIONAIS_VALES)
                tot_filiais_vale = sum((D[r].get("filiais", 0) or 0) for r in REGIONAIS_VALES)
                tot_fix = sum((D[r].get("total_fix", 0) or 0) for r in REGIONAIS_VALES)
                tot_vales = sum((D[r].get("total_vales", 0) or 0) for r in REGIONAIS_VALES)
                # ticket médio ponderado pelo nº de filiais com vale
                if tot_filiais_vale > 0:
                    soma_tm = sum((D[r].get("ticket_medio", 0) or 0) * (D[r].get("filiais", 0) or 0) for r in REGIONAIS_VALES)
                    ticket = soma_tm / tot_filiais_vale
                else:
                    ticket = 0.0
                perc_ativ = (tot_filiais_vale / tot_fix * 100.0) if tot_fix > 0 else 0.0
                return tot_valor, tot_filiais_vale, tot_vales, ticket, perc_ativ

            V1, F1, T1, K1, P1 = agregados(D1)
            V2, F2, T2, K2, P2 = agregados(D2)
            V3, F3, T3, K3, P3 = agregados(D3)

            # função de linha do resumo — assinatura explícita (evita embaralhar flags)
            def linha(widget, label, a, b, c, *, tipo, mes1, mes2, mes3):
                # valores base
                if tipo == "money":
                    a_txt, b_txt, c_txt = fmt_moeda_br(a), fmt_moeda_br(b), fmt_moeda_br(c)
                elif tipo == "percent":
                    a_txt = f"{a:.2f}%".replace(".", ",")
                    b_txt = f"{b:.2f}%".replace(".", ",")
                    c_txt = f"{c:.2f}%".replace(".", ",")
                else:  # int
                    a_txt, b_txt, c_txt = str(int(round(a))), str(int(round(b))), str(int(round(c)))

                # flags para delta
                is_money   = (tipo == "money")
                is_percent = (tipo == "percent")
                is_int     = (tipo == "int")

                d21_txt, d21_val = delta_texto(a, b, is_percent, is_money, is_int)
                d32_txt, d32_val = delta_texto(b, c, is_percent, is_money, is_int)

                # somente a parte % recebe cor
                p21 = d21_txt.split(" / ")[1]
                p32 = d32_txt.split(" / ")[1]
                tag21 = "pos" if d21_val > 0 else "neg" if d21_val < 0 else "neu"
                tag32 = "pos" if d32_val > 0 else "neg" if d32_val < 0 else "neu"

                parts = [
                    (f"{label}: {a_txt} → {b_txt} → {c_txt}  |  Δ {mes2} − {mes1}: ", None),
                    (p21, tag21),
                    (f"  |  Δ {mes3} − {mes2}: ", None),
                    (p32, tag32)
                ]
                write_line(widget, parts)

            # escrever resumo
            linha(txt_valor,  "Valor Total (R$)", V1, V2, V3, tipo="money",   mes1=m1, mes2=m2, mes3=m3)
            linha(txt_filial, "Filiais com Vale", F1, F2, F3, tipo="int",     mes1=m1, mes2=m2, mes3=m3)
            linha(txt_totalv, "Total de Vales",   T1, T2, T3, tipo="int",     mes1=m1, mes2=m2, mes3=m3)
            linha(txt_ticket, "Ticket Médio (R$)",K1, K2, K3, tipo="money",   mes1=m1, mes2=m2, mes3=m3)
            linha(txt_perc,   "% Filiais com Vale", P1, P2, P3, tipo="percent", mes1=m1, mes2=m2, mes3=m3)

        # Botões (tema dark)
        botoes = tk.Frame(janela, bg=BG_DARK)
        botoes.pack(pady=10)

        tk.Button(botoes, text="Calcular", bg="#31a252", fg=FG_TEXT,
                command=render, relief="flat", padx=12, pady=6).pack(side="left", padx=10)
     
        btn_pdf = tk.Button(
            topo,  # ou no seu rodapé de botões, caso exista
            text="Gerar PDF",
            command=lambda: gerar_pdf(mes1_var.get(), mes2_var.get(), mes3_var.get()),
            font=("Segoe UI", 10), bg="#31a252", fg=FG_TEXT,
            activebackground="#6ecf42", activeforeground="#172a38",
            relief="flat", bd=0, padx=12, pady=6
        )
        btn_pdf.grid(row=0, column=6, padx=(16, 0))  # ajuste a posição como preferir

        tk.Button(botoes, text="Fechar", bg=BG_PANEL, fg=FG_TEXT,
                command=janela.destroy, relief="flat", padx=12, pady=6).pack(side="left", padx=10)

        # primeira renderização
        render()




    def abrir_analise_avaliacao(self):
        janela = tk.Toplevel(self.master)
        janela.title("Análise das Avaliações")
        janela.configure(bg=BG_DARK)
        janela.state("zoomed")

        tk.Label(
            janela,
            text="📋 Análise das Avaliações das Lojas",
            font=("Segoe UI", 14, "bold"),
            bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=10)

        meses_unicos = sorted(
            {p.get("mes", "") for p in self.perfis if p.get("mes")},
            key=lambda s: (int(s.split("/")[1]), MESES_PTBR.index(s.split("/")[0]) + 1)
        )
        if not meses_unicos:
            tk.Label(janela, text="Nenhum perfil encontrado.", bg=BG_DARK, fg=FG_TEXT).pack(pady=10)
            return

        mes_var = tk.StringVar(value=meses_unicos[-1])
        topo = tk.Frame(janela, bg=BG_DARK)
        topo.pack(fill="x", padx=20, pady=(0, 10))

        tk.Label(topo, text="Mês:", font=FONT_UI, bg=BG_DARK, fg=FG_TEXT).pack(side="left", padx=(0, 8))
        mes_menu = tk.OptionMenu(topo, mes_var, *meses_unicos)
        mes_menu.configure(
            bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, highlightthickness=0
        )
        mes_menu.pack(side="left")

        container = tk.Frame(janela, bg=BG_DARK)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        def render():
            for w in container.winfo_children():
                w.destroy()

            mes_sel = mes_var.get()
            perfis_mes = [p for p in self.perfis if p.get("mes") == mes_sel]

            if not perfis_mes:
                tk.Label(container, text="Nenhum perfil com avaliações para o mês selecionado.",
                        bg=BG_DARK, fg=FG_TEXT).pack()
                return

            for perfil in perfis_mes:
                if "avaliacoes" not in perfil or not perfil["avaliacoes"]:
                    perfil["avaliacoes"] = []
                self._exibir_coluna_perfil(perfil, container)

        render()
        mes_var.trace_add("write", lambda *args: render())

        btn_frame = tk.Frame(janela, bg=BG_DARK)
        btn_frame.pack(side="bottom", fill="x", pady=20)

        tk.Button(
            btn_frame, text="Fechar", command=janela.destroy,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=1, padx=8)

        tk.Button(
            btn_frame, text="Análise Geral",
            command=self.abrir_analise_geral,   
            font=FONT_UI, bg="#007acc", fg=FG_TEXT,
            activebackground="#3399ff", activeforeground="#ffffff",
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=0, padx=8)


    def abrir_resultados_mes_perfis(self):
        janela = tk.Toplevel(self.master)
        janela.title("Resultados do mês — Perfis")
        janela.configure(bg=BG_DARK)
        janela.state("zoomed")

        tk.Label(
            janela, text="📊 Resultados do mês (Quadro por Perfis)",
            font=("Segoe UI", 14, "bold"), bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=(10, 10))

        meses_unicos = sorted(
            {p.get("mes", "") for p in self.perfis if p.get("mes")},
            key=lambda s: (int(s.split("/")[1]), MESES_PTBR.index(s.split("/")[0]) + 1)
        )
        if not meses_unicos:
            tk.Label(janela, text="Nenhum perfil encontrado.", bg=BG_DARK, fg=FG_TEXT).pack(pady=10)
            return

        mes_var = tk.StringVar(value=meses_unicos[-1]) 
        topo = tk.Frame(janela, bg=BG_DARK)
        topo.pack(fill="x", padx=20, pady=(0, 10))

        tk.Label(topo, text="Mês:", font=FONT_UI, bg=BG_DARK, fg=FG_TEXT).pack(side="left", padx=(0, 8))
        mes_menu = tk.OptionMenu(topo, mes_var, *meses_unicos)
        mes_menu.configure(bg=BG_PANEL, fg=FG_TEXT, activebackground="#444444",
                        activeforeground=FG_ACTIVE, relief="flat", bd=0, highlightthickness=0)
        mes_menu.pack(side="left")

        frame_resultados = tk.Frame(janela, bg=BG_DARK)
        frame_resultados.pack(fill="both", expand=True, padx=20, pady=20)

        rodape = tk.Frame(janela, bg=BG_DARK)
        rodape.pack(side="bottom", fill="x", pady=20)

        tk.Button(
            rodape, text="Análise Geral",
            command=self.abrir_analise_geral_simples, 
            font=FONT_UI, bg="#007acc", fg=FG_TEXT,
            activebackground="#3399ff", activeforeground="#ffffff",
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=0, padx=8)

        tk.Button(
            rodape, text="Fechar", command=janela.destroy,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=1, padx=8)

        def sincronizar_planilha(perfil):
            """Garante que perfil['planilha'] exista e tenha uma entrada por loja do perfil."""
            if "lojas" not in perfil or not perfil["lojas"]:
                return False 
            if "planilha" not in perfil or perfil["planilha"] is None:
                perfil["planilha"] = []
            nomes_lojas = {l["loja"] for l in perfil["lojas"]}
            existentes = {p["loja"] for p in perfil["planilha"]}
            for loja in perfil["lojas"]:
                if loja["loja"] not in existentes:
                    perfil["planilha"].append({"loja": loja["loja"], "cnpj": loja["cnpj"], "dias": {}})
            perfil["planilha"] = [p for p in perfil["planilha"] if p["loja"] in nomes_lojas]
            return True

        def render():
            for w in frame_resultados.winfo_children():
                w.destroy()

            mes_sel = mes_var.get()
            perfis_mes = [p for p in self.perfis if p.get("mes") == mes_sel]

            if not perfis_mes:
                tk.Label(frame_resultados, text="Nenhum perfil para o mês selecionado.",
                        bg=BG_DARK, fg=FG_TEXT).pack(pady=10)
                return

            container = tk.Frame(frame_resultados, bg=BG_DARK)
            container.pack(fill="both", expand=True)

            total_finalizados_geral = 0
            total_validos_geral = 0

            for perfil in perfis_mes:
                try:
                    mes_nome, ano_str = perfil["mes"].split("/")
                    ano = int(ano_str)
                    mes_num = MESES_PTBR.index(mes_nome) + 1
                    dias_mes = calendar.monthrange(ano, mes_num)[1]
                except Exception:
                    col_warn = tk.Frame(container, bg=BG_DARK)
                    col_warn.pack(side="left", fill="y", padx=30)
                    tk.Label(col_warn, text=f"Perfil: {perfil.get('nome','(sem nome)')} — mês inválido: {perfil.get('mes')}",
                            bg=BG_DARK, fg="#ff4d4d").pack(anchor="w")
                    continue

                if not sincronizar_planilha(perfil):
                    col_empty = tk.Frame(container, bg=BG_DARK)
                    col_empty.pack(side="left", fill="y", padx=30)
                    tk.Label(
                        col_empty,
                        text=f"Perfil: {perfil.get('nome','(sem nome)')} ({perfil.get('mes')})",
                        font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_ACTIVE
                    ).pack(anchor="w", pady=(10, 6))
                    tk.Label(col_empty, text="Nenhuma loja cadastrada neste perfil.",
                            bg=BG_DARK, fg=FG_TEXT).pack(anchor="w")
                    continue

                col = tk.Frame(container, bg=BG_DARK)
                col.pack(side="left", fill="y", padx=30)

                tk.Label(
                    col, text=f"Perfil: {perfil['nome']} ({perfil['mes']})",
                    font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_ACTIVE
                ).pack(anchor="w", pady=(10, 6))

                linhas_texto = []
                total_finalizados = 0
                total_validos = 0

                for loja in perfil["planilha"]:
                    dias_validos = []
                    dias_finalizados = []
                    for d in range(1, dias_mes + 1):
                        dia_str = str(d)
                        status = loja["dias"].get(dia_str, "")
                        if calendar.weekday(ano, mes_num, d) == 6 and status != "Finalizado":
                            continue
                        if status == "Domingo":
                            continue
                        dias_validos.append(d)
                        if status == "Finalizado":
                            dias_finalizados.append(d)

                    perc = (len(dias_finalizados) / len(dias_validos)) * 100 if dias_validos else 0.0
                    total_finalizados += len(dias_finalizados)
                    total_validos += len(dias_validos)

                    linhas_texto.append(
                        f"Loja {loja['loja']}: {len(dias_finalizados)} de {len(dias_validos)} dias — {perc:.1f}% Finalizado"
                    )

                if not linhas_texto:
                    tk.Label(col, text="Nenhum dia válido (apenas Domingos ou vazio).",
                            bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", padx=10)

                n = len(linhas_texto)
                import math
                itens_por_coluna = math.ceil(n / 2) if n else 0

                cols_frame = tk.Frame(col, bg=BG_DARK)
                cols_frame.pack(fill="x", expand=False, anchor="w")
                col_esq = tk.Frame(cols_frame, bg=BG_DARK)
                col_esq.grid(row=0, column=0, sticky="nw", padx=(0, 24))
                col_dir = tk.Frame(cols_frame, bg=BG_DARK)
                col_dir.grid(row=0, column=1, sticky="nw")

                for i in range(itens_por_coluna):
                    tk.Label(col_esq, text=linhas_texto[i], bg=BG_DARK, fg=FG_TEXT,
                            anchor="w", justify="left").pack(anchor="w")
                for i in range(itens_por_coluna, n):
                    tk.Label(col_dir, text=linhas_texto[i], bg=BG_DARK, fg=FG_TEXT,
                            anchor="w", justify="left").pack(anchor="w")

                if total_validos:
                    geral = (total_finalizados / total_validos) * 100
                    tk.Label(
                        col,
                        text=f"Total no mês: {total_finalizados} de {total_validos} dias — {geral:.1f}% Finalizado",
                        font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=FG_TEXT,
                        wraplength=480, justify="left", anchor="w"
                    ).pack(anchor="w", pady=(8, 0))

                total_finalizados_geral += total_finalizados
                total_validos_geral += total_validos

        render()
        mes_var.trace_add("write", lambda *args: render())

    def abrir_analise_geral_simples(self):
        janela = tk.Toplevel(self.master)
        janela.title("Análise Geral (Sem Gráficos)")
        janela.configure(bg=BG_DARK)
        janela.state("zoomed")

        tk.Label(
            janela,
            text="📊 Análise Geral de Resultados Importados (sem gráficos)",
            font=("Segoe UI", 14, "bold"), bg=BG_DARK, fg=FG_TEXT
        ).pack(pady=10)

        frame_resumo = tk.Frame(janela, bg=BG_DARK)
        frame_resumo.pack(fill="both", expand=True, padx=20, pady=20)

        btn_frame = tk.Frame(janela, bg=BG_DARK)
        btn_frame.pack(side="bottom", fill="x", pady=20)

        self.resultados_importados_simples = []

        def render_resumo():
            for w in frame_resumo.winfo_children():
                w.destroy()

            import pandas as pd

            if not self.resultados_importados_simples:
                tk.Label(frame_resumo, text="Nenhum resultado importado.",
                        bg=BG_DARK, fg=FG_TEXT).pack(anchor="w")
                return

            df_total = pd.concat(self.resultados_importados_simples, ignore_index=True)

            linhas_lojas = []
            total_finalizados = 0
            total_validos = 0

            tem_dias_cols = {"Dias Finalizados", "Dias Válidos"}.issubset(df_total.columns)
            tem_perc_cols = {"% Finalizado", "Dias Válidos"}.issubset(df_total.columns)

            if not ("Loja" in df_total.columns):
                tk.Label(frame_resumo,
                        text="Arquivo importado não contém a coluna 'Loja'.",
                        bg=BG_DARK, fg="#ff4d4d").pack(anchor="w")
                return

            for loja, grupo in df_total.groupby("Loja"):
                if tem_dias_cols:
                    f = int(grupo["Dias Finalizados"].fillna(0).sum())
                    v = int(grupo["Dias Válidos"].fillna(0).sum())
                elif tem_perc_cols:
                    import math
                    v = int(grupo["Dias Válidos"].fillna(0).sum())
                    estimados = []
                    for _, row in grupo.iterrows():
                        perc = float(row.get("% Finalizado", 0) or 0)
                        val = int(row.get("Dias Válidos", 0) or 0)
                        estimados.append(int(round((perc/100.0) * val)))
                    f = int(sum(estimados))
                else:
                    f, v = 0, 0

                p = (f / v * 100) if v else 0.0
                linhas_lojas.append(f"Loja {normalizar_loja_valor(loja)}: {f} de {v} dias — {p:.1f}% Finalizado")
                total_finalizados += f
                total_validos += v

            tk.Label(
                frame_resumo, text="Resultados por Loja:",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT
            ).pack(anchor="w", pady=(0, 6))

            import math
            n = len(linhas_lojas)
            itens_por_coluna = math.ceil(n / 2) if n else 0

            cols_frame = tk.Frame(frame_resumo, bg=BG_DARK)
            cols_frame.pack(fill="x", expand=False, anchor="w")
            col_esq = tk.Frame(cols_frame, bg=BG_DARK)
            col_esq.grid(row=0, column=0, sticky="nw", padx=(0, 24))
            col_dir = tk.Frame(cols_frame, bg=BG_DARK)
            col_dir.grid(row=0, column=1, sticky="nw")

            for i in range(itens_por_coluna):
                tk.Label(col_esq, text=linhas_lojas[i], bg=BG_DARK, fg=FG_TEXT,
                        anchor="w", justify="left").pack(anchor="w")
            for i in range(itens_por_coluna, n):
                tk.Label(col_dir, text=linhas_lojas[i], bg=BG_DARK, fg=FG_TEXT,
                        anchor="w", justify="left").pack(anchor="w")

            perc_geral = (total_finalizados / total_validos * 100) if total_validos else 0.0
            tk.Label(
                frame_resumo,
                text=f"Total no mês: {total_finalizados} de {total_validos} dias — {perc_geral:.1f}% Finalizado",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_TEXT
            ).pack(anchor="w", pady=(10, 0))

            if (not tem_dias_cols) and (not tem_perc_cols):
                tk.Label(
                    frame_resumo,
                    text=("Observação: não encontrei colunas suficientes para calcular os resultados. "
                        "Use arquivos com 'Dias Finalizados' e 'Dias Válidos', "
                        "ou ao menos '% Finalizado' + 'Dias Válidos'."),
                    bg=BG_DARK, fg="#ffcc00", wraplength=700, justify="left"
                ).pack(anchor="w", pady=(8, 6))

        def importar_resultados_excel_simples():
            from tkinter import filedialog
            import pandas as pd

            caminho = filedialog.askopenfilename(
                title="Selecione o arquivo Excel",
                filetypes=[("Arquivos Excel", "*.xlsx")]
            )
            if not caminho:
                return

            try:
                df = pd.read_excel(caminho, engine="openpyxl")
            except Exception as e:
                messagebox.showerror("Erro", f"Não foi possível ler o arquivo: {e}")
                return

            if "Loja" not in df.columns:
                messagebox.showerror("Erro", "Arquivo não contém a coluna 'Loja'.")
                return

            regionais = []
            for loja_val in df["Loja"]:
                loja_norm = normalizar_loja_valor(loja_val)
                info = next((l for l in self.lojas if normalizar_loja_valor(l["loja"]) == loja_norm), None)
                regional = normalizar_regional(info["regional"]) if info else "OUTROS"
                regionais.append(regional)
            df["Regional"] = regionais

            self.resultados_importados_simples.append(df)
            render_resumo()

        tk.Button(
            btn_frame, text="Importar Resultados",
            command=importar_resultados_excel_simples,
            font=FONT_UI, bg="#31a252", fg=FG_TEXT,
            activebackground="#6ecf42", activeforeground="#172a38",
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=0, padx=8)

        tk.Button(
            btn_frame, text="Fechar", command=janela.destroy,
            font=FONT_UI, bg=BG_PANEL, fg=FG_TEXT,
            activebackground="#444444", activeforeground=FG_ACTIVE,
            relief="flat", bd=0, padx=12, pady=8
        ).grid(row=0, column=1, padx=8)

        render_resumo()

        def regional_da_loja(nome_loja):
            info = next((l for l in self.lojas if l["loja"] == nome_loja), None)
            return normalizar_regional(info.get("regional") if info else "OUTROS")

        def calcular_e_exibir():
            for w in frame_resultados.winfo_children():
                w.destroy()

            mes_sel = mes_var.get()
            perfis_mes = [p for p in self.perfis if p.get("mes") == mes_sel]

            if not perfis_mes:
                tk.Label(frame_resultados, text="Nenhum perfil para o mês selecionado.",
                        bg=BG_DARK, fg=FG_TEXT).pack(pady=10)
                return

            container = tk.Frame(frame_resultados, bg=BG_DARK)
            container.pack(fill="both", expand=True)

            total_finalizados_geral = 0
            total_validos_geral = 0

            for perfil in perfis_mes:
                mes_nome, ano_str = perfil["mes"].split("/")
                ano = int(ano_str)
                mes_num = MESES_PTBR.index(mes_nome) + 1
                dias_mes = calendar.monthrange(ano, mes_num)[1]

                if "planilha" not in perfil or perfil["planilha"] is None:
                    perfil["planilha"] = []
                nomes_lojas = {l["loja"] for l in perfil["lojas"]}
                existentes = {p["loja"] for p in perfil["planilha"]}
                for loja in perfil["lojas"]:
                    if loja["loja"] not in existentes:
                        perfil["planilha"].append({"loja": loja["loja"], "cnpj": loja["cnpj"], "dias": {}})
                perfil["planilha"] = [p for p in perfil["planilha"] if p["loja"] in nomes_lojas]

                col = tk.Frame(container, bg=BG_DARK)
                col.pack(side="left", fill="y", padx=30)

                tk.Label(
                    col, text=f"Perfil: {perfil['nome']} ({perfil['mes']})",
                    font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_ACTIVE
                ).pack(anchor="w", pady=(10, 6))

                linhas_texto = []
                total_finalizados = 0
                total_validos = 0

                for loja in perfil["planilha"]:
                    dias_validos = []
                    dias_finalizados = []
                    for d in range(1, dias_mes + 1):
                        dia_str = str(d)
                        status = loja["dias"].get(dia_str, "")
                        if calendar.weekday(ano, mes_num, d) == 6 and status != "Finalizado":
                            continue
                        if status == "Domingo":
                            continue
                        dias_validos.append(d)
                        if status == "Finalizado":
                            dias_finalizados.append(d)

                    perc = (len(dias_finalizados) / len(dias_validos)) * 100 if dias_validos else 0.0
                    total_finalizados += len(dias_finalizados)
                    total_validos += len(dias_validos)

                    linhas_texto.append(
                        f"Loja {loja['loja']}: {len(dias_finalizados)} de {len(dias_validos)} dias — {perc:.1f}% Finalizado"
                    )

                n = len(linhas_texto)
                import math
                itens_por_coluna = math.ceil(n / 2) if n else 0

                cols_frame = tk.Frame(col, bg=BG_DARK)
                cols_frame.pack(fill="x", expand=False, anchor="w")
                col_esq = tk.Frame(cols_frame, bg=BG_DARK)
                col_esq.grid(row=0, column=0, sticky="nw", padx=(0, 24))
                col_dir = tk.Frame(cols_frame, bg=BG_DARK)
                col_dir.grid(row=0, column=1, sticky="nw")

                for i in range(itens_por_coluna):
                    tk.Label(col_esq, text=linhas_texto[i], bg=BG_DARK, fg=FG_TEXT,
                            anchor="w", justify="left").pack(anchor="w")
                for i in range(itens_por_coluna, n):
                    tk.Label(col_dir, text=linhas_texto[i], bg=BG_DARK, fg=FG_TEXT,
                            anchor="w", justify="left").pack(anchor="w")

                if total_validos:
                    geral = (total_finalizados / total_validos) * 100
                    tk.Label(
                        col,
                        text=f"Total no mês: {total_finalizados} de {total_validos} dias — {geral:.1f}% Finalizado",
                        font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=FG_TEXT,
                        wraplength=480, justify="left", anchor="w"
                    ).pack(anchor="w", pady=(8, 0))

                total_finalizados_geral += total_finalizados
                total_validos_geral += total_validos

        calcular_e_exibir()
        mes_var.trace_add("write", lambda *args: calcular_e_exibir())

    def _exibir_coluna_perfil(self, perfil, container):
        coluna = tk.Frame(container, bg=BG_DARK)
        coluna.pack(side="left", fill="y", padx=30)

        tk.Label(coluna, text=f"Perfil: {perfil['nome']} ({perfil['mes']})",
                font=("Segoe UI", 12, "bold"), bg=BG_DARK, fg=FG_ACTIVE).pack(anchor="w", pady=(10, 6))

        todas_notas = []
        regionais_medias = {}
        regionais_contagem = {}

        for loja in perfil["avaliacoes"]:
            notas = loja["notas"]
            if notas:
                media = sum(notas.values()) / len(notas)
                todas_notas.append(media)
                cor = "#ff0000" if media < 2.5 else "#ffcc00" if media < 3.5 else "#1ca61c"
                tk.Label(coluna, text=f"Loja {loja['loja']}: Média {media:.2f}",
                        bg=BG_DARK, fg=cor).pack(anchor="w", padx=10)

                loja_info = next((l for l in self.lojas if l["loja"] == loja["loja"]), {})
                regional = loja_info.get("regional", "OUTROS").upper()
                regionais_medias[regional] = regionais_medias.get(regional, 0.0) + media
                regionais_contagem[regional] = regionais_contagem.get(regional, 0) + 1

        if todas_notas:
            media_geral = sum(todas_notas) / len(todas_notas)
            cor_geral = "#ff0000" if media_geral < 2.5 else "#ffcc00" if media_geral < 3.5 else "#1ca61c"
            tk.Label(coluna, text=f"Média Geral: {media_geral:.2f}",
                    font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=cor_geral).pack(anchor="w", pady=(10, 6))

        tk.Label(coluna, text="Média por Estado:",
                font=("Segoe UI", 11, "bold"), bg=BG_DARK, fg=FG_TEXT).pack(anchor="w", pady=(10, 4))
        for reg in sorted(regionais_medias.keys()):
            total = regionais_medias[reg]
            count = regionais_contagem[reg]
            media_regional = total / count if count else 0.0
            cor_reg = "#ff0000" if media_regional < 2.5 else "#ffcc00" if media_regional < 3.5 else "#1ca61c"
            tk.Label(coluna, text=f"{reg}: Média {media_regional:.2f}",
                    bg=BG_DARK, fg=cor_reg).pack(anchor="w", padx=20)