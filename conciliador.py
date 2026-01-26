
import os
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
from PyPDF2 import PdfReader

# --------------------------
# Helpers de parsing e normalização
# --------------------------

def smart_to_float(x: object) -> float | None:
    """Converte valores monetários com formato brasileiro/US para float."""
    s = str(x)
    s = re.sub(r"[^0-9,.\-]", "", s)
    has_comma = "," in s
    has_dot = "." in s
    if has_comma and not has_dot:
        s2 = s.replace(",", ".")
    elif has_dot and not has_comma:
        s2 = s
    elif has_dot and has_comma:
        # decide pelo último separador como decimal
        if s.rfind(",") > s.rfind("."):
            s2 = s.replace(".", "").replace(",", ".")
        else:
            s2 = s.replace(",", "")
    else:
        s2 = s
    try:
        return float(s2)
    except:
        return None

def classify_tipo_text(text: str) -> str | None:
    t = text.lower()
    if re.search(r"debito|débito", t):
        return "Débito"
    if re.search(r"credito|crédito", t):
        return "Crédito"
    return None

# --------------------------
# Parsing: CUPOM FISCAL (PDF)
# --------------------------


def parse_cupom_pdf(pdf_path: str) -> pd.DataFrame:
    """
    Parser de CUPOM (ajustado para quebra de página):
    - Mantém o bloco do cupom (current_cupom/in_block) atravessando rodapés/cabeçalhos de página.
    - 'stop_totais_pat' agora só pega seções de TOTAIS reais, não o rodapé com endereço/email/CNPJ.
    - Ignora cabeçalhos do relatório ("RELATORIO CUPOM FISCAL", "FILIAL :", "PERIODO :", "Emissao :", "Usuario/Horario :").
    """
    import re
    from PyPDF2 import PdfReader
    import pandas as pd

    rows = []
    reader = PdfReader(pdf_path)

    # Cabeçalho do cupom (COO 3–6 dígitos, com/sem NORMAL)
    header_pat = re.compile(
        r"^\s*(\d{3,6})\s+\d{3,6}\s+\d{3}(?:\s+NORMAL\b)?",
        re.IGNORECASE
    )

    # Linha informativa (opcional)
    codpos_pat = re.compile(
        r"^CODPOS\s+AUTORIZACAO\s+DOCUMENTO\s+(SITUACAO|STATUS)$",
        re.IGNORECASE
    )

    # Venda (crédito/débito) na mesma linha com valor
    venda_pat = re.compile(
        r"(?:VINCULADO\s+)?VENDA\s+A\s+CARTAO\s+(?:DE\s+)?(CREDITO|DEBITO)\s+([\d\.,]+)",
        re.IGNORECASE
    )

    # ===== NOVO: separar rodapé/cabeçalho de página do 'totais' real =====
    # Cabeçalhos da página do relatório
    page_header_pat = re.compile(
        r"(?i)^(RELATORIO\s+CUPOM\s+FISCAL|FILIAL\s*:|PERIODO\s*:|Emissao\s*:|Usuario/Horario\s*:)"
    )
    # Rodapé com endereço/email/CNPJ/etc.
    page_footer_pat = re.compile(
        r"(?i)(RUA\s+PALMEIRA|C\.N\.P\.J|Email\s*:)"
    )
    # Totais/encerramentos reais do relatório (resumos)
    stop_totais_pat = re.compile(
        r"(?i)(DESCRICAO\s+TOTAIS|TOTAL\s+GERAL|TOTAIS\s+CUPOM|TOTAL\s+DIA|^TOTAL\b)"
    )

    current_cupom = None
    in_block = False
    seen_tuples = set()

    def smart_to_float(x: object) -> float | None:
        s = str(x)
        s = re.sub(r"[^0-9,\.\-]", "", s)
        has_comma = "," in s
        has_dot = "." in s
        if has_comma and not has_dot:
            s2 = s.replace(",", ".")
        elif has_dot and not has_comma:
            s2 = s
        elif has_dot and has_comma:
            # decide pelo último separador como decimal
            s2 = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        else:
            s2 = s
        try:
            return round(float(s2), 2)
        except:
            return None

    for page in reader.pages:
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        for ln in lines:
            # 0) Ignorar cabeçalho/rodapé de página sem encerrar bloco
            if page_header_pat.search(ln) or page_footer_pat.search(ln):
                # Não mexe em in_block/current_cupom; apenas ignora
                continue

            # 1) Área de totais/encerramento REAL: encerrar bloco
            if stop_totais_pat.search(ln):
                in_block = False
                current_cupom = None
                # zera qualquer estado de transição
                continue

            # 2) Cabeçalho do cupom
            m_head = header_pat.match(ln)
            if m_head:
                current_cupom = m_head.group(1)
                in_block = True
                continue

            # 3) Dentro do bloco, linha CODPOS (apenas informativa)
            if in_block and codpos_pat.match(ln):
                continue

            # 4) Venda dentro do bloco (aceita logo após quebra de página)
            m_venda = venda_pat.search(ln)
            if m_venda and in_block and current_cupom:
                tipo = "Crédito" if m_venda.group(1).upper().startswith("CRED") else "Débito"
                valor = smart_to_float(m_venda.group(2))
                if valor is not None:
                    tup = (current_cupom, tipo, valor)
                    if tup not in seen_tuples:
                        rows.append({
                            "origem": "CUPOM",
                            "doc": current_cupom,
                            "tipo_cartao": tipo,
                            "valor_venda": valor
                        })
                        seen_tuples.add(tup)

    return pd.DataFrame(rows)

# --------------------------
# Parsing: NF (PDF)
# --------------------------



def parse_nf_pdf(pdf_path: str) -> pd.DataFrame:
    import re
    from PyPDF2 import PdfReader
    from decimal import Decimal, ROUND_HALF_UP

    def br2d(s: str) -> Decimal:
        return Decimal(s.replace(".", "").replace(",", "."))

    def d2f(d: Decimal) -> float:
        return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    # ★ SIMPLIFICADO: qualquer linha que COMEÇA com 5–6 dígitos é uma NF
    padrao_nf = re.compile(r"^\s*(\d{5,6})\b")

    # valores monetários
    money_pat = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})")

    # labels
    
    credito_label = re.compile(r"^\s*VENDA\s+A\s+CARTAO\s+DE\s+CREDITO\b", re.IGNORECASE)
    debito_label  = re.compile(r"VENDA\s+A\s+CARTAO\s+DEBITO", re.IGNORECASE)

    def find_val(lines, idx, lookahead=4):
        mm = money_pat.findall(lines[idx])
        if mm:
            return br2d(mm[-1])
        for k in range(idx+1, min(idx+lookahead+1, len(lines))):
            mm = money_pat.findall(lines[k])
            if mm:
                return br2d(mm[-1])
        return None

    rows = []
    last_nota = None
    reader = PdfReader(pdf_path)

    for page_num, page in enumerate(reader.pages):
        raw = page.extract_text() or ""
        lines = [l.strip() for l in raw.split("\n") if l.strip()]

        for idx, ln in enumerate(lines):

            # capturamos a NF da forma mais ampla e segura possível
            m_nf = padrao_nf.match(ln)
            if m_nf:
                last_nota = m_nf.group(1)
                continue

            if not last_nota:
                continue

            # Débito
            if debito_label.search(ln):
                val = find_val(lines, idx)
                if val:
                    rows.append({
                        "origem": "NF",
                        "doc": last_nota,
                        "tipo_cartao": "Débito",
                        "valor_venda": d2f(val),
                        "pagina": page_num
                    })
                continue


            # CRÉDITO - SOMENTE venda normal, exclui devoluções
            if credito_label.search(ln):
                val = find_val(lines, idx)
                if val:
                    rows.append({
                        "origem": "NF",
                        "doc": last_nota,
                        "tipo_cartao": "Crédito",
                        "valor_venda": d2f(val),
                        "pagina": page_num
                    })
                continue


    return pd.DataFrame(rows)

# --------------------------
# Parsing: RECIBOS (PDF)
# --------------------------




def parse_recibos_pdf(pdf_path: str) -> pd.DataFrame:
    """
    Parser 'regra híbrida' para RECIBOS — AJUSTADO p/ considerar JR_CART na soma:
    - Cabeçalho 'CART. CRD./DEB.' (e 'TOTAL :') pode vir ANTES de 'CODPOS AUTORIZACAO DOCUMENTO';
      bufferizamos e aplicamos ao DOCUMENTO correto quando CODPOS aparece.
    - DPP: somamos cartão e acumulamos JR_CART por tipo de bloco:
      CART_DEB, CART_CRED; JR_CART_DEB/JR_CART_CRED; também acumulamos não-cartão:
      DESPESAS, DINH, CHEQUE, DEP_ANTECIPADO.
    - Fechamento do bloco:
      Débito:
        * Se cabeçalho for "misto" OU houver qualquer valor não-cartão,
          → usar SOMA(DPP_DEB + JR_CART_DEB).
        * Senão, se houver TOTAL no cabeçalho → usar o TOTAL.
        * Caso contrário → SOMA(DPP_DEB + JR_CART_DEB).
      Crédito:
        * Se houver TOTAL → usar TOTAL (não subtrai JR_CART).
        * Senão → usar SOMA(DPP_CRED + JR_CART_CRED).
    Retorna DataFrame com: origem='RECIBO', doc=<DOCUMENTO>, tipo_cartao ('Débito'/'Crédito'), valor_venda.
    """
    import re
    from PyPDF2 import PdfReader
    import pandas as pd

    reader = PdfReader(pdf_path)

    # Detectores
    labels_pat = re.compile(r"(?i)\bCODPOS\b.*\bDOCUMENTO\b")
    nums_pat = re.compile(r"\d{3,}")
    cart_hdr  = re.compile(r"(?i)CART\.\s*(CRD|DEB)\.")                              # 'CART. CRD.' ou 'CART. DEB.'
    cart_tot  = re.compile(r"(?i)CART\.\s*(DEB|CRD)\.\s*.*?TOTAL\s*:\s*([\d\.,]+)")  # TOTAL : 999,99
    dpp_a     = re.compile(r"^DPP\s+(\d+)\s+\S+\s+(.+)$")                            # DPP <doc> <serie> <resto>
    dpp_b     = re.compile(r"^DPP(\d+)\b\s*(.+)$")                                   # DPP<doc> <resto>
    money_pat = re.compile(r"\d+(?:\.\d{3})*,\d{2}")

    rows = []

    # Estado por bloco
    in_block = False
    documento = None
    tipo_bloco = None  # 'Crédito'/'Débito'/None

    header_total_deb = None
    header_total_cred = None
    header_text = ""
    header_is_mixed_text = False  # TRUE se cabeçalho menciona DIN./DEP./PIX/CHEQUE

    # Acumuladores
    sum_dpp_deb  = 0.0
    sum_dpp_cred = 0.0
    sum_jr_cart_deb  = 0.0   # AJUSTE: juros do bloco de débito
    sum_jr_cart_cred = 0.0   # AJUSTE: juros do bloco de crédito
    sum_non_card = 0.0       # DESPESAS + DINH + CHEQUE + DEP_ANTECIPADO

    def smart_to_float(x: object) -> float:
        s = str(x)
        s = re.sub(r"[^\d,\.\-]", "", s)
        if "," in s and "." not in s:
            s = s.replace(",", ".")
        elif "." in s and "," in s:
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        try:
            return round(float(s), 2)
        except:
            return 0.0

    def try_doc(lines, i):
        # Busca DOCUMENTO nas próximas 1–3 linhas com números
        for k in range(1, 4):
            j = i + k
            if j < len(lines):
                nums = nums_pat.findall(lines[j])
                if len(nums) >= 3: return nums[2]
                elif len(nums) >= 2: return nums[-1]
                elif len(nums) >= 1: return nums[0]
        return None

    def start_block():
        nonlocal in_block, documento, tipo_bloco
        nonlocal header_total_deb, header_total_cred, header_text, header_is_mixed_text
        nonlocal sum_dpp_deb, sum_dpp_cred, sum_jr_cart_deb, sum_jr_cart_cred, sum_non_card
        flush_block()  # fecha anterior, se existir
        in_block = True
        documento = None
        tipo_bloco = None
        header_total_deb = None
        header_total_cred = None
        header_text = ""
        header_is_mixed_text = False
        sum_dpp_deb = 0.0
        sum_dpp_cred = 0.0
        sum_jr_cart_deb = 0.0     # reset
        sum_jr_cart_cred = 0.0    # reset
        sum_non_card = 0.0

    def flush_block():
        nonlocal in_block, documento, tipo_bloco
        nonlocal header_total_deb, header_total_cred, header_text, header_is_mixed_text
        nonlocal sum_dpp_deb, sum_dpp_cred, sum_jr_cart_deb, sum_jr_cart_cred, sum_non_card, rows

        if not in_block or documento is None:
            return

        # ==== DÉBITO (regra híbrida com JR_CART incluído quando usar DPP) ====
        tem_nao_cartao = (sum_non_card > 0.0)
        if header_total_deb is not None:
            if tem_nao_cartao or header_is_mixed_text:
                # Cabeçalho “misto” → usar SOMA DPP + JR_CART (cartão)
                deb_out = round((sum_dpp_deb or 0.0) + (sum_jr_cart_deb or 0.0), 2)
            else:
                # Só cartão no bloco → confiar no TOTAL do cabeçalho (TOTAL já inclui JR_CART)
                deb_out = round(header_total_deb, 2)
        else:
            # Sem TOTAL → usar SOMA DPP + JR_CART
            deb_out = round((sum_dpp_deb or 0.0) + (sum_jr_cart_deb or 0.0), 2)

        # ==== CRÉDITO (TOTAL direto; senão SOMA DPP + JR_CART) ====
        if header_total_cred is not None:
            cred_out = round(header_total_cred, 2)  # AJUSTE: não subtrai JR_CART
        else:
            cred_out = round((sum_dpp_cred or 0.0) + (sum_jr_cart_cred or 0.0), 2)

        # Emite linhas por tipo (> 0)
        if deb_out > 0:
            rows.append({
                "origem": "RECIBO", "doc": documento,
                "tipo_cartao": "Débito", "valor_venda": deb_out
            })
        if cred_out > 0:
            rows.append({
                "origem": "RECIBO", "doc": documento,
                "tipo_cartao": "Crédito", "valor_venda": cred_out
            })

        # Reset bloco
        in_block = False
        documento = None
        tipo_bloco = None
        header_total_deb = None
        header_total_cred = None
        header_text = ""
        header_is_mixed_text = False
        sum_dpp_deb = 0.0
        sum_dpp_cred = 0.0
        sum_jr_cart_deb = 0.0
        sum_jr_cart_cred = 0.0
        sum_non_card = 0.0

    # === Varredura do PDF ===
    for page in reader.pages:
        raw = page.extract_text() or ""
        lines = [l.strip() for l in raw.split("\n") if l.strip()]

        i = 0
        while i < len(lines):
            ln = lines[i]

            # Cabeçalho de cartão (pode vir antes de CODPOS)
            m_hdr = cart_hdr.search(ln)
            if m_hdr:
                start_block()
                tipo_bloco = "Crédito" if m_hdr.group(1).upper().startswith("CRD") else "Débito"
                header_text = ln
                header_is_mixed_text = bool(re.search(r"\b(DIN\.|DEP\.|PIX|CHEQUE)\b", ln, flags=re.IGNORECASE))
                m_tot = cart_tot.search(ln)
                if m_tot:
                    tipo_tot = m_tot.group(1).upper()
                    val_tot = smart_to_float(m_tot.group(2))
                    if tipo_tot.startswith("DEB"):
                        header_total_deb = round(val_tot or 0.0, 2)
                    else:
                        header_total_cred = round(val_tot or 0.0, 2)
                i += 1
                continue

            # CODPOS AUTORIZACAO DOCUMENTO → atribui DOCUMENTO ao bloco pendente
            if labels_pat.search(ln):
                if not in_block:
                    start_block()
                documento = try_doc(lines, i)
                i += 1
                continue

            # DPP dentro do bloco: somar cartão + acumular JR_CART e NÃO-CARTÃO
            if in_block:
                m = dpp_a.match(ln)
                alt = dpp_b.match(ln) if not m else None
                if m or alt:
                    rest = m.group(2) if m else alt.group(2)
                    doc_dpp = m.group(1) if m else alt.group(1)

                    if documento is None and doc_dpp:
                        documento = doc_dpp

                    amounts = money_pat.findall(rest)
                    n = len(amounts)

                    # [0] RECEBIDO, [1] V_DOC, [2] JR_DOC, [3] JR_CART, [4] DESPESAS,
                    # [5] DINH, [6] CHEQUE, [7] CART_DEB, [8] CART_CRED, [9] DEP_ANTECIPADO, [10] DEVCAR
                    idx_jr   = 3 if n >= 4 else (2 if n == 3 else None)
                    idx_desp = 4 if n > 4 else None
                    idx_dinh = 5 if n > 5 else None
                    idx_chq  = 6 if n > 6 else None
                    idx_deb  = 7 if n > 7 else None
                    idx_cred = 8 if n > 8 else None
                    idx_dep  = 9 if n > 9 else None

                    jr_cart   = smart_to_float(amounts[idx_jr])   if (idx_jr   is not None and idx_jr   < n) else 0.0
                    despesas  = smart_to_float(amounts[idx_desp]) if (idx_desp is not None and idx_desp < n) else 0.0
                    dinh      = smart_to_float(amounts[idx_dinh]) if (idx_dinh is not None and idx_dinh < n) else 0.0
                    cheque    = smart_to_float(amounts[idx_chq])  if (idx_chq  is not None and idx_chq  < n) else 0.0
                    cart_deb  = smart_to_float(amounts[idx_deb])  if (idx_deb  is not None and idx_deb  < n) else 0.0
                    cart_cred = smart_to_float(amounts[idx_cred]) if (idx_cred is not None and idx_cred < n) else 0.0
                    depo      = smart_to_float(amounts[idx_dep])  if (idx_dep  is not None and idx_dep  < n) else 0.0

                    sum_dpp_deb  += cart_deb or 0.0
                    sum_dpp_cred += cart_cred or 0.0

                    # AJUSTE: juros entram na soma do tipo correspondente ao bloco
                    if tipo_bloco == "Crédito":
                        sum_jr_cart_cred += jr_cart or 0.0
                    elif tipo_bloco == "Débito":
                        sum_jr_cart_deb  += jr_cart or 0.0

                    sum_non_card += (despesas or 0.0) + (dinh or 0.0) + (cheque or 0.0) + (depo or 0.0)
                    i += 1
                    continue

            i += 1

        flush_block()

    return pd.DataFrame(rows)


# --------------------------
# Pagamentos (Excel CSV)
# --------------------------

def load_pagamentos(path: str) -> pd.DataFrame:
    """
    Se Excel ANALITICO: tenta ler colunas 'Cartões' e 'Descrição do Lançamento' e 'Valor Bruto'.
    Se CSV: aceita já padronizado com colunas ['tipo_cartao','valor_bruto'].
    Filtra cartão (Crédito/Débito) e exclui negativos.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in [".xlsx", ".xls"]:
        df = pd.read_excel(path, engine="openpyxl", sheet_name="ANALITICO", header=7)
        df = df.dropna(how="all")
        txt_cols = [c for c in ["Cartões", "Descrição do Lançamento"] if c in df.columns]
        tipos = df.apply(lambda r: classify_tipo_text(" ".join([str(r[c]) for c in txt_cols if c in df.columns and pd.notna(r[c])])), axis=1)
        valores = df["Valor Bruto"].apply(smart_to_float)
        out = pd.DataFrame({"tipo_cartao": tipos, "valor_bruto": valores})
    else:
        # CSV já padronizado
        out = pd.read_csv(path)
        if not set(["tipo_cartao", "valor_bruto"]).issubset(set(out.columns)):
            raise ValueError("CSV de pagamentos deve ter colunas: tipo_cartao, valor_bruto")

    out = out[out["tipo_cartao"].isin(["Crédito", "Débito"])]
    out = out[out["valor_bruto"].notna() & (out["valor_bruto"] >= 0)]
    out["valor_bruto"] = out["valor_bruto"].round(2)
    return out

# --------------------------
# Conciliação por valor + condição
# --------------------------


# === Conciliação com múltiplos pagamentos (2-sum / 3-sum) ===

def conciliar_vendas_pagamentos(
    vendas_df: pd.DataFrame,
    pagamentos_df: pd.DataFrame,
    max_split: int = 3,  # até 3 pagamentos por venda
    tol: float = 0.01    # tolerância para diferenças de centavos
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Etapas:
    A) 1:1 exato por (tipo_cartao, valor)
    B) Combinações (2-sum / 3-sum) do mesmo tipo para vendas restantes  -> multi-pagamento por venda
    C) NOVO: Combinações (2-sum / 3-sum) de VENDAS do mesmo tipo para pagamentos restantes -> multi-venda por pagamento

    Retorna: comparacao_df, pagamentos_sem_match_df, summary_matches_df
    """
    pagamentos_df = pagamentos_df.copy()
    pagamentos_df["valor_bruto"] = pagamentos_df["valor_bruto"].round(2)

    # índice -> (tipo, valor) para acesso rápido
    idx_tipo_val = {
        i: (pagamentos_df.loc[i, "tipo_cartao"], float(pagamentos_df.loc[i, "valor_bruto"]))
        for i in pagamentos_df.index
    }

    # listas de pagamentos disponíveis por tipo
    disponiveis_por_tipo = {
        "Crédito": [(i, v) for i, (t, v) in idx_tipo_val.items() if t == "Crédito"],
        "Débito":  [(i, v) for i, (t, v) in idx_tipo_val.items() if t == "Débito"]
    }

    # lookup 1:1 por tipo/valor
    lookup_1a1 = {"Crédito": {}, "Débito": {}}
    for tipo, lst in disponiveis_por_tipo.items():
        for i, v in lst:
            lookup_1a1[tipo].setdefault(v, []).append(i)

    rows = []
    usados_pags = set()   # pagamentos já consumidos
    usados_vendas = set() # índices de vendas já conciliadas
    vendas_sem_match = [] # lista de (row_series, tipo, valor, idx)

    # --- Passo A: 1:1 exato ---
    for idx_v, s in vendas_df.iterrows():
        tipo = s["tipo_cartao"]
        valor_venda = round(float(s["valor_venda"]), 2)
        lst = lookup_1a1.get(tipo, {}).get(valor_venda, [])
        if lst:
            pay_idx = lst.pop(0)
            usados_pags.add(pay_idx)
            usados_vendas.add(idx_v)
            rows.append({
                "origem": s["origem"], "doc": s["doc"], "tipo_cartao": tipo,
                "valor_venda": valor_venda, "valor_pagamento": valor_venda,
                "status": "Conciliado (valor + condição)",
                "diferenca": 0.00,
                "pagamentos_usados": str(pay_idx),
                "qtd_pagamentos": 1
            })
        else:
            vendas_sem_match.append((s, tipo, valor_venda, idx_v))

    # utilitário original: busca combinação de 2 ou 3 pagamentos do mesmo tipo
    def encontra_combo_pags(tipo: str, alvo: float, max_k: int):
        candidatos = [(i, v) for (i, v) in disponiveis_por_tipo.get(tipo, [])
                      if i not in usados_pags and v <= alvo + tol]
        candidatos.sort(key=lambda x: x[1])

        # 2-sum
        seen = {}
        for i, v in candidatos:
            need = round(alvo - v, 2)
            if need < -tol:
                continue
            if need in seen:
                j = seen[need]
                if j != i:
                    return [i, j]
            if v not in seen:
                seen[v] = i

        # 3-sum
        if max_k >= 3:
            n = len(candidatos)
            for a in range(n):
                i_a, v_a = candidatos[a]
                rem = round(alvo - v_a, 2)
                if rem < -tol:
                    continue
                seen2 = {}
                for b in range(n):
                    if b == a:
                        continue
                    i_b, v_b = candidatos[b]
                    need = round(rem - v_b, 2)
                    if need < -tol:
                        continue
                    if need in seen2:
                        i_c = seen2[need]
                        if i_c != i_a and i_c != i_b:
                            return [i_a, i_b, i_c]
                    if v_b not in seen2:
                        seen2[v_b] = i_b
        return None

    # --- Passo B: multi-pagamento para uma venda ---
    for s, tipo, valor_venda, idx_v in vendas_sem_match:
        combo = encontra_combo_pags(tipo, valor_venda, max_split)
        if combo:
            soma = round(sum(idx_tipo_val[i][1] for i in combo), 2)
            for i in combo:
                usados_pags.add(i)
            usados_vendas.add(idx_v)
            rows.append({
                "origem": s["origem"], "doc": s["doc"], "tipo_cartao": tipo,
                "valor_venda": valor_venda, "valor_pagamento": soma,
                "status": "Conciliado (multi pagamento)",
                "diferenca": round(soma - valor_venda, 2),
                "pagamentos_usados": "\n".join(map(str, combo)),
                "qtd_pagamentos": len(combo)
            })

    # >>> NOVO: Passo C — multi-venda para um pagamento <<<
    # Preparamos as vendas livres (não usadas) por tipo
    vendas_livres_por_tipo = {"Crédito": [], "Débito": []}
    for idx_v, s in vendas_df.iterrows():
        if idx_v in usados_vendas:
            continue
        tipo = s["tipo_cartao"]
        val  = round(float(s["valor_venda"]), 2)
        vendas_livres_por_tipo[tipo].append((idx_v, s, val))

    def encontra_combo_vendas(tipo: str, alvo: float, max_k: int):
        candidatos = [(idx_v, val) for (idx_v, s, val) in vendas_livres_por_tipo.get(tipo, [])
                      if idx_v not in usados_vendas and val <= alvo + tol]
        candidatos.sort(key=lambda x: x[1])

        # 2-sum
        seen = {}
        for i_v, v in candidatos:
            need = round(alvo - v, 2)
            if need < -tol:
                continue
            if need in seen:
                j_v = seen[need]
                if j_v != i_v:
                    return [i_v, j_v]
            if v not in seen:
                seen[v] = i_v

        # 3-sum
        if max_k >= 3:
            n = len(candidatos)
            for a in range(n):
                i_a, v_a = candidatos[a]
                rem = round(alvo - v_a, 2)
                if rem < -tol:
                    continue
                seen2 = {}
                for b in range(n):
                    if b == a:
                        continue
                    i_b, v_b = candidatos[b]
                    need = round(rem - v_b, 2)
                    if need < -tol:
                        continue
                    if need in seen2:
                        i_c = seen2[need]
                        if i_c != i_a and i_c != i_b:
                            return [i_a, i_b, i_c]
                    if v_b not in seen2:
                        seen2[v_b] = i_b
        return None
    



    # >>> PASSO D — multi-venda + multi-pagamento (COMPLETO) <<<
    # Pré-requisito: 'vendas_livres_por_tipo' foi definido no Passo C.
    # Se por algum motivo não estiver no escopo, reconstrua:
    # try:
    #     vendas_livres_por_tipo
    # except NameError:
    #     vendas_livres_por_tipo = {"Crédito": [], "Débito": []}
    #     for idx_v, s in vendas_df.iterrows():
    #         if idx_v in usados_vendas: 
    #             continue
    #         t = s["tipo_cartao"]; val = round(float(s["valor_venda"]), 2)
    #         vendas_livres_por_tipo[t].append((idx_v, s, val))

    # Lista de pagamentos livres por tipo
    pags_livres_por_tipo = {"Crédito": [], "Débito": []}
    for pay_idx in pagamentos_df.index:
        if pay_idx in usados_pags:
            continue
        t, v = idx_tipo_val[pay_idx]
        pags_livres_por_tipo[t].append((pay_idx, round(float(v), 2)))

    # Configuração: quantidade máxima de vendas em um grupo (subset) que casa com o total dos pagamentos
    max_items_venda = 10  # ajuste se necessário. 10 cobre bem casos com 5+ recibos.

    def encontra_combo_vendas_exato_por_total(tipo: str, alvo: float, max_items: int):
        """
        Procura um subset de vendas do 'tipo' cuja soma == alvo (com tolerância 'tol').
        Aceita de 1 até 'max_items' itens. Retorna lista de índices de vendas ou None.
        """
        # candidatos livres e relevantes (<= alvo + tol)
        cand = [(idx_v, val) for (idx_v, s, val) in vendas_livres_por_tipo.get(tipo, [])
                if (idx_v not in usados_vendas) and (val <= round(alvo + tol, 2))]
        if not cand:
            return None

        # tenta 1:1 exato antes (caso trivial)
        alvo_r = round(alvo, 2)
        for i_v, v in cand:
            if abs(v - alvo_r) <= tol:
                return [i_v]

        # ordena crescente para facilitar poda e reduzir explosão combinatória
        cand.sort(key=lambda x: x[1])
        n = len(cand)

        # backtracking com poda
        best = None

        def dfs(start, acc_sum, chosen):
            nonlocal best
            # sucesso?
            if abs(acc_sum - alvo_r) <= tol and chosen:
                best = chosen[:]
                return True
            # podas por tamanho e por excesso
            if len(chosen) >= max_items or acc_sum > alvo_r + tol:
                return False

            prev_val = None
            for pos in range(start, n):
                i_v, v = cand[pos]
                if i_v in usados_vendas:
                    continue
                # evita percorrer valores iguais na mesma profundidade (reduz duplicatas)
                if prev_val is not None and abs(v - prev_val) <= 1e-9:
                    continue
                prev_val = v

                new_sum = round(acc_sum + v, 2)
                if dfs(pos + 1, new_sum, chosen + [i_v]):
                    return True
            return False

        dfs(0, 0.0, [])
        return best

    def tenta_conciliar_pagamentos_em_vendas(tipo: str, pay_indices: list):
        """
        Recebe 1, 2 ou 3 índices de pagamentos (mesmo tipo) e
        tenta achar um subset de vendas cuja soma == soma desses pagamentos.
        Se conciliar, marca usados e emite linhas em 'rows'. Retorna True/False.
        """
        total_p = round(sum(idx_tipo_val[i][1] for i in pay_indices), 2)
        combo_v = encontra_combo_vendas_exato_por_total(tipo, total_p, max_items_venda)
        if not combo_v:
            return False

        # marca como usados
        for i in pay_indices:
            usados_pags.add(i)
        for i_v in combo_v:
            usados_vendas.add(i_v)

        # emite uma linha por venda do combo, apontando para TODOS os pagamentos do grupo
        total_grp = total_p
        for i_v in combo_v:
            s_v = vendas_df.loc[i_v]
            rows.append({
                "origem": s_v["origem"],
                "doc": s_v["doc"],
                "tipo_cartao": tipo,
                "valor_venda": round(float(s_v["valor_venda"]), 2),
                "valor_pagamento": total_grp,
                "status": "Conciliado (multi venda + multi pagamento)",
                "diferenca": round(total_grp - float(s_v["valor_venda"]), 2),
                "pagamentos_usados": "\n".join(map(str, pay_indices)),
                "qtd_pagamentos": len(pay_indices),
            })
        return True

    # Varre por tipo e tenta 1P->nV, depois 2P->nV, depois 3P->nV (limitado por max_split)
    for tipo in ("Crédito", "Débito"):
        # helper para obter índices de pagamentos livres do tipo (vai mudando conforme conciliamos)
        def pagamentos_livres_do_tipo():
            return [i for (i, _) in pags_livres_por_tipo.get(tipo, []) if i not in usados_pags]

        # 1 pagamento -> subset de vendas
        for i in pagamentos_livres_do_tipo():
            tenta_conciliar_pagamentos_em_vendas(tipo, [i])

        # 2 pagamentos -> subset de vendas
        livres = pagamentos_livres_do_tipo()
        m = len(livres)
        for a in range(m):
            i_a = livres[a]
            if i_a in usados_pags:
                continue
            for b in range(a + 1, m):
                i_b = livres[b]
                if i_b in usados_pags:
                    continue
                tenta_conciliar_pagamentos_em_vendas(tipo, [i_a, i_b])

        # 3 pagamentos -> subset de vendas (se permitido pelo max_split)
        if max_split >= 3:
            livres = pagamentos_livres_do_tipo()
            m = len(livres)
            for a in range(m):
                i_a = livres[a]
                if i_a in usados_pags:
                    continue
                for b in range(a + 1, m):
                    i_b = livres[b]
                    if i_b in usados_pags:
                        continue
                    for c in range(b + 1, m):
                        i_c = livres[c]
                        if i_c in usados_pags:
                            continue
                        tenta_conciliar_pagamentos_em_vendas(tipo, [i_a, i_b, i_c])



    # Percorre pagamentos que sobraram e tenta formar combos de vendas
    for pay_idx in pagamentos_df.index:
        if pay_idx in usados_pags:
            continue
        tipo_p, val_p = idx_tipo_val[pay_idx]
        combo_v = encontra_combo_vendas(tipo_p, val_p, max_split)
        if combo_v:
            soma_v = round(sum(vendas_df.loc[i_v, "valor_venda"] for i_v in combo_v), 2)
            # marca pagamento como usado
            usados_pags.add(pay_idx)
            # cria uma linha de conciliação para CADA venda do combo, apontando para o MESMO pagamento
            for i_v in combo_v:
                s_v = vendas_df.loc[i_v]
                usados_vendas.add(i_v)
                rows.append({
                    "origem": s_v["origem"], "doc": s_v["doc"], "tipo_cartao": tipo_p,
                    "valor_venda": round(float(s_v["valor_venda"]), 2), "valor_pagamento": val_p,
                    "status": "Conciliado (multi venda)",
                    "diferenca": round(val_p - float(s_v["valor_venda"]), 2),
                    "pagamentos_usados": str(pay_idx),
                    "qtd_pagamentos": 1  # um pagamento cobre várias vendas; aqui é por linha de venda
                })

    # Vendas que continuaram sem match (nem A/B/C)
    for idx_v, s in vendas_df.iterrows():
        if idx_v in usados_vendas:
            continue
        rows.append({
            "origem": s.get("origem", ""), "doc": s.get("doc", ""), "tipo_cartao": s.get("tipo_cartao", ""),
            "valor_venda": round(float(s.get("valor_venda", 0)), 2), "valor_pagamento": None,
            "status": "Sem pagamento encontrado",
            "diferenca": None, "pagamentos_usados": "", "qtd_pagamentos": 0
        })

    comparacao = pd.DataFrame(rows)

    # pagamentos não utilizados
    pagamentos_sem_match = pagamentos_df.loc[[i for i in pagamentos_df.index if i not in usados_pags]].copy()

    # sumário
    summary_matches = (comparacao
        .groupby(["origem", "tipo_cartao", "status"])["valor_venda"]
        .agg(["count", "sum"])
        .reset_index())
    return comparacao, pagamentos_sem_match, summary_matches



# --------------------------
# Exportar Excel final
# --------------------------

def export_excel_final(dest_path: str, comparacao: pd.DataFrame, vendas_agg: pd.DataFrame, pagamentos: pd.DataFrame,
                       pagamentos_sem_match: pd.DataFrame, summary_matches: pd.DataFrame):
    with pd.ExcelWriter(dest_path, engine="openpyxl") as w:
        comparacao.to_excel(w, index=False, sheet_name="comparacao")
        vendas_agg.to_excel(w, index=False, sheet_name="vendas_aggregadas")
        pagamentos.to_excel(w, index=False, sheet_name="pagamentos_base")
        summary_matches.to_excel(w, index=False, sheet_name="sumario_matches")
        if not pagamentos_sem_match.empty:
            pagamentos_sem_match.to_excel(w, index=False, sheet_name="pagamentos_sem_match")

# --------------------------
# Tkinter GUI
# --------------------------


import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import re

# >>> mantém seus imports e funções existentes (parse_* e conciliar_*)

class ConciliacaoApp(tk.Toplevel):
    def __init__(self, master=None):
        super().__init__(master)


        # ===== ESTILO DARK PARA NOTEBOOK / TREEVIEW =====
        style = ttk.Style(self)

        # habilita tema para permitir customização completa
        style.theme_use("clam")

        # Notebook (as abas)
        style.configure("TNotebook",
            background="#1e1e1e",
            borderwidth=0
        )
        style.configure("TNotebook.Tab",
            background="#2e2e2e",
            foreground="#ffffff",
            padding=[10, 5]
        )
        style.map("TNotebook.Tab",
            background=[("selected", "#00bfff")],
            foreground=[("selected", "#000000")]
        )

        # Corpo interno do Notebook
        style.configure("TFrame", background="#1e1e1e")

        # Treeview fundo escuro
        style.configure("Treeview",
            background="#1e1e1e",
            foreground="#ffffff",
            fieldbackground="#1e1e1e",
            rowheight=24,
        )
        style.configure("Treeview.Heading",
            background="#2e2e2e",
            foreground="#ffffff"
        )
        style.map("Treeview.Heading",
            background=[("active", "#00bfff")]
        )

        # Scrollbars escuros
        style.configure("Vertical.TScrollbar", background="#1e1e1e")
        style.configure("Horizontal.TScrollbar", background="#1e1e1e")

        # Tema/estilo no padrão prisma.py
        self.title("Conciliação Cartões")
        self.configure(bg="#1e1e1e")
        self.geometry("1080x720")

        # Arquivos selecionados
        self.cupom_paths = []
        self.nf_paths = []
        self.recibos_paths = []
        self.pagamentos_path = None

        # Dados finais
        self.vendas_aggregadas = pd.DataFrame()
        self.pagamentos_df = pd.DataFrame()
        self.comparacao_df = pd.DataFrame()
        self.pagamentos_sem_match = pd.DataFrame()
        self.summary_matches = pd.DataFrame()

    
        self._create_widgets()

    # ---------- Helpers de UI / formatação ----------
    def _apply_brilho(self, botao):
        botao.bind("<Enter>", lambda e: botao.config(bg="#00bfff", fg="#ffffff"))
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

        # Cabeçalhos
        for c in columns:
            head = (headings[c] if headings and c in headings else c)
            tree.heading(c, text=head)
            tree.column(c, width=140, stretch=True)
        if stretch_last and columns:
            tree.column(columns[-1], width=220, stretch=True)

        # tags de cor por status
        tree.tag_configure("ok", background="#133b2a")     # verde escuro
        tree.tag_configure("warn", background="#3b1f13")   # laranja escuro

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

    # ---------- UI principal ----------
    def _create_widgets(self):
        pad = {"padx": 12, "pady": 8}

        # Linha de seleção de arquivos (dark style)
        top = tk.Frame(self, bg="#1e1e1e")
        top.pack(fill="x", padx=10, pady=10)

        def make_file_row(row, label, on_select):
            tk.Label(top, text=label, font=("Segoe UI", 10), bg="#1e1e1e", fg="#ffffff").grid(row=row, column=0, sticky="w", **pad)
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

        self.lbl_cupom = make_file_row(0, "Cupom Fiscal:", self.select_cupom)
        self.lbl_nf    = make_file_row(1, "Nota Fiscal:",   self.select_nf)
        self.lbl_rec   = make_file_row(2, "Recibo:",       self.select_recibos)
        self.lbl_pay   = make_file_row(3, "Pagamentos", self.select_pagamentos)

        # Botões de ação (sem Exportar Excel)
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

        # Notebook de dashboards (substitui Log/Status)
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

        # Métricas rápidas no topo do Sumário
        header_sum = tk.Frame(self.tab_sum, bg="#1e1e1e")
        header_sum.pack(fill="x")
        self.lbl_metric = tk.Label(header_sum, text="Métricas: –",
                                   font=("Segoe UI", 11, "bold"),
                                   bg="#1e1e1e", fg="#ffffff")
        self.lbl_metric.pack(side="left", padx=8, pady=6)

        # Tabelas
        self.tree_sum = self._create_tree(
            self.tab_sum,
            columns=["origem", "tipo_cartao", "status", "count", "sum"],
            headings={"origem": "Origem", "tipo_cartao": "Tipo", "status": "Status",
                      "count": "Qtde", "sum": "Valor (R$)"}
        )
        self.tree_comp = self._create_tree(
            self.tab_comp,
            columns=["origem", "doc", "tipo_cartao", "valor_venda", "valor_pagamento", "status", "diferenca"],
            headings={"origem": "Origem", "doc": "Documento", "tipo_cartao": "Tipo",
                      "valor_venda": "Venda (R$)", "valor_pagamento": "Pagamento (R$)",
                      "status": "Status", "diferenca": "Diferença (R$)"},
            stretch_last=True
        )
        # Inicial: cria com colunas mínimas; na atualização a gente recria se vierem outras
        self.tree_pay = self._create_tree(
            self.tab_pay,
            columns=["tipo_cartao", "valor_bruto"],
            headings={"tipo_cartao": "Tipo", "valor_bruto": "Valor (R$)"}
        )

    # ---------- Seletores ----------
    def select_cupom(self):
        paths = filedialog.askopenfilenames(title="Selecionar Cupom Fiscal (PDF)", filetypes=[("PDF", "*.pdf")])
        if paths:
            self.cupom_paths = list(paths)
            self.lbl_cupom.config(text=f"{len(paths)} arquivo(s) selecionado(s)")

    def select_nf(self):
        paths = filedialog.askopenfilenames(title="Selecionar Nota Fiscal (PDF)", filetypes=[("PDF", "*.pdf")])
        if paths:
            self.nf_paths = list(paths)
            self.lbl_nf.config(text=f"{len(paths)} arquivo(s) selecionado(s)")

    def select_recibos(self):
        paths = filedialog.askopenfilenames(title="Selecionar Recibos (PDF)", filetypes=[("PDF", "*.pdf")])
        if paths:
            self.recibos_paths = list(paths)
            self.lbl_rec.config(text=f"{len(paths)} arquivo(s) selecionado(s)")

    def select_pagamentos(self):
        path = filedialog.askopenfilename(title="Selecionar Pagamentos (Excel/CSV)",
                                          filetypes=[("Excel/CSV", "*.xlsx;*.xls;*.csv"),
                                                     ("Excel", "*.xlsx;*.xls"),
                                                     ("CSV", "*.csv")])
        if path:
            self.pagamentos_path = path
            self.lbl_pay.config(text=re.split(r"[\\/]", path)[-1])

    # ---------- Fluxo principal ----------
    def run_conciliacao(self):
        try:
            # 1) Extrair vendas
            vendas_frames = []
            for p in self.cupom_paths:
                df = parse_cupom_pdf(p)
                vendas_frames.append(df)
            for p in self.nf_paths:
                df = parse_nf_pdf(p)
                vendas_frames.append(df)
            for p in self.recibos_paths:
                df = parse_recibos_pdf(p)
                vendas_frames.append(df)
            if not vendas_frames:
                messagebox.showwarning("Conciliação", "Nenhuma fonte de vendas selecionada.")
                return
            self.vendas_aggregadas = pd.concat(vendas_frames, ignore_index=True)

            # 2) Pagamentos
            if not self.pagamentos_path:
                messagebox.showwarning("Conciliação", "Selecione o arquivo de pagamentos.")
                return
            self.pagamentos_df = load_pagamentos(self.pagamentos_path)

            # 3) Conciliação

            self.comparacao_df, self.pagamentos_sem_match, self.summary_matches = conciliar_vendas_pagamentos(
                self.vendas_aggregadas, self.pagamentos_df, max_split=3, tol=0.01
            )

            conc = (self.comparacao_df["status"] == "Conciliado (valor + condição)").sum()
            nao  = (self.comparacao_df["status"] == "Sem pagamento encontrado").sum()
            sobra = len(self.pagamentos_sem_match)

            # 4) Atualiza métricas
            self.lbl_metric.config(
                text=f"Métricas — Conciliadas: {conc} | Não encontradas: {nao} | Pagamentos sobrando: {sobra}"
            )

            # Sumário
            df_sum = self.summary_matches.copy()
            if not df_sum.empty:
                df_sum.columns = ["origem", "tipo_cartao", "status", "count", "sum"]
            self._fill_tree(self.tree_sum, df_sum, money_cols={"sum"}, status_col="status")

            # Comparação
            df_comp = self.comparacao_df.copy()
            self._fill_tree(self.tree_comp, df_comp,
                            money_cols={"valor_venda", "valor_pagamento", "diferenca"},
                            status_col="status")

            # Pagamentos sem conciliação (recria Treeview se as colunas forem diferentes)
            df_pay = self.pagamentos_sem_match.copy()
            if not df_pay.empty:
                cols = list(df_pay.columns)
                if set(cols) != set(self.tree_pay["columns"]):
                    # destrói e recria com novas colunas
                    for w in self.tab_pay.winfo_children():
                        w.destroy()
                    self.tree_pay = self._create_tree(
                        self.tab_pay, columns=cols,
                        headings={c: c.replace("_", " ").title() for c in cols}
                    )
                money_cols = {c for c in df_pay.columns if re.search(r"valor|bruto|total", c, flags=re.I)}
                self._fill_tree(self.tree_pay, df_pay, money_cols=money_cols)
            else:
                self._fill_tree(self.tree_pay, df_pay)

            messagebox.showinfo("Conciliação",
                                f"Concluída.\nConciliadas: {conc}\nNão encontradas: {nao}\nPagamentos sobrando: {sobra}")

        except Exception as e:
            messagebox.showerror("Erro", f"Falha na conciliação:\n{e}")

    

    def limpar_selecoes(self):
        # limpa seleções
        self.cupom_paths = []
        self.nf_paths = []
        self.recibos_paths = []
        self.pagamentos_path = None
        self.lbl_cupom.config(text="")
        self.lbl_nf.config(text="")
        self.lbl_rec.config(text="")
        self.lbl_pay.config(text="")

        # zera dataframes
        self.vendas_aggregadas = pd.DataFrame()
        self.pagamentos_df = pd.DataFrame()
        self.comparacao_df = pd.DataFrame()
        self.pagamentos_sem_match = pd.DataFrame()
        self.summary_matches = pd.DataFrame()

        # limpa UI
        self.lbl_metric.config(text="Métricas: –")
        self._fill_tree(self.tree_sum, pd.DataFrame())
        self._fill_tree(self.tree_comp, pd.DataFrame())
        self._fill_tree(self.tree_pay, pd.DataFrame())

    # === ATUALIZADO: run_conciliacao passa a preencher as abas ===
    def run_conciliacao(self):
        try:
            # 1) Extrair vendas (Cupom, NF, Recibos)
            vendas_frames = []
            for p in self.cupom_paths:
                df = parse_cupom_pdf(p)
                vendas_frames.append(df)
            for p in self.nf_paths:
                df = parse_nf_pdf(p)
                vendas_frames.append(df)
            for p in self.recibos_paths:
                df = parse_recibos_pdf(p)
                vendas_frames.append(df)
            if not vendas_frames:
                messagebox.showwarning("Conciliação", "Nenhuma fonte de vendas selecionada.")
                return
            self.vendas_aggregadas = pd.concat(vendas_frames, ignore_index=True)

            # 2) Pagamentos
            if not self.pagamentos_path:
                messagebox.showwarning("Conciliação", "Selecione o arquivo de pagamentos.")
                return
            self.pagamentos_df = load_pagamentos(self.pagamentos_path)

            # 3) Conciliação
            self.comparacao_df, self.pagamentos_sem_match, self.summary_matches = conciliar_vendas_pagamentos(
                self.vendas_aggregadas, self.pagamentos_df
            )
            conc = (self.comparacao_df["status"] == "Conciliado (valor + condição)").sum()
            nao = (self.comparacao_df["status"] == "Sem pagamento encontrado").sum()
            sobra = len(self.pagamentos_sem_match)

            # 4) Atualiza métricas e tabelas
            self.lbl_metric.config(
                text=f"Métricas — Conciliadas: {conc} | Não encontradas: {nao} | Pagamentos sobrando: {sobra}"
            )

            # Sumário
            df_sum = self.summary_matches.copy()
            if not df_sum.empty:
                # renomeia colunas para bater com Treeview
                df_sum.columns = ["origem", "tipo_cartao", "status", "count", "sum"]
            self._fill_tree(self.tree_sum, df_sum, money_cols={"sum"}, status_col="status")

            # Comparação (formata valores)
            df_comp = self.comparacao_df.copy()
            self._fill_tree(
                self.tree_comp, df_comp,
                money_cols={"valor_venda", "valor_pagamento", "diferenca"},
                status_col="status"
            )

            # Pagamentos sem conciliação
            df_pay = self.pagamentos_sem_match.copy()
            # Se tiver colunas adicionais, recria Treeview com todas as colunas relevantes
            if not df_pay.empty:
                cols = [c for c in df_pay.columns if c not in ("", None)]
                # reconstruir se diferente
                if set(cols) != set(self.tree_pay["columns"]):
                    # recria o tree com colunas novas
                    for w in self.tab_pay.winfo_children():
                        w.destroy()
                    self.tree_pay = self._create_tree(
                        self.tab_pay, columns=cols,
                        headings={c: c.replace("_", " ").title() for c in cols}
                    )
                money_cols = {c for c in df_pay.columns if "valor" in c.lower() or "bruto" in c.lower()}
                self._fill_tree(self.tree_pay, df_pay, money_cols=money_cols)
            else:
                self._fill_tree(self.tree_pay, df_pay)  # mostra "–"

            messagebox.showinfo("Conciliação", f"Conciliação concluída.\nConciliadas: {conc}\nNão encontradas: {nao}\nPagamentos sobrando: {sobra}")

        except Exception as e:
            messagebox.showerror("Erro", f"Falha na conciliação: {e}")


    def exportar_excel(self):
        if self.comparacao_df.empty or self.vendas_aggregadas.empty or self.pagamentos_df.empty:
            messagebox.showwarning("Exportar", "Execute a conciliação antes de exportar.")
            return
        dest = filedialog.asksaveasfilename(
            title="Salvar Excel de Conciliação",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")]
        )
        if not dest:
            return
        try:
            export_excel_final(dest, self.comparacao_df, self.vendas_aggregadas, self.pagamentos_df,
                               self.pagamentos_sem_match, self.summary_matches)
            self.log(f"Excel exportado: {dest}")
            messagebox.showinfo("Exportar", f"Excel exportado com sucesso:\n{dest}")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao exportar: {e}")
            self.log(f"Erro exportar: {e}")

if __name__ == "__main__":
    app = ConciliacaoApp()
    app.mainloop()
