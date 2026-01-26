
# conciliador_pix_maquineta.py
import re
import pandas as pd
from PyPDF2 import PdfReader
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# =========================================================
# 1) PARSERS DE VENDAS (Cupons, Notas, Recibos) - PIX MAQUINETA
# =========================================================

def _smart_to_float_brl(x: object):
    """Converte valores BR (9.999,99 / 999,99) para float com 2 casas, tolerando ruídos."""
    s = re.sub(r"[^\d,.\-]", "", str(x))
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    try:
        return round(float(s), 2)
    except:
        return None

def parse_cupom_pix_maq_pdf(pdf_path: str) -> pd.DataFrame:
    """
    Extrai vendas PIX MAQUINETA do RELATÓRIO CUPOM FISCAL (PDF).
    Retorna DataFrame: ['origem','doc','tipo_cartao','valor_venda'] com tipo_cartao='Pix Maquineta'.
    """
    header_pat = re.compile(r"^\s*(\d{3,6})\s+\d{3,6}\s+\d{3}(?:\s+NORMAL\b)?", re.IGNORECASE)
    header_search_pat = re.compile(r"\b(\d{3,6})\s+\d{3,6}\s+\d{3}\b(?:\s+NORMAL\b)?", re.IGNORECASE)
    stop_totais_pat = re.compile(r"(?i)DESCRICAO\s+TOTAIS|TOTAL\s+DIA|^TOTAL\b")
    page_footer_pat = re.compile(r"(?i)RUA\s+PALMEIRA|C\.N\.P\.J|Email\s*:")
    venda_maq_pat = re.compile(r"(?i)\bVENDA\s+PIX\s+MAQUINETA\b")
    money_pat = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")

    rows, seen = [], set()
    reader = PdfReader(pdf_path)
    current_coo, in_block = None, False

    for page in reader.pages:
        lines = [l.strip() for l in (page.extract_text() or "").split("\n") if l.strip()]
        for ln in lines:
            m_head = header_pat.match(ln) or header_search_pat.search(ln)
            if m_head:
                current_coo = m_head.group(1)
                in_block = True
                continue

            if stop_totais_pat.search(ln):
                current_coo = None
                in_block = False
                continue

            if page_footer_pat.search(ln):
                continue

            if not in_block or not current_coo:
                continue

            if venda_maq_pat.search(ln):
                vals = money_pat.findall(ln)
                if not vals:
                    continue
                valor = _smart_to_float_brl(vals[-1])
                if valor is None:
                    continue
                key = (current_coo, valor)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "origem": "CUPOM",
                    "doc": current_coo,
                    "tipo_cartao": "Pix Maquineta",
                    "valor_venda": valor
                })
    return pd.DataFrame(rows)

def parse_nf_pix_maq_pdf(pdf_path: str) -> pd.DataFrame:
    """
    Extrai vendas 'VENDA PIX MAQUINETA' de Notas Fiscais (PDF).
    Retorna DataFrame: ['origem','doc','serie','tipo_cartao','valor_venda'] com tipo_cartao='Pix Maquineta'.
    """
    nf_num_start_pat = re.compile(r"^\s*(\d{5,6})(?:\s+(\d{1,4}))?\b")
    nf_verbal_pat = re.compile(r"(?i)\b(?:NOTA\s+FISCAL|NF)\b.*?\b(?:N[ºO]\.?\s*)?(\d{5,6})\b")
    serie_pat = re.compile(r"(?i)\bS[ÉE]RIE\b\s*(\d{1,4})\b")
    money_pat = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")

    page_header_pat = re.compile(r"(?i)RELATORIO|EMISSAO|USUARIO/HORARIO|FILIAL|PERIODO")
    page_footer_pat = re.compile(r"(?i)RUA\s+PALMEIRA|C\.N\.P\.J|Email\s*:")
    stop_totais_pat = re.compile(r"(?i)DESCRICAO\s+TOTAIS|TOTAL\s+GERAL|TOTAL\s+DIA|^TOTAL\b")

    venda_maq_pat = re.compile(r"(?i)\bVENDA\s+PIX\s+MAQUINETA\b")

    rows = []
    in_block, nf, serie, tem_pix_maq, valor_pix = False, None, None, False, None

    def flush():
        nonlocal in_block, nf, serie, tem_pix_maq, valor_pix
        if in_block and nf and tem_pix_maq and valor_pix:
            try:
                val = float(str(valor_pix).replace(".", "").replace(",", "."))
            except Exception:
                val = None
            if val is not None:
                rows.append({
                    "origem": "NF",
                    "doc": nf,
                    "serie": serie,
                    "tipo_cartao": "Pix Maquineta",
                    "valor_venda": round(val, 2)
                })
        in_block, nf, serie, tem_pix_maq, valor_pix = False, None, None, False, None

    reader = PdfReader(pdf_path)
    for page in reader.pages:
        for ln in [l.strip() for l in (page.extract_text() or "").split("\n") if l.strip()]:
            if page_header_pat.search(ln) or page_footer_pat.search(ln):
                continue
            if stop_totais_pat.search(ln):
                flush()
                continue

            m_start = nf_num_start_pat.match(ln)
            m_verbal = nf_verbal_pat.search(ln)
            m_serie = serie_pat.search(ln)

            if m_start or m_verbal:
                flush()
                if m_start:
                    nf = m_start.group(1)
                    serie = m_start.group(2) if (m_start.lastindex and m_start.group(2)) else None
                else:
                    nf = m_verbal.group(1)
                    serie = None
                in_block = True
                continue

            if in_block and serie is None and m_serie:
                serie = m_serie.group(1)

            if not in_block:
                continue

            if venda_maq_pat.search(ln):
                tem_pix_maq = True
                vals = money_pat.findall(ln)
                if vals:
                    valor_pix = vals[-1]
                continue

    flush()
    return pd.DataFrame(rows)



def parse_recibos_pix_maq_pdf(pdf_path: str) -> pd.DataFrame:
    """
    Lê o RELATÓRIO DE RECIBOS (PDF) e extrai SOMENTE blocos:
      - 'DEP. SAFRAPAY PIX' (aceita 'DEP SAFRAPAY PIX')
      - 'DEP. GETNET PIX'  (aceita 'DEP GETNET PIX')
    Regra de valor:
      - Se houver linhas DPP no bloco, SOMA a coluna DEPOSITO das DPP.
      - Se não houver DEPOSITO (>0), usa o 'TOTAL : <valor>' do cabeçalho.
    Retorna DataFrame:
      ['origem','doc','tipo_cartao','valor_venda'] (Pix Maquineta).
    """
    import re
    import pandas as pd
    from PyPDF2 import PdfReader

    # Marcadores do cabeçalho do bloco (tolerantes a 'DEP.' opcional)
    SAFRA_PAT = re.compile(r"(?i)\bDEP\.?\s*SAFRAPAY\s*PIX\b")
    GETNET_PAT = re.compile(r"(?i)\bDEP\.?\s*GETNET\s*PIX\b")

    # Evitar capturar outros tipos (QR Code/ITAU etc.)
    EXCLUIR_OUTROS_PAT = re.compile(r"(?i)\bPIX\s*QRCOD\b|\bDEP\.?\s*ITAU\b")

    # DOC (último número longo antes do marcador DEP ... PIX)
    LONGNUM_PAT = re.compile(r"\b\d{6,}\b")

    # TOTAL no cabeçalho (tolerante a ruído '**' etc.)
    MONEY_BRL = r"\d{1,3}(?:\.\d{3})*,\d{2}"
    TOTAL_PAT  = re.compile(r"(?i)\bTOTAL\s*:\s*[^\d-]*(" + MONEY_BRL + r")")

    # DPP (linhas de detalhamento)
    DPP_PAT = re.compile(r"^\s*DPP\b", re.IGNORECASE)
    # Padrão monetário para varrer a linha inteira
    MONEY_PAT = re.compile(MONEY_BRL)

    # Rodapé e fechamento
    PAGE_FOOTER_PAT = re.compile(r"(?i)RUA\s+PALMEIRA|C\.N\.P\.J|Email\s*:")
    STOP_TOTAIS_PAT = re.compile(r"(?i)\bTOTAL\s+DIA\b")

    def _to_float_brl(x: object) -> float:
        s = re.sub(r"[^\d,.\-]", "", str(x))
        if "," in s and "." not in s:
            s = s.replace(",", ".")
        elif "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        try:
            return round(float(s), 2)
        except:
            return 0.0

    rows = []
    reader = PdfReader(pdf_path)

    # Estado do bloco
    in_block = False
    doc_bloco = None
    header_total = 0.0
    deposito_sum = 0.0
    bloco_eh_pix_maq = False  # se o cabeçalho tem SAFRAPAY PIX ou GETNET PIX

    def _flush():
        nonlocal in_block, doc_bloco, header_total, deposito_sum, bloco_eh_pix_maq
        if in_block and bloco_eh_pix_maq and doc_bloco:
            valor = deposito_sum if deposito_sum > 0 else header_total
            if valor > 0:
                rows.append({
                    "origem": "RECIBO",
                    "doc": str(doc_bloco),
                    "tipo_cartao": "Pix Maquineta",
                    "valor_venda": round(valor, 2),
                })
        # reset bloco
        in_block = False
        doc_bloco = None
        header_total = 0.0
        deposito_sum = 0.0
        bloco_eh_pix_maq = False

    for page in reader.pages:
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        for i, ln in enumerate(lines):
            # ignora rodapé
            if PAGE_FOOTER_PAT.search(ln):
                continue

            # encerra em TOTAL DIA
            if STOP_TOTAIS_PAT.search(ln):
                _flush()
                continue

            U = ln.upper()

            # ignorar explicitamente QRCode/ITAU etc.
            if EXCLUIR_OUTROS_PAT.search(U):
                # não estamos em um bloco válido de Pix Maq; se tínhamos um aberto, fecha
                _flush()
                continue

            # Detecta cabeçalho do bloco (SAFRAPAY/GETNET) em qualquer posição
            m_safra = SAFRA_PAT.search(U)
            m_get   = GETNET_PAT.search(U)
            if m_safra or m_get:
                # fecha o anterior e inicia novo
                _flush()
                in_block = True
                bloco_eh_pix_maq = True

                # DOC = último número longo ANTES do marcador
                pos = (m_safra or m_get).start()
                prefix = ln[:pos]
                nums = LONGNUM_PAT.findall(prefix)
                doc_bloco = nums[-1] if nums else None

                # TOTAL do cabeçalho (fallback se não houver depósitos)
                m_tot = TOTAL_PAT.search(ln) or (TOTAL_PAT.search(lines[i+1]) if i + 1 < len(lines) else None)
                header_total = _to_float_brl(m_tot.group(1)) if m_tot else 0.0

                # pronto para ler DPP abaixo
                continue

            # Se estamos dentro de um bloco Pix Maq, some DEPOSITO das DPP
            if in_block and bloco_eh_pix_maq and DPP_PAT.match(ln):
                # Captura todos os valores monetários da DPP
                vals = MONEY_PAT.findall(ln)
                if not vals:
                    continue
                # Heurística: DEPOSITO é a 3ª coluna a partir do fim (… DEPOSITO, ANTECIPADO, DEVCAR)
                deposito = _to_float_brl(vals[-3]) if len(vals) >= 3 else _to_float_brl(vals[-1])
                deposito_sum += deposito if deposito else 0.0

    _flush()
    return pd.DataFrame(rows)


# =========================================================
# 2) PARSER DE PAGAMENTOS (Excel - Extrato de Vendas Pix - Detalhado)
# =========================================================

def parse_pagamentos_pix_maq_excel(xlsx_path: str) -> pd.DataFrame:
    """
    Lê o Excel 'Extrato de Vendas Pix – Detalhado' e retorna um DF com:
    - tipo_pagamento='Pix Maquineta'
    - valor_bruto (R$) + campos auxiliares (banco, id, cv, forma_captura, meio_captura, terminal, dt_venda, hr_venda, status)
    Filtra STATUS = 'Paga'.
    """
    df_raw = pd.read_excel(xlsx_path, engine="openpyxl", sheet_name=0, header=None)
    if df_raw.empty:
        return pd.DataFrame(columns=["tipo_pagamento", "valor_bruto"])

    marker_idx = None
    for i in range(len(df_raw)):
        row_str = " ".join(str(x) for x in df_raw.iloc[i].astype(str).tolist()).strip()
        if re.search(r"(?i)\bExtrato\s+de\s+Vendas\s+Pix\s*-\s*Detalhado\b", row_str):
            marker_idx = i
            break

    if marker_idx is None:
        for i in range(len(df_raw)):
            row = df_raw.iloc[i].astype(str).str.strip().tolist()
            if any(re.search(r"(?i)\bVALOR\s+DA\s+VENDA\b", c) for c in row) and \
               any(re.search(r"(?i)\bDATA/HORA\s+DA\s+VENDA\b", c) for c in row):
                marker_idx = i - 1
                break

    header_row = marker_idx + 1 if marker_idx is not None else 0
    header = df_raw.iloc[header_row].astype(str).str.strip().tolist()
    df = df_raw.iloc[header_row + 1:].copy()
    df.columns = header
    df = df.dropna(how="all")

    colmap = {str(c).strip().upper(): c for c in df.columns}
    def _find(names):
        upper = list(colmap.keys())
        for n in names:
            for u in upper:
                if re.fullmatch(n, u):
                    return colmap[u]
        return None

    col_data_hora = _find([r"DATA/HORA\s+DA\s+VENDA"])
    col_banco     = _find([r"INSTITUIÇÃO\s+BANCÁRIA"])
    col_id        = _find([r"ID/TRANSAÇÃO\s*\(ID\)"])
    col_cv        = _find([r"NÚMERO\s+DO\s+COMPROVANTE\s+DE\s+VENDAS\s*\(CV\)"])
    col_forma     = _find([r"FORMA\s+DE\s+CAPTURA"])
    col_meio      = _find([r"MEIO\s+DE\s+CAPTURA"])
    col_terminal  = _find([r"NÚMERO\s+DO\s+TERMINAL"])
    col_valor     = _find([r"VALOR\s+DA\s+VENDA"])
    col_taxa      = _find([r"VALOR\s+TAXA"])
    col_status    = _find([r"STATUS"])

    if col_valor is None or col_status is None:
        return pd.DataFrame(columns=["tipo_pagamento", "valor_bruto"])

    df = df[df[col_status].astype(str).str.strip().str.upper() == "PAGA"]
    df["valor_bruto"] = pd.to_numeric(df[col_valor], errors="coerce").round(2)

    if col_data_hora:
        dt_col = df[col_data_hora].astype(str)
        df["dt_venda"] = dt_col.str.extract(r"(^\S+)")
        df["hr_venda"] = dt_col.str.extract(r"\s(\S+)$")

    out = pd.DataFrame({
        "tipo_pagamento": "Pix Maquineta",
        "valor_bruto": df["valor_bruto"],
        "banco": df[col_banco] if col_banco else None,
        "id": df[col_id] if col_id else None,
        "cv": df[col_cv] if col_cv else None,
        "forma_captura": df[col_forma] if col_forma else None,
        "meio_captura": df[col_meio] if col_meio else None,
        "terminal": df[col_terminal] if col_terminal else None,
        "dt_venda": df["dt_venda"] if "dt_venda" in df.columns else None,
        "hr_venda": df["hr_venda"] if "hr_venda" in df.columns else None,
        "status": df[col_status],
    })
    out = out[pd.notna(out["valor_bruto"])].reset_index(drop=True)
    return out

# =========================================================
# 3) CONCILIAÇÃO - PIX MAQUINETA (1:1 + multi-venda -> 1 pagamento)
# =========================================================

def conciliar_pix_maq_valores(
    vendas_df: pd.DataFrame,
    pagamentos_df: pd.DataFrame,
    tol: float = 0.01,
    max_items_venda: int = 10,
    debug: bool = False
):
    """
    Concilia VENDAS Pix Maquineta com PAGAMENTOS Pix Maquineta:
    A) 1:1 por valor; B) multi-venda -> 1 pagamento (subset-sum).
    Retorna: comparacao_df, pagamentos_sem_match_df, sumario_df.
    """
    vendas = vendas_df.copy()
    pagamentos = pagamentos_df.copy()

    if "tipo_cartao" not in vendas.columns or "valor_venda" not in vendas.columns:
        raise ValueError("vendas_df deve conter colunas: 'tipo_cartao','valor_venda','origem','doc'")
    if "tipo_pagamento" not in pagamentos.columns or "valor_bruto" not in pagamentos.columns:
        raise ValueError("pagamentos_df deve conter colunas: 'tipo_pagamento','valor_bruto'")

    vendas = vendas[
        vendas["tipo_cartao"].astype(str).str.upper().str.contains("PIX", na=False)
        & vendas["tipo_cartao"].astype(str).str.upper().str.contains("MAQUINETA", na=False)
    ].copy()
    pagamentos = pagamentos[
        pagamentos["tipo_pagamento"].astype(str).str.upper().str.contains("PIX", na=False)
        & pagamentos["tipo_pagamento"].astype(str).str.upper().str.contains("MAQUINETA", na=False)
    ].copy()

    vendas["valor_venda"] = vendas["valor_venda"].astype(float).round(2)
    pagamentos["valor_bruto"] = pagamentos["valor_bruto"].astype(float).round(2)

    lookup = {}
    for i, v in pagamentos["valor_bruto"].items():
        lookup.setdefault(v, []).append(i)

    usados_pag, usados_vendas, rows = set(), set(), []

    # Passo A: 1:1
    for idx_v, s in vendas.iterrows():
        val_venda = float(s["valor_venda"])
        candidatos = lookup.get(round(val_venda, 2), [])
        pay_idx = None
        if candidatos:
            pay_idx = candidatos.pop(0)
        elif tol > 0:
            for v_val, idxs in lookup.items():
                if idxs and abs(v_val - val_venda) <= tol:
                    pay_idx = idxs.pop(0)
                    break

        if pay_idx is not None:
            usados_pag.add(pay_idx)
            usados_vendas.add(idx_v)
            rows.append({
                "origem": s.get("origem", ""),
                "doc": s.get("doc", ""),
                "tipo": "Pix Maquineta",
                "valor_venda": val_venda,
                "valor_pagamento": float(pagamentos.loc[pay_idx, "valor_bruto"]),
                "status": "Conciliado (valor)",
                "diferenca": round(float(pagamentos.loc[pay_idx, "valor_bruto"]) - val_venda, 2),
            })

    # Passo B: multi-venda -> 1 pagamento (subset-sum)
    vendas_livres = [(idx_v, float(vendas.loc[idx_v, "valor_venda"])) for idx_v in vendas.index if idx_v not in usados_vendas]
    vendas_livres.sort(key=lambda x: x[1])
    n_v = len(vendas_livres)

    def encontra_subset_por_total(alvo: float):
        alvo_r = round(alvo, 2)
        for i_v, v in vendas_livres:
            if i_v in usados_vendas:
                continue
            if abs(v - alvo_r) <= tol:
                return [i_v]
        best = None
        def dfs(start, acc_sum, chosen):
            nonlocal best
            if abs(acc_sum - alvo_r) <= tol and chosen:
                best = chosen[:]
                return True
            if len(chosen) >= max_items_venda or acc_sum > alvo_r + tol:
                return False
            prev_val = None
            for pos in range(start, n_v):
                i_v, v = vendas_livres[pos]
                if i_v in usados_vendas:
                    continue
                if prev_val is not None and abs(v - prev_val) <= 1e-9:
                    continue
                prev_val = v
                new_sum = round(acc_sum + v, 2)
                if dfs(pos + 1, new_sum, chosen + [i_v]):
                    return True
            return False
        dfs(0, 0.0, [])
        return best

    for pay_idx in pagamentos.index:
        if pay_idx in usados_pag:
            continue
        alvo = float(pagamentos.loc[pay_idx, "valor_bruto"])
        combo_v = encontra_subset_por_total(alvo)
        if debug:
            print(f"[PIX MAQ] pagamento={pay_idx} alvo={alvo} -> vendas={combo_v}")
        if combo_v:
            usados_pag.add(pay_idx)
            for i_v in combo_v:
                usados_vendas.add(i_v)
            for i_v in combo_v:
                s_v = vendas.loc[i_v]
                rows.append({
                    "origem": s_v.get("origem", ""),
                    "doc": s_v.get("doc", ""),
                    "tipo": "Pix Maquineta",
                    "valor_venda": float(s_v["valor_venda"]),
                    "valor_pagamento": alvo,
                    "status": "Conciliado (multi venda)",
                    "diferenca": round(alvo - float(s_v["valor_venda"]), 2),
                })

    for idx_v, s in vendas.iterrows():
        if idx_v in usados_vendas:
            continue
        rows.append({
            "origem": s.get("origem", ""),
            "doc": s.get("doc", ""),
            "tipo": "Pix Maquineta",
            "valor_venda": float(s["valor_venda"]),
            "valor_pagamento": None,
            "status": "Sem pagamento encontrado",
            "diferenca": None,
        })

    comparacao_df = pd.DataFrame(rows)
    pagamentos_sem_match_df = pagamentos.loc[[i for i in pagamentos.index if i not in usados_pag]].copy()
    sumario_df = (
        comparacao_df.groupby(["origem", "status"])["valor_venda"]
        .agg(["count", "sum"]).reset_index()
    )
    return comparacao_df, pagamentos_sem_match_df, sumario_df

# =========================================================
# 4) JANELA TKINTER - ConciliacaoPixMaquinetaApp
# =========================================================

class ConciliacaoPixMaquinetaApp(tk.Toplevel):
    def __init__(self, master=None):
        super().__init__(master)
        self.title("Conciliação - PIX Maquineta")
        self.configure(bg="#1e1e1e")
        self.geometry("1080x720")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background="#1e1e1e", borderwidth=0)
        style.configure("TNotebook.Tab", background="#2e2e2e", foreground="#ffffff", padding=[10, 5])
        style.map("TNotebook.Tab", background=[("selected", "#00bfff")], foreground=[("selected", "#000000")])
        style.configure("TFrame", background="#1e1e1e")
        style.configure("Treeview", background="#1e1e1e", foreground="#ffffff",
                        fieldbackground="#1e1e1e", rowheight=24)
        style.configure("Treeview.Heading", background="#2e2e2e", foreground="#ffffff")
        style.map("Treeview.Heading", background=[("active", "#00bfff")])

        # Estado
        self.cupom_paths = []
        self.nf_paths = []
        self.recibos_paths = []
        self.pagamentos_path = None

        self.vendas_aggregadas = pd.DataFrame()
        self.pagamentos_df = pd.DataFrame()
        self.comparacao_df = pd.DataFrame()
        self.pagamentos_sem_match = pd.DataFrame()
        self.summary_matches = pd.DataFrame()

        self._create_widgets()

    # ---------- Helpers visuais ----------
    def _apply_brilho(self, botao):
        botao.bind("<Enter>", lambda e: botao.config(bg="#00bfff", fg="#000000"))
        botao.bind("<Leave>", lambda e: botao.config(bg="#2e2e2e", fg="#ffffff"))

    def _fmt_brl(self, v) -> str:
        try:
            n = float(v)
            s = f"{n:,.2f}"
            return s.replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            return "" if pd.isna(v) else str(v)

    def _create_tree(self, parent, columns, headings=None, stretch_last=True):
        frm = tk.Frame(parent, bg="#1e1e1e")
        frm.pack(fill="both", expand=True)
        xscroll = ttk.Scrollbar(frm, orient="horizontal")
        yscroll = ttk.Scrollbar(frm, orient="vertical")
        tree = ttk.Treeview(frm, columns=columns, show="headings",
                            xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        xscroll.config(command=tree.xview)
        yscroll.config(command=tree.yview)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        for c in columns:
            head = (headings[c] if headings and c in headings else c)
            tree.heading(c, text=head)
            tree.column(c, width=140, stretch=True)
        if stretch_last and columns:
            tree.column(columns[-1], width=220, stretch=True)

        tree.tag_configure("ok", background="#133b2a")
        tree.tag_configure("warn", background="#3b1f13")
        return tree

    def _fill_tree(self, tree, df, money_cols=None, status_col=None):
        for i in tree.get_children():
            tree.delete(i)
        if df is None or df.empty:
            tree.insert("", "end", values=("–",) * len(tree["columns"]))
            return
        money_cols = set(money_cols or [])
        cols = list(tree["columns"])
        for _, row in df.iterrows():
            vals = []
            for c in cols:
                v = row.get(c, "")
                v = self._fmt_brl(v) if c in money_cols else ("" if pd.isna(v) else v)
                vals.append(v)
            tags = []
            if status_col:
                st = str(row.get(status_col, "")).lower()
                if st.startswith("sem pagamento"):
                    tags.append("warn")
                elif st.startswith("conciliado"):
                    tags.append("ok")
            tree.insert("", "end", values=vals, tags=tags)

    # ---------- UI ----------
    def _create_widgets(self):
        pad = {"padx": 12, "pady": 8}

        top = tk.Frame(self, bg="#1e1e1e")
        top.pack(fill="x", padx=10, pady=10)

        def make_file_row(row, label, on_select):
            tk.Label(top, text=label, font=("Segoe UI", 10),
                     bg="#1e1e1e", fg="#ffffff").grid(row=row, column=0, sticky="w", **pad)
            btn = tk.Button(top, text="Selecionar...", command=on_select,
                            font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
                            activebackground="#444444", activeforeground="#00bfff",
                            relief="flat", bd=0, padx=10, pady=5)
            btn.grid(row=row, column=1, **pad)
            self._apply_brilho(btn)
            lab = tk.Label(top, text="", font=("Segoe UI", 10),
                           bg="#1e1e1e", fg="#ffffff")
            lab.grid(row=row, column=2, sticky="w", **pad)
            return lab

        self.lbl_cupom = make_file_row(0, "Cupom (PDF):", self.select_cupom)
        self.lbl_nf    = make_file_row(1, "Notas Fiscais (PDF):", self.select_nf)
        self.lbl_rec   = make_file_row(2, "Recibos (PDF):", self.select_recibos)
        self.lbl_pay   = make_file_row(3, "Pagamentos Pix Maquineta (Excel):", self.select_pagamentos)

        btn_bar = tk.Frame(self, bg="#1e1e1e")
        btn_bar.pack(fill="x", padx=10, pady=4)
        btn_run = tk.Button(btn_bar, text="Conciliar", command=self.run_conciliacao,
                            font=("Segoe UI", 12), bg="#2e2e2e", fg="#ffffff",
                            activebackground="#444444", activeforeground="#00bfff",
                            relief="flat", bd=0, padx=12, pady=8)
        btn_run.pack(side="left", padx=6)
        self._apply_brilho(btn_run)

        btn_clear = tk.Button(btn_bar, text="Limpar Seleções", command=self.limpar_selecoes,
                              font=("Segoe UI", 12), bg="#8b0000", fg="#ffffff",
                              activebackground="#E10000", activeforeground="#ffffff",
                              relief="flat", bd=0, padx=12, pady=8)
        btn_clear.pack(side="left", padx=6)

        body = tk.Frame(self, bg="#1e1e1e")
        body.pack(fill="both", expand=True, padx=10, pady=10)
        self.nb = ttk.Notebook(body)
        self.nb.pack(fill="both", expand=True)
        self.tab_sum = tk.Frame(self.nb, bg="#1e1e1e")
        self.tab_comp = tk.Frame(self.nb, bg="#1e1e1e")
        self.tab_pay = tk.Frame(self.nb, bg="#1e1e1e")
        self.nb.add(self.tab_sum, text="Sumário")
        self.nb.add(self.tab_comp, text="Comparação")
        self.nb.add(self.tab_pay, text="Pagamentos sem conciliação")

        # --- Compatibilidade com código antigo: alias do container de resultados
        self.frame_resultados = self.tab_comp

        header_sum = tk.Frame(self.tab_sum, bg="#1e1e1e")
        header_sum.pack(fill="x")
        self.lbl_metric = tk.Label(header_sum, text="Métricas: –",
                                   font=("Segoe UI", 11, "bold"),
                                   bg="#1e1e1e", fg="#ffffff")
        self.lbl_metric.pack(side="left", padx=8, pady=6)

        self.tree_sum = self._create_tree(
            self.tab_sum,
            columns=["origem", "status", "count", "sum"],
            headings={"origem": "Origem", "status": "Status", "count": "Qtde", "sum": "Valor (R$)"}
        )
        self.tree_comp = self._create_tree(
            self.tab_comp,
            columns=["origem", "doc", "tipo", "valor_venda", "valor_pagamento", "status", "diferenca"],
            headings={"origem": "Origem", "doc": "Documento", "tipo": "Tipo",
                      "valor_venda": "Venda (R$)", "valor_pagamento": "Pagamento (R$)",
                      "status": "Status", "diferenca": "Diferença (R$)"},
            stretch_last=True
        )
        self.tree_pay = self._create_tree(
            self.tab_pay,
            columns=["tipo_pagamento", "valor_bruto", "banco", "id", "cv", "forma_captura",
                     "meio_captura", "terminal", "dt_venda", "hr_venda", "status"],
            headings={"tipo_pagamento": "Tipo", "valor_bruto": "Valor (R$)", "banco": "Banco",
                      "id": "ID", "cv": "CV", "forma_captura": "Forma", "meio_captura": "Meio",
                      "terminal": "Terminal", "dt_venda": "Data", "hr_venda": "Hora", "status": "Status"}
        )

    # ---------- Seletores ----------
    def select_cupom(self):
        paths = filedialog.askopenfilenames(title="Selecionar Cupom (PDF)", filetypes=[("PDF", "*.pdf")])
        if paths:
            self.cupom_paths = list(paths)
            self.lbl_cupom.config(text=f"{len(paths)} arquivo(s)")

    def select_nf(self):
        paths = filedialog.askopenfilenames(title="Selecionar Notas Fiscais (PDF)", filetypes=[("PDF", "*.pdf")])
        if paths:
            self.nf_paths = list(paths)
            self.lbl_nf.config(text=f"{len(paths)} arquivo(s)")

    def select_recibos(self):
        paths = filedialog.askopenfilenames(title="Selecionar Recibos (PDF)", filetypes=[("PDF", "*.pdf")])
        if paths:
            self.recibos_paths = list(paths)
            self.lbl_rec.config(text=f"{len(paths)} arquivo(s)")

    def select_pagamentos(self):
        # Use um filtro combinado sem ';' (Tkinter espera espaços): "*.xlsx *.xls"
        path = filedialog.askopenfilename(
            title="Selecionar Pagamentos Pix Maquineta (Excel)",
            filetypes=[("Excel", "*.xlsx *.xls")]
        )
        if path:
            self.pagamentos_path = path
            self.lbl_pay.config(text=re.split(r"[\\/]", path)[-1])

    # ---------- Fluxo principal ----------
    def run_conciliacao(self):
        """Fluxo completo da conciliação Pix Maquineta: extrai, concilia e atualiza UI."""
        import pandas as pd
        try:
            # 1) Vendas
            vendas_frames = []
            for p in self.cupom_paths:
                df = parse_cupom_pix_maq_pdf(p)
                if df is not None and not df.empty:
                    vendas_frames.append(df)
            for p in self.nf_paths:
                df = parse_nf_pix_maq_pdf(p)
                if df is not None and not df.empty:
                    vendas_frames.append(df)
            for p in self.recibos_paths:
                df = parse_recibos_pix_maq_pdf(p)
                if df is not None and not df.empty:
                    vendas_frames.append(df)

            if not vendas_frames:
                messagebox.showwarning("Conciliação", "Nenhuma fonte de vendas selecionada.")
                return

            self.vendas_aggregadas = pd.concat(vendas_frames, ignore_index=True)
            if self.vendas_aggregadas.empty:
                messagebox.showwarning("Conciliação", "Não foi possível extrair vendas dos arquivos selecionados.")
                return

            # 2) Pagamentos (Excel)
            if not self.pagamentos_path:
                messagebox.showwarning("Conciliação", "Selecione o relatório de pagamentos Pix Maquineta (.xlsx / .xls).")
                return

            self.pagamentos_df = parse_pagamentos_pix_maq_excel(self.pagamentos_path)
            if self.pagamentos_df is None or self.pagamentos_df.empty:
                messagebox.showwarning("Conciliação", "Nenhum pagamento 'Paga' encontrado no Excel informado.")

            # 3) Conciliação
            self.comparacao_df, self.pagamentos_sem_match, self.summary_matches = conciliar_pix_maq_valores(
                self.vendas_aggregadas, self.pagamentos_df, tol=0.01, max_items_venda=10, debug=False
            )

            # 4) Métricas
            conc = self.comparacao_df["status"].astype(str).str.startswith("Conciliado").sum()
            nao = (self.comparacao_df["status"] == "Sem pagamento encontrado").sum()
            sobra = len(self.pagamentos_sem_match)
            self.lbl_metric.config(
                text=f"Métricas — Conciliadas: {conc}  Não encontradas: {nao}  Pagamentos sobrando: {sobra}"
            )

            # 5) Tabelas
            df_sum = self.summary_matches.copy()
            if df_sum is not None and not df_sum.empty:
                df_sum.columns = ["origem", "status", "count", "sum"]
                self._fill_tree(self.tree_sum, df_sum, money_cols={"sum"}, status_col="status")
            else:
                self._fill_tree(self.tree_sum, pd.DataFrame())

            df_comp = self.comparacao_df.copy()
            self._fill_tree(self.tree_comp, df_comp, money_cols={"valor_venda", "valor_pagamento", "diferenca"}, status_col="status")

            df_pay = self.pagamentos_sem_match.copy()
            if df_pay is not None and not df_pay.empty:
                cols = list(df_pay.columns)
                if set(cols) != set(self.tree_pay["columns"]):
                    for w in self.tab_pay.winfo_children():
                        w.destroy()
                    self.tree_pay = self._create_tree(
                        self.tab_pay, columns=cols,
                        headings={c: c.replace("_", " ").title() for c in cols}
                    )
                money_cols = {c for c in df_pay.columns if "valor" in c.lower() or "bruto" in c.lower() or "taxa" in c.lower()}
                self._fill_tree(self.tree_pay, df_pay, money_cols=money_cols)
            else:
                self._fill_tree(self.tree_pay, pd.DataFrame())

            messagebox.showinfo(
                "Conciliação",
                f"Concluída.\nConciliadas: {conc}\nNão encontradas: {nao}\nPagamentos sobrando: {sobra}"
            )

        except Exception as e:
            messagebox.showerror("Erro", f"Falha na conciliação:\n{e}")

    def limpar_selecoes(self):
        self.cupom_paths = []
        self.nf_paths = []
        self.recibos_paths = []
        self.pagamentos_path = None
        self.lbl_cupom.config(text="")
        self.lbl_nf.config(text="")
        self.lbl_rec.config(text="")
        self.lbl_pay.config(text="")
        self.vendas_aggregadas = pd.DataFrame()
        self.pagamentos_df = pd.DataFrame()
        self.comparacao_df = pd.DataFrame()
        self.pagamentos_sem_match = pd.DataFrame()
        self.summary_matches = pd.DataFrame()
        self.lbl_metric.config(text="Métricas: –")
        self._fill_tree(self.tree_sum, pd.DataFrame())
        self._fill_tree(self.tree_comp, pd.DataFrame())
        self._fill_tree(self.tree_pay, pd.DataFrame())
