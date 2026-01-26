
import re
import pandas as pd
from PyPDF2 import PdfReader
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import re


def parse_cupom_pix_pdf(pdf_path: str):
    """
    Extrai vendas de PIX (QR Code) a partir do RELATÓRIO CUPOM FISCAL (PDF),
    incluindo apenas linhas 'VENDA PIX' e EXCLUINDO 'VENDA PIX MAQUINETA'.

    Retorna um pandas.DataFrame com colunas:
      - origem: 'CUPOM'
      - doc: COO do cupom (string)
      - tipo_cartao: 'Pix QrCode'
      - valor_venda: float (R$)

    Este parser assume formato similar ao relatório mostrado em CF.pdf,
    com cabeçalho de cupom: '<COO> <IMPR> <SERIE> NORMAL ...' e linhas
    de pagamento contendo 'VENDA PIX <valor>'. (Ex.: 'CPIX ... VENDA PIX 148,50')
    """


    # --- Helpers locais (mantidos dentro da única def) ---
    def _smart_to_float(x: object):
        s = str(x)
        s = re.sub(r"[^\d,.\-]", "", s)
        has_comma = "," in s
        has_dot = "." in s
        if has_comma and not has_dot:
            s2 = s.replace(",", ".")
        elif has_dot and has_comma:
            # usa o último separador como decimal
            s2 = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        else:
            s2 = s
        try:
            return round(float(s2), 2)
        except:
            return None

    # Cabeçalho do cupom: 'COO IMPR SERIE NORMAL ...'
    header_pat = re.compile(
        r"^\s*(\d{3,6})\s+\d{3,6}\s+\d{3}(?:\s+NORMAL\b)?",
        re.IGNORECASE
    )

    # Linhas a ignorar (cabeçalho/rodapé e áreas de totais do relatório)
    page_header_pat = re.compile(r"(?i)RELATORIO\s+CUPOM\s+FISCAL")
    page_footer_pat = re.compile(r"(?i)RUA\s+PALMEIRA|C\.N\.P\.J|Email\.:")
    stop_totais_pat = re.compile(r"(?i)DESCRICAO\s+TOTAIS|TOTAL\s+DIA|^TOTAL\b")

    # Detecta venda PIX (sem maquineta)
    venda_pix_pat = re.compile(r"(?i)\bVENDA\s+PIX\b")
    venda_pix_maq_pat = re.compile(r"(?i)\bVENDA\s+PIX\s+MAQUINETA\b")

    # Valor monetário brasileiro
    money_pat = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")

    rows = []
    seen = set()  # deduplicar (coo, valor)

    reader = PdfReader(pdf_path)
    current_coo = None
    in_block = False

    for page in reader.pages:
        text = page.extract_text() or ""
        # quebra em linhas e limpa espaços extremos
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        for ln in lines:
            # ignora cabeçalho/rodapé de página
            if page_header_pat.search(ln) or page_footer_pat.search(ln):
                continue

            # fim de blocos em áreas de totais/encerramento
            if stop_totais_pat.search(ln):
                current_coo = None
                in_block = False
                continue

            # início de um cupom
            m_head = header_pat.match(ln)
            if m_head:
                current_coo = m_head.group(1)
                in_block = True
                continue

            if not in_block or not current_coo:
                continue

            # dentro do cupom: só 'VENDA PIX' (exclui Pix Maquineta)
            if venda_pix_maq_pat.search(ln):
                continue  # explicitamente excluído

            if venda_pix_pat.search(ln):
                # pega o último valor monetário na linha
                m_vals = money_pat.findall(ln)
                if not m_vals:
                    continue
                val = _smart_to_float(m_vals[-1])
                if val is None:
                    continue

                key = (current_coo, val)
                if key in seen:
                    continue
                seen.add(key)

                rows.append({
                    "origem": "CUPOM",
                    "doc": current_coo,
                    "tipo_cartao": "Pix QrCode",
                    "valor_venda": val
                })

    return pd.DataFrame(rows)




def parse_nf_pix_pdf(pdf_path: str) -> pd.DataFrame:
    """
    Extrai vendas Pix QRCode de Notas Fiscais (PDF) mesmo com cabeçalhos quebrados/variantes:
    - Reconhece início de NF por:
      * linha que começa com 5–6 dígitos (número da NF), opcionalmente seguida de SÉRIE;
      * linhas contendo "NOTA FISCAL", "NF", "Nº"/"NO." com número da NF;
      * linhas com "SÉRIE" em separado.
    - Mantém bloco de NF atravessando páginas e ignora cabeçalho/rodapé do relatório.
    - Marca VENDA PIX normal e exclui VENDA PIX MAQUINETA.
    Retorna DataFrame com colunas:
      ['origem','doc','serie','tipo_cartao','valor_venda'] onde tipo_cartao='Pix QrCode'.
    """
    import re
    from PyPDF2 import PdfReader
    import pandas as pd

    reader = PdfReader(pdf_path)

    # --- Padrões mais amplos ---
    # a) NF começa com 5–6 dígitos (largamente usado nos seus PDFs)
    nf_num_start_pat = re.compile(r"^\s*(\d{5,6})(?:\s+(\d{1,4}))?\b")
    # b) Cabeçalhos verbais: "NOTA FISCAL", "NF", "Nº" / "NO." seguidos de número (5–6 dígitos)
    nf_verbal_pat = re.compile(r"(?i)\b(?:NOTA\s+FISCAL|NF)\b.*?\b(?:N[ºO]\.?\s*)?(\d{5,6})\b")
    # c) Série pode vir separada: "SÉRIE 1" ou "SERIE 1"
    serie_pat = re.compile(r"(?i)\bS[ÉE]RIE\b\s*(\d{1,4})\b")

    # Valor monetário brasileiro
    money_pat = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")

    # Cabeçalho/rodapé genérico de relatório (para ignorar sem fechar bloco)
    page_header_pat = re.compile(r"(?i)RELATORIO|EMISSAO|USUARIO/HORARIO|FILIAL|PERIODO")
    page_footer_pat = re.compile(r"(?i)RUA\s+PALMEIRA|C\.N\.P\.J|Email\s*:")
    # Área de totais/encerramento (fecha bloco)
    stop_totais_pat = re.compile(r"(?i)DESCRICAO\s+TOTAIS|TOTAL\s+GERAL|TOTAL\s+DIA|^TOTAL\b")

    # PIX (normal x maquineta)
    venda_pix_pat = re.compile(r"(?i)\bVENDA\s+PIX\b")
    venda_pix_maq_pat = re.compile(r"(?i)\bVENDA\s+PIX\s+MAQUINETA\b")

    rows = []
    in_block = False
    nf = None
    serie = None
    tem_pix_normal = False
    tem_pix_maquineta = False
    valor_pix = None

    def flush_block():
        nonlocal in_block, nf, serie, tem_pix_normal, tem_pix_maquineta, valor_pix, rows
        if in_block and nf:
            # Emite somente se houve VENDA PIX e não houve MAQUINETA
            if tem_pix_normal and not tem_pix_maquineta and valor_pix:
                valor = float(str(valor_pix).replace(".", "").replace(",", "."))
                rows.append({
                    "origem": "NF",
                    "doc": nf,
                    "serie": serie,
                    "tipo_cartao": "Pix QrCode",
                    "valor_venda": round(valor, 2)
                })
        # Reset
        in_block = False
        nf = None
        serie = None
        tem_pix_normal = False
        tem_pix_maquineta = False
        valor_pix = None

    for page in reader.pages:
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for ln in lines:
            # Ignorar cabeçalho/rodapé de página sem encerrar o bloco
            if page_header_pat.search(ln) or page_footer_pat.search(ln):
                continue

            # Fechar bloco em áreas reais de totais/encerramento
            if stop_totais_pat.search(ln):
                flush_block()
                continue

            # --- Detectar início/continuação de NF ---
            m_start = nf_num_start_pat.match(ln)
            m_verbal = nf_verbal_pat.search(ln)
            m_serie = serie_pat.search(ln)

            if m_start or m_verbal:
                # Fechar bloco anterior e iniciar novo
                flush_block()
                if m_start:
                    nf = m_start.group(1)
                    # se segunda captura existir, é série na mesma linha
                    serie = m_start.group(2) if m_start.lastindex and m_start.group(2) else None
                else:
                    nf = m_verbal.group(1)
                    # série pode aparecer em outra linha; deixamos None por enquanto
                    serie = None
                in_block = True
                # segue para avaliar possíveis VENDA PIX nas próximas linhas
                continue

            # Se estamos num bloco de NF, permitir série em linha separada
            if in_block and serie is None and m_serie:
                serie = m_serie.group(1)
                # continua

            # Fora de bloco → nada a fazer
            if not in_block:
                continue

            # Detectar VENDA PIX (normal) e exclusão de MAQUINETA
            if venda_pix_maq_pat.search(ln):
                tem_pix_maquineta = True
                continue

            if venda_pix_pat.search(ln) and not venda_pix_maq_pat.search(ln):
                tem_pix_normal = True
                vals = money_pat.findall(ln)
                if vals:
                    valor_pix = vals[-1]  # último valor da linha
                continue

    # flush final
    flush_block()
    return pd.DataFrame(rows)



def parse_recibos_pix_pdf(pdf_path: str) -> pd.DataFrame:
    """
    Extrai RECIBOS Pix QrCode olhando SOMENTE linhas que contenham 'DEP. PIX QRCOD'
    e capturando o 'TOTAL : <valor>' da mesma linha. DOC é o último número longo (>=6 dígitos)
    que aparece ANTES do marcador 'DEP. PIX QRCOD'. Ignora 'GETNET PIX' e 'PIX MAQUINETA'.
    Retorna DataFrame com colunas: ['origem','doc','tipo_cartao','valor_venda'].
    """
    import re
    import pandas as pd
    from PyPDF2 import PdfReader

    # Padrões
    PIX_QR_PAT      = re.compile(r"(?i)\bDEP\.\s*PIX\s*QRCOD\b")
    EXCLUIR_PAT     = re.compile(r"(?i)\bGETNET\s+PIX\b|\bPIX\s+MAQUINETA\b")
    MONEY_BRL       = r"\d{1,3}(?:\.\d{3})*,\d{2}"
    TOTAL_PAT       = re.compile(r"(?i)\bTOTAL\s*:\s*[^\d-]*(" + MONEY_BRL + r")")  # tolera '**', espaços etc.
    LONGNUM_PAT     = re.compile(r"\b\d{6,}\b")  # números longos (DOCs, etc.)
    FOOTER_PAT      = re.compile(r"(?i)RUA\s+PALMEIRA|C\.N\.P\.J|Email\s*:")
    STOP_TOTAIS_PAT = re.compile(r"(?i)\bTOTAL\s+DIA\b")

    def _smart_to_float(s: str) -> float:
        s = re.sub(r"[^\d,.\-]", "", str(s))
        if "," in s and "." not in s:
            s = s.replace(",", ".")
        elif "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        try:
            return round(float(s), 2)
        except:
            return 0.0

    rows = []
    seen = set()  # (doc, valor)
    reader = PdfReader(pdf_path)

    for page in reader.pages:
        text = page.extract_text() or ""
        # quebra conservadora: mantém linhas não vazias
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        for ln in lines:
            # ignora rodapé e total dia (não precisamos deles aqui)
            if FOOTER_PAT.search(ln) or STOP_TOTAIS_PAT.search(ln):
                continue

            U = ln.upper()
            if EXCLUIR_PAT.search(U):
                continue
            if not PIX_QR_PAT.search(U):
                continue  # só nos interessa DEP. PIX QRCOD

            # TOTAL : <valor> na própria linha (com ruído tolerado)
            m_tot = TOTAL_PAT.search(ln)
            if not m_tot:
                # sem total, não registra (regra atual olha apenas TOTAL do cabeçalho)
                continue
            valor = _smart_to_float(m_tot.group(1))
            if valor <= 0:
                continue

            # DOC = último número longo antes do marcador 'DEP. PIX QRCOD'
            pix_pos = PIX_QR_PAT.search(U).start()  # posição do início do marcador
            prefix  = ln[:pix_pos]                  # tudo antes de 'DEP. PIX QRCOD'
            nums    = LONGNUM_PAT.findall(prefix)
            doc     = nums[-1] if nums else None

            if not doc:
                # se não achou DOC, ainda podemos registrar, mas melhor evitar linhas sem doc
                # continue  # descomente se quiser ignorar sem DOC
                doc = ""  # registra vazio para análise

            key = (doc, valor)
            if key in seen:
                continue
            seen.add(key)

            rows.append({
                "origem": "RECIBO",
                "doc": doc,
                "tipo_cartao": "Pix QrCode",
                "valor_venda": valor,
            })

    return pd.DataFrame(rows)



def parse_pagamentos_pix_qr_pdf(pdf_path: str):
    """
    Extrai pagamentos Pix QrCode a partir do 'RELATORIO MOVIMENTACAO PIX QRCOD - ITAU' (PDF).
    Retorna um pandas.DataFrame com colunas:
      - tipo_pagamento: 'Pix QrCode'
      - valor_bruto: float (R$)
      - txid: str (quando encontrado)
      - pedido: str (quando encontrado)
      - filial: str (quando encontrado)
      - dt_receb: str (DD/MM/AAAA)
      - hr_receb: str (como presente no relatório, ex.: '8,05')
      - vendedor: str (quando encontrado)

    Observações:
    - Ignora cabeçalhos/rodapés e linha 'TOTAL ...'.
    - Heurísticas leves para localizar TXID/pedido/vendedor.
    """
    import re
    import pandas as pd
    from PyPDF2 import PdfReader

    # --- Helpers ---
    def _smart_to_float(x: object):
        s = str(x)
        s = re.sub(r"[^\d,.\-]", "", s)
        has_comma, has_dot = ("," in s), ("." in s)
        if has_comma and not has_dot:
            s = s.replace(",", ".")
        elif has_comma and has_dot:
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        try:
            return round(float(s), 2)
        except:
            return None

    # Padrões
    page_header_pat = re.compile(r"(?i)RELATORIO\s+MOVIMENTACAO\s+PIX\s+QRCOD|ITAU|EMISSAO|USUARIO|EMPRESA|FILIAL\s*:|PERIODO", re.UNICODE)
    page_footer_pat = re.compile(r"(?i)RUA\s+PALMEIRA|C\.N\.P\.J|Email\.:|Página\s+\d+/\d+", re.UNICODE)
    total_pat        = re.compile(r"(?i)^\s*TOTAL\b")
    money_pat        = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")           # 9.999,99
    date_pat         = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")               # DD/MM/AAAA
    hour_pat         = re.compile(r"\b\d{1,2},\d{2}\b")                   # 8,05 | 10,06 etc.
    txid_pat         = re.compile(r"\b[Rr]?\w{20,}\b")                    # TXID longo (>=20), pode iniciar com 'R'
    pedido_pat       = re.compile(r"\b\d{6,}\b")                          # números longos (ex.: 900293681)
    vendedor_pat     = re.compile(r"\b\d{3,5}\b")                         # códigos curtos (ex.: 4626)
    filial_pat       = re.compile(r"^\s*(\d{1,3})\b")                     # primeiro número curto no início

    rows = []
    reader = PdfReader(pdf_path)

    for page in reader.pages:
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        for ln in lines:
            # Ignorar cabeçalhos/rodapés e linha de TOTAL
            if page_header_pat.search(ln) or page_footer_pat.search(ln) or total_pat.search(ln):
                continue

            # Procurar valor (último valor monetário da linha)
            m_vals = money_pat.findall(ln)
            if not m_vals:
                continue
            
            valor = _smart_to_float(m_vals[-1])
            valor = round(valor, 2)
            if valor is None:
                continue

            # Data/hora receb.
            m_date = date_pat.search(ln)
            m_hour = hour_pat.search(ln)

            # TXID e pedido (heurísticas)
            m_txid = None
            # prefira uma cadeia muito longa (>=24), senão pega a mais longa disponível
            candidates_txid = txid_pat.findall(ln)
            if candidates_txid:
                m_txid = max(candidates_txid, key=len)

            # Pedido: número longo próximo ao TXID; como heurística, pegue o primeiro numérico longo diferente do TXID
            m_pedido = None
            if m_txid:
                # remove o txid do texto e volta a buscar
                ln_wo_txid = ln.replace(m_txid, " ")
                nums = pedido_pat.findall(ln_wo_txid)
                if nums:
                    m_pedido = nums[0]
            else:
                nums = pedido_pat.findall(ln)
                if nums:
                    m_pedido = nums[0]

            # Vendedor (código curto); escolha o primeiro que não coincida com filial/pedido
            m_vendedor = None
            short_nums = vendedor_pat.findall(ln)
            if short_nums:
                # Evitar capturar 'filial' duplicada; escolha um que tenha 4 dígitos (padrão mais comum)
                pref = [n for n in short_nums if len(n) == 4]
                m_vendedor = pref[0] if pref else short_nums[0]

            # Filial (primeiro token numérico curto no início)
            m_filial = None
            m_fil = filial_pat.match(ln)
            if m_fil:
                m_filial = m_fil.group(1)

            rows.append({
                "tipo_pagamento": "Pix QrCode",
                "valor_bruto": valor,
                "txid": m_txid,
                "pedido": m_pedido,
                "filial": m_filial,
                "dt_receb": m_date.group(0) if m_date else None,
                "hr_receb": m_hour.group(0) if m_hour else None,
                "vendedor": m_vendedor
            })

    return pd.DataFrame(rows)

def conciliar_pix_valores(vendas_df, pagamentos_df, tol: float = 0.01, max_items_venda: int = 10):
    """
    Concilia VENDAS Pix QrCode com PAGAMENTOS Pix QrCode:
    - Passo A: 1:1 por valor (tolerância 'tol').
    - Passo B: Multi-venda para 1 pagamento (subset de vendas cuja soma == pagamento), com até 'max_items_venda' itens.
    Retorna:
      comparacao_df, pagamentos_sem_match_df, sumario_df (agrupado por ['origem','status']).
    """
    import pandas as pd

    # Normaliza e filtra apenas Pix QrCode
    vendas = vendas_df.copy()
    pagamentos = pagamentos_df.copy()

    if "tipo_cartao" not in vendas.columns or "valor_venda" not in vendas.columns:
        raise ValueError("vendas_df deve conter colunas: tipo_cartao, valor_venda, origem, doc")
    if "tipo_pagamento" not in pagamentos.columns or "valor_bruto" not in pagamentos.columns:
        raise ValueError("pagamentos_df deve conter colunas: tipo_pagamento, valor_bruto")

    vendas = vendas[vendas["tipo_cartao"].astype(str).str.upper().str.contains("PIX", na=False)]
    pagamentos = pagamentos[pagamentos["tipo_pagamento"].astype(str).str.upper().str.contains("PIX", na=False)]

    vendas["valor_venda"] = vendas["valor_venda"].astype(float).round(2)
    pagamentos["valor_bruto"] = pagamentos["valor_bruto"].astype(float).round(2)

    # Índice de pagamentos disponíveis por valor (lista de índices)
    lookup = {}
    for i, v in pagamentos["valor_bruto"].items():
        lookup.setdefault(v, []).append(i)

    usados_pag = set()
    usados_vendas = set()
    rows = []

    # ---------- Passo A: 1:1 ----------
    for idx_v, s in vendas.iterrows():
        val_venda = float(s["valor_venda"])
        candidatos = lookup.get(round(val_venda, 2), [])
        pay_idx = None
        if candidatos:
            pay_idx = candidatos.pop(0)
        elif tol > 0:
            # tenta dentro da tolerância
            for v_val, idxs in lookup.items():
                if not idxs:
                    continue
                if abs(v_val - val_venda) <= tol:
                    pay_idx = idxs.pop(0)
                    break
        if pay_idx is not None:
            usados_pag.add(pay_idx)
            usados_vendas.add(idx_v)
            rows.append({
                "origem": s.get("origem", ""),
                "doc": s.get("doc", ""),
                "tipo": "Pix QrCode",
                "valor_venda": val_venda,
                "valor_pagamento": float(pagamentos.loc[pay_idx, "valor_bruto"]),
                "status": "Conciliado (valor)",
                "diferenca": round(float(pagamentos.loc[pay_idx, "valor_bruto"]) - val_venda, 2),
            })
        # se não casou, deixamos para o Passo B

    # ---------- Passo B: Multi-venda para 1 pagamento ----------
    # Pré-seleciona vendas livres (não usadas) para o subset-sum
    vendas_livres = [(idx_v, float(vendas.loc[idx_v, "valor_venda"]))
                     for idx_v in vendas.index if idx_v not in usados_vendas]
    # ordena para ajudar na poda
    vendas_livres.sort(key=lambda x: x[1])
    n_v = len(vendas_livres)

    def encontra_subset_por_total(alvo: float):
        """
        Busca subset de 'vendas_livres' cuja soma == alvo (com tol).
        Limita a combinação a 'max_items_venda' itens para evitar explosão.
        Retorna lista de índices de vendas ou None.
        """
        alvo_r = round(alvo, 2)
        # tenta 1:1 exato antes
        for i_v, v in vendas_livres:
            if i_v in usados_vendas:
                continue
            if abs(v - alvo_r) <= tol:
                return [i_v]

        # backtracking com poda
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

    # percorre pagamentos não usados e tenta formar subsets de vendas
    for pay_idx in pagamentos.index:
        if pay_idx in usados_pag:
            continue
        alvo = float(pagamentos.loc[pay_idx, "valor_bruto"])
        combo_v = encontra_subset_por_total(alvo)
        if combo_v:
            # marca como usados
            usados_pag.add(pay_idx)
            for i_v in combo_v:
                usados_vendas.add(i_v)
            # emite uma linha por venda apontando para o MESMO pagamento
            for i_v in combo_v:
                s_v = vendas.loc[i_v]
                rows.append({
                    "origem": s_v.get("origem", ""),
                    "doc": s_v.get("doc", ""),
                    "tipo": "Pix QrCode",
                    "valor_venda": float(s_v["valor_venda"]),
                    "valor_pagamento": alvo,
                    "status": "Conciliado (multi venda)",
                    "diferenca": round(alvo - float(s_v["valor_venda"]), 2),
                })

    # ---------- Linhas que continuaram sem match ----------
    for idx_v, s in vendas.iterrows():
        if idx_v in usados_vendas:
            continue
        rows.append({
            "origem": s.get("origem", ""),
            "doc": s.get("doc", ""),
            "tipo": "Pix QrCode",
            "valor_venda": float(s["valor_venda"]),
            "valor_pagamento": None,
            "status": "Sem pagamento encontrado",
            "diferenca": None,
        })

    comparacao_df = pd.DataFrame(rows)

    pagamentos_sem_match_df = pagamentos.loc[[i for i in pagamentos.index if i not in usados_pag]].copy()

    # Sumário por origem e status (separa CUPOM x NF x RECIBO)
    sumario_df = (
        comparacao_df.groupby(["origem", "status"])["valor_venda"]
        .agg(["count", "sum"])
        .reset_index()
    )

    return comparacao_df, pagamentos_sem_match_df, sumario_df

class ConciliacaoPixApp(tk.Toplevel):
    def __init__(self, master=None):
        super().__init__(master)

        # ===== Estilo Dark/Clam (mesmo do ConciliacaoApp) =====
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("TNotebook", background="#1e1e1e", borderwidth=0)
        style.configure("TNotebook.Tab",
                        background="#2e2e2e",
                        foreground="#ffffff",
                        padding=[10, 5])
        style.map("TNotebook.Tab",
                  background=[("selected", "#00bfff")],
                  foreground=[("selected", "#000000")])

        style.configure("TFrame", background="#1e1e1e")

        style.configure("Treeview",
                        background="#1e1e1e",
                        foreground="#ffffff",
                        fieldbackground="#1e1e1e",
                        rowheight=24)
        style.configure("Treeview.Heading",
                        background="#2e2e2e",
                        foreground="#ffffff")
        style.map("Treeview.Heading",
                  background=[("active", "#00bfff")])

        # ===== Janela =====
        self.title("Conciliação Pix QrCode")
        self.configure(bg="#1e1e1e")
        self.geometry("1080x720")
        if master is not None:
            self.transient(master)

        # ===== Estado =====
        self.cupom_paths = []
        self.nf_paths = []
        self.recibos_paths = []
        self.pagamentos_path = None

        self.vendas_aggregadas = pd.DataFrame()
        self.pagamentos_df = pd.DataFrame()
        self.comparacao_df = pd.DataFrame()
        self.pagamentos_sem_match = pd.DataFrame()
        self.summary_matches = pd.DataFrame()

        # ===== UI =====
        self._create_widgets()

    # ---------------- Helpers visuais/format ----------------
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

        for c in columns:
            head = (headings[c] if headings and c in headings else c)
            tree.heading(c, text=head)
            tree.column(c, width=140, stretch=True)

        if stretch_last and columns:
            tree.column(columns[-1], width=220, stretch=True)

        # tags (mesma paleta do Cartões)
        tree.tag_configure("ok", background="#133b2a")   # conciliado
        tree.tag_configure("warn", background="#3b1f13") # não encontrado
        return tree

    def _fill_tree(self, tree, df, money_cols=None, status_col=None):
        # limpa
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

    # ----------------- Construção da UI ---------------------
    def _create_widgets(self):
        pad = {"padx": 12, "pady": 8}

        # Linha de seleção (idêntica ao Cartões)
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

        self.lbl_cupom = make_file_row(0, "Cupom Fiscal:", self.select_cupom)
        self.lbl_nf    = make_file_row(1, "Notas Fiscais:", self.select_nf)
        self.lbl_rec   = make_file_row(2, "Recibos:", self.select_recibos)
        self.lbl_pay   = make_file_row(3, "Pagamentos Pix:", self.select_pagamentos)

        # Barra de ação (igual ao Cartões)
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

        # Notebook
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

        # Header Métricas
        header_sum = tk.Frame(self.tab_sum, bg="#1e1e1e")
        header_sum.pack(fill="x")
        self.lbl_metric = tk.Label(header_sum, text="Métricas: –",
                                   font=("Segoe UI", 11, "bold"),
                                   bg="#1e1e1e", fg="#ffffff")
        self.lbl_metric.pack(side="left", padx=8, pady=6)

        # Tabelas (colunas ADAPTADAS ao Pix)

        self.tree_sum = self._create_tree(
            self.tab_sum,
            columns=["origem", "status", "count", "sum"],
            headings={"origem": "Origem", "status": "Status", "count": "Qtde", "sum": "Valor (R$)"}
        )


        self.tree_comp = self._create_tree(
            self.tab_comp,
            columns=["origem", "doc", "valor_venda", "valor_pagamento", "status", "diferenca"],
            headings={"origem": "Origem", "doc": "Documento",
                      "valor_venda": "Venda (R$)", "valor_pagamento": "Pagamento (R$)",
                      "status": "Status", "diferenca": "Diferença (R$)"},
            stretch_last=True
        )

        self.tree_pay = self._create_tree(
            self.tab_pay,
            columns=["valor_bruto", "txid", "pedido", "filial", "dt_receb", "hr_receb", "vendedor"],
            headings={"valor_bruto": "Valor (R$)", "txid": "TXID", "pedido": "Pedido",
                      "filial": "Filial", "dt_receb": "Data", "hr_receb": "Hora", "vendedor": "Vendedor"}
        )

    # ----------------- Seletores -----------------
    def select_cupom(self):
        paths = filedialog.askopenfilenames(title="Selecionar Cupom Fiscal (PDF)", filetypes=[("PDF", "*.pdf")])
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
        path = filedialog.askopenfilename(title="Selecionar Pagamentos Pix (PDF)", filetypes=[("PDF", "*.pdf")])
        if path:
            self.pagamentos_path = path
            self.lbl_pay.config(text=re.split(r"[\\/]", path)[-1])

    # ----------------- Fluxo principal -----------------
    def run_conciliacao(self):
        try:
            # 1) Extrair vendas Pix (Cupom, NF, Recibos)
            vendas_frames = []

            for p in self.cupom_paths:
                vendas_frames.append(parse_cupom_pix_pdf(p))

            for p in self.nf_paths:
                vendas_frames.append(parse_nf_pix_pdf(p))

            for p in self.recibos_paths:
                vendas_frames.append(parse_recibos_pix_pdf(p))

            if not vendas_frames:
                messagebox.showwarning("Conciliação", "Nenhuma fonte de vendas selecionada.")
                return

            self.vendas_aggregadas = pd.concat(vendas_frames, ignore_index=True)

            # 2) Pagamentos Pix QrCode
            if not self.pagamentos_path:
                messagebox.showwarning("Conciliação", "Selecione o relatório de Pagamentos Pix.")
                return

            self.pagamentos_df = parse_pagamentos_pix_qr_pdf(self.pagamentos_path)

            # 3) Conciliação (por valor 1:1, tol=0.01)
            self.comparacao_df, self.pagamentos_sem_match, self.summary_matches = conciliar_pix_valores(
                self.vendas_aggregadas, self.pagamentos_df, tol=0.01
            )

            conc = (self.comparacao_df["status"] == "Conciliado (valor)").sum()
            nao  = (self.comparacao_df["status"] == "Sem pagamento encontrado").sum()
            sobra = len(self.pagamentos_sem_match)

            # 4) Atualiza métricas e tabelas
            self.lbl_metric.config(
                text=f"Métricas — Conciliadas: {conc}  |  Não encontradas: {nao}  |  Pagamentos sobrando: {sobra}"
            )

            # Sumário (adaptado → status, count, sum)
            df_sum = self.summary_matches.copy()
            if not df_sum.empty:
                df_sum.columns = ["origem", "status", "count", "sum"]  # renomeia para o cabeçalho da Tree
                self._fill_tree(self.tree_sum, df_sum, money_cols={"sum"}, status_col="status")
            else:
                self._fill_tree(self.tree_sum, pd.DataFrame())


            # Comparação (sem tipo_cartao)
            df_comp = self.comparacao_df.copy()
            self._fill_tree(self.tree_comp, df_comp,
                            money_cols={"valor_venda", "valor_pagamento", "diferenca"},
                            status_col="status")

            # Pagamentos sem conciliação (campos Pix)
            df_pay = self.pagamentos_sem_match.copy()
            # se vierem colunas adicionais, recria:
            if not df_pay.empty:
                cols = list(df_pay.columns)
                if set(cols) != set(self.tree_pay["columns"]):
                    for w in self.tab_pay.winfo_children():
                        w.destroy()
                    self.tree_pay = self._create_tree(
                        self.tab_pay, columns=cols,
                        headings={c: c.replace("_"," ").title() for c in cols}
                    )
                money_cols = {c for c in df_pay.columns if re.search(r"(valor|bruto|total)", c, flags=re.I)}
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



class ConciliacaoPixFrame(tk.Frame):
    def __init__(self, master=None, on_voltar=None):
        super().__init__(master, bg="#1e1e1e")
        self.on_voltar = on_voltar  # callback para voltar ao menu

        # ===== ESTILO DARK =====
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background="#1e1e1e", borderwidth=0)
        style.configure("TNotebook.Tab", background="#2e2e2e", foreground="#ffffff", padding=[10, 5])
        style.map("TNotebook.Tab", background=[("selected", "#00bfff")], foreground=[("selected", "#000000")])
        style.configure("TFrame", background="#1e1e1e")
        style.configure("Treeview",
                        background="#1e1e1e",
                        foreground="#ffffff",
                        fieldbackground="#1e1e1e",
                        rowheight=24)
        style.configure("Treeview.Heading", background="#2e2e2e", foreground="#ffffff")
        style.map("Treeview.Heading", background=[("active", "#00bfff")])

        # ===== Estado =====
        self.cupom_paths = []
        self.nf_paths = []
        self.recibos_paths = []
        self.pagamentos_path = None

        self.vendas_aggregadas = pd.DataFrame()
        self.pagamentos_df = pd.DataFrame()
        self.comparacao_df = pd.DataFrame()
        self.pagamentos_sem_match = pd.DataFrame()
        self.summary_matches = pd.DataFrame()

        # ===== UI =====
        self._create_widgets()

    # ---------- Helpers de UI ----------
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

        for c in columns:
            head = (headings[c] if headings and c in headings else c)
            tree.heading(c, text=head)
            tree.column(c, width=140, stretch=True)

        if stretch_last and columns:
            tree.column(columns[-1], width=220, stretch=True)

        tree.tag_configure("ok", background="#133b2a")   # verde escuro
        tree.tag_configure("warn", background="#3b1f13") # laranja escuro
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

    # ---------- Seletores ----------
    def _create_widgets(self):
        # Top bar com título e botão Voltar (igual ao fluxo do Controle de Lojas)
        header = tk.Frame(self, bg="#1e1e1e")
        header.pack(fill="x", padx=10, pady=(10, 0))

        tk.Label(header, text="Conciliação Pix QrCode",
                 font=("Segoe UI", 14, "bold"), bg="#1e1e1e", fg="#ffffff").pack(side="left")

        btn_voltar = tk.Button(header, text="Voltar", command=self._voltar,
                               font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
                               activebackground="#444444", activeforeground="#00bfff",
                               relief="flat", bd=0, padx=10, pady=5)
        btn_voltar.pack(side="right")
        self._apply_brilho(btn_voltar)

        pad = {"padx": 12, "pady": 8}
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
            lab = tk.Label(top, text="", font=("Segoe UI", 10), bg="#1e1e1e", fg="#ffffff")
            lab.grid(row=row, column=2, sticky="w", **pad)
            return lab

        self.lbl_cupom   = make_file_row(0, "Cupom Fiscal (PDF):", self.select_cupom)
        self.lbl_nf      = make_file_row(1, "Nota Fiscal à Vista (PDF):", self.select_nf)
        self.lbl_rec     = make_file_row(2, "Recibos (PDF):", self.select_recibos)
        self.lbl_pay     = make_file_row(3, "Pagamentos Pix QrCode (PDF):", self.select_pagamentos)

        # Barra de ação
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

        btn_export = tk.Button(btn_bar, text="Exportar Excel", command=self.exportar_excel,
                               font=("Segoe UI", 12), bg="#2e2e2e", fg="#ffffff",
                               activebackground="#444444", activeforeground="#00bfff",
                               relief="flat", bd=0, padx=12, pady=8)
        btn_export.pack(side="left", padx=6)
        self._apply_brilho(btn_export)

        # Notebook de dashboards
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

        header_sum = tk.Frame(self.tab_sum, bg="#1e1e1e")
        header_sum.pack(fill="x")
        self.lbl_metric = tk.Label(header_sum, text="Métricas: –",
                                   font=("Segoe UI", 11, "bold"),
                                   bg="#1e1e1e", fg="#ffffff")
        self.lbl_metric.pack(side="left", padx=8, pady=6)

        self.tree_sum = self._create_tree(
            self.tab_sum,
            columns=["status", "count", "sum"],
            headings={"status": "Status", "count": "Qtde", "sum": "Valor (R$)"}
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
            columns=["tipo_pagamento", "valor_bruto", "txid", "pedido", "filial", "dt_receb", "hr_receb", "vendedor"],
            headings={"tipo_pagamento": "Tipo", "valor_bruto": "Valor (R$)", "txid": "TXID", "pedido": "Pedido",
                      "filial": "Filial", "dt_receb": "Data", "hr_receb": "Hora", "vendedor": "Vendedor"}
        )

    # ---------- Seletores de arquivos ----------
    def select_cupom(self):
        paths = filedialog.askopenfilenames(title="Selecionar Cupom Fiscal (PDF)", filetypes=[("PDF", "*.pdf")])
        if paths:
            self.cupom_paths = list(paths)
            self.lbl_cupom.config(text=f"{len(paths)} arquivo(s)")

    def select_nf(self):
        paths = filedialog.askopenfilenames(title="Selecionar Notas à Vista (PDF)", filetypes=[("PDF", "*.pdf")])
        if paths:
            self.nf_paths = list(paths)
            self.lbl_nf.config(text=f"{len(paths)} arquivo(s)")

    def select_recibos(self):
        paths = filedialog.askopenfilenames(title="Selecionar Recibos (PDF)", filetypes=[("PDF", "*.pdf")])
        if paths:
            self.recibos_paths = list(paths)
            self.lbl_rec.config(text=f"{len(paths)} arquivo(s)")

    def select_pagamentos(self):
        path = filedialog.askopenfilename(title="Selecionar Pagamentos Pix QrCode (PDF)", filetypes=[("PDF", "*.pdf")])
        if path:
            self.pagamentos_path = path
            self.lbl_pay.config(text=re.split(r"[\\/]", path)[-1])

    # ---------- Fluxo principal ----------
    def run_conciliacao(self):
        try:
            # 1) Extrair vendas (Cupom, NF, Recibos)
            vendas_frames = []

            for p in self.cupom_paths:
                df = parse_cupom_pix_pdf(p)
                vendas_frames.append(df)

            for p in self.nf_paths:
                df = parse_nf_pix_pdf(p)
                vendas_frames.append(df)

            for p in self.recibos_paths:
                df = parse_recibos_pix_pdf(p)
                vendas_frames.append(df)

            if not vendas_frames:
                messagebox.showwarning("Conciliação", "Nenhuma fonte de vendas selecionada.")
                return

            self.vendas_aggregadas = pd.concat(vendas_frames, ignore_index=True)

            # 2) Pagamentos Pix QrCode
            if not self.pagamentos_path:
                messagebox.showwarning("Conciliação", "Selecione o relatório de pagamentos Pix QrCode.")
                return

            self.pagamentos_df = parse_pagamentos_pix_qr_pdf(self.pagamentos_path)

            # 3) Conciliação (por valor 1:1)
            self.comparacao_df, self.pagamentos_sem_match, self.summary_matches = conciliar_pix_valores(
                self.vendas_aggregadas, self.pagamentos_df, tol=0.01
            )

            conc = (self.comparacao_df["status"] == "Conciliado (valor)").sum()
            nao  = (self.comparacao_df["status"] == "Sem pagamento encontrado").sum()
            sobra = len(self.pagamentos_sem_match)

            # 4) Atualiza métricas e tabelas
            self.lbl_metric.config(
                text=f"Métricas — Conciliadas: {conc} | Não encontradas: {nao} | Pagamentos sobrando: {sobra}"
            )

            df_sum = self.summary_matches.copy()
            if not df_sum.empty:
                df_sum.columns = ["status", "count", "sum"]
            self._fill_tree(self.tree_sum, df_sum, money_cols={"sum"}, status_col="status")

            df_comp = self.comparacao_df.copy()
            self._fill_tree(self.tree_comp, df_comp,
                            money_cols={"valor_venda", "valor_pagamento", "diferenca"},
                            status_col="status")

            df_pay = self.pagamentos_sem_match.copy()
            self._fill_tree(self.tree_pay, df_pay, money_cols={"valor_bruto"})

            messagebox.showinfo("Conciliação",
                                f"Concluída.\nConciliadas: {conc}\nNão encontradas: {nao}\nPagamentos sobrando: {sobra}")

        except Exception as e:
            messagebox.showerror("Erro", f"Falha na conciliação:\n{e}")

    def exportar_excel(self):
        if self.comparacao_df.empty or self.vendas_aggregadas.empty or self.pagamentos_df.empty:
            messagebox.showwarning("Exportar", "Execute a conciliação antes de exportar.")
            return
        from tkinter import filedialog
        dest = filedialog.asksaveasfilename(
            title="Salvar Excel de Conciliação Pix",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")]
        )
        if not dest:
            return
        try:
            with pd.ExcelWriter(dest, engine="openpyxl") as w:
                self.comparacao_df.to_excel(w, index=False, sheet_name="comparacao")
                self.vendas_aggregadas.to_excel(w, index=False, sheet_name="vendas_aggregadas")
                self.pagamentos_df.to_excel(w, index=False, sheet_name="pagamentos_base")
                self.summary_matches.to_excel(w, index=False, sheet_name="sumario_matches")
                if not self.pagamentos_sem_match.empty:
                    self.pagamentos_sem_match.to_excel(w, index=False, sheet_name="pagamentos_sem_match")
            messagebox.showinfo("Exportar", f"Excel exportado com sucesso:\n{dest}")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao exportar:\n{e}")

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

    def _voltar(self):
        # callback para voltar ao menu (quem controla é o prisma.py)
        try:
            if callable(self.on_voltar):
                self.on_voltar()
        except Exception:
            pass




