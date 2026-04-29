"""
Conciliador PIX QRCODE — PMZ Peças e Pneus
Relaciona vendas PIX (Cupom Fiscal, Nota Fiscal, Recibos)
com o extrato de Movimentação PIX QRCOD do banco.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import pdfplumber
import os, re
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# CORES
# ─────────────────────────────────────────────────────────────────────────────
COR_BG        = "#1e1e2e"
COR_PAINEL    = "#2a2a3e"
COR_BORDA     = "#3a3a5c"
COR_ACENTO    = "#7c6af7"
COR_ACENTO2   = "#5a4fcf"
COR_TEXTO     = "#e0e0f0"
COR_TEXTO_SEC = "#9090b0"
COR_VERDE     = "#2ecc71"
COR_AMARELO   = "#f39c12"
COR_VERMELHO  = "#e74c3c"
COR_CINZA     = "#7f8c8d"
COR_AZUL      = "#3498db"
COR_LARANJA   = "#e67e22"

# ─────────────────────────────────────────────────────────────────────────────
# PARSING DOS RELATÓRIOS
# ─────────────────────────────────────────────────────────────────────────────

def _num(val):
    """Converte string/float para float seguro. Suporta formato BR (1.234,56)."""
    if val is None:
        return 0.0
    s = str(val).strip()
    # Formato BR: tem ponto como separador de milhar e vírgula como decimal
    if re.search(r"\d\.\d{3},\d", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    s = re.sub(r"[^\d\.\-]", "", s)
    try:
        return float(s)
    except:
        return 0.0


def _extrair_linhas_pdf(path):
    """
    Extrai todas as linhas de texto de um PDF usando pdfplumber.
    Retorna lista de strings, uma por linha, preservando layout.
    Usa extract_text com layout=True para manter alinhamento de colunas.
    """
    linhas = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            texto = page.extract_text(layout=True) or ""
            for linha in texto.splitlines():
                linhas.append(linha)
    return linhas


def _ultimo_num_linha(linha):
    """
    Retorna o último número positivo de uma linha de texto.
    É o valor na coluna mais à direita — o valor circulado nos relatórios.
    """
    # Encontra todos os padrões numéricos BR na linha
    tokens = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d+\.\d{2}|\d+", linha)
    for tok in reversed(tokens):
        n = _num(tok)
        if n > 0:
            return n
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# PARSER: CUPOM FISCAL (PDF)
# ─────────────────────────────────────────────────────────────────────────────

def ler_cupom_fiscal(path):
    """
    Lê o Relatório Cupom Fiscal (PDF).

    Layout por cupom no texto extraído:
      Linha A:  88425  88425  002  NORMAL  CPIX  4088  5.405  28,32  0,00  0,00  0,00  28,32  28,32
      Linha B:  CODPOS  AUTORIZACAO  DOCUMENTO  SITUACAO  (opcional)
      Linha C:  13846955  5465613213  6545464546  VINCULADO   (opcional)
      Linha D:                              VENDA PIX                                           28,32
                                            ^texto pgto                              ^valor circulado

    Regra:
    - Linha com número ≥5 dígitos no início → novo cupom, captura COND (5ª palavra) e VENDER (6ª)
    - Linha contendo "VENDA PIX" mas NÃO "MAQUINETA" → pega último número = valor
    """
    linhas = _extrair_linhas_pdf(path)
    registros = []

    cupom_atual  = None
    cond_atual   = ""
    vender_atual = ""

    for linha in linhas:
        linha_strip = linha.strip()
        if not linha_strip:
            continue
        up = linha_strip.upper()

        # ── Linha principal do cupom ──────────────────────────────────────────
        # Começa com número de 5+ dígitos seguido de espaço e mais números
        m = re.match(r"^(\d{5,})\s+\d{5,}\s+\d+\s+\w+\s+(\S+)\s+(\S+)", linha_strip)
        if m:
            cupom_atual  = m.group(1)
            cond_atual   = m.group(2)   # ex: CPIX, CD, C4
            vender_atual = m.group(3)   # código do vendedor
            continue

        # ── Linhas de totais/rodapé → resetar contexto de cupom ─────────────
        # Palavras que indicam seção de totalizadores, não um cupom individual
        if any(t in up for t in ["TOTAL GERAL", "TOTAL :", "TOTAL:",
                                  "FILIAL :", "PERIODO", "EMISSAO",
                                  "DESCRICAO", "TOTAIS", "CANCELADOS",
                                  "SERVICOS", "VENDAS"]):
            cupom_atual = None
            continue

        # ── Linha de pagamento VENDA PIX ─────────────────────────────────────
        if "VENDA PIX" in up and "MAQUINETA" not in up:
            # Ignorar se não há cupom ativo (estamos numa seção de totais)
            if not cupom_atual:
                continue
            valor = _ultimo_num_linha(linha_strip)
            if valor > 0:
                registros.append({
                    "origem":     "Cupom Fiscal",
                    "referencia": f"Cupom {cupom_atual}",
                    "valor":      round(valor, 2),
                    "descricao":  (f"VENDA PIX | Cupom {cupom_atual} "
                                   f"| Cond: {cond_atual} | Vend: {vender_atual}"),
                    "status":     "pendente",
                    "par_banco":  "",
                })
                # Após registrar o pagamento PIX, resetar para não capturar
                # uma segunda linha "VENDA PIX" de totais logo abaixo
                cupom_atual = None

    return pd.DataFrame(registros)


# ─────────────────────────────────────────────────────────────────────────────
# PARSER: NOTA FISCAL (PDF)
# ─────────────────────────────────────────────────────────────────────────────

def ler_nota_fiscal(path):
    """
    Lê o Relatório de Venda Avista — Notas Fiscais (PDF).

    Layout por nota:
      Linha A:  259837  091  41  3  61001002  JURANDIR GOMES PEREIRA FILHO  2AMPIX  275,00  0,00  0,00  275,00  5.405
      Linha B:  CODPOS  AUTORIZACAO  DOCUMENTO  STATUS  (opcional)
      Linha C:                    VENDA PIX                                  275,00
                                  ^texto pgto                    ^valor circulado

    Regra:
    - Linha com número ≥5 dígitos no início + ao menos mais 2 números → nova nota
    - Linha contendo "VENDA PIX" mas NÃO "MAQUINETA" → pega último número
    """
    linhas = _extrair_linhas_pdf(path)
    registros = []

    nota_atual    = None
    cliente_atual = ""
    cond_atual    = ""

    for linha in linhas:
        linha_strip = linha.strip()
        if not linha_strip:
            continue
        up = linha_strip.upper()

        # ── Linha principal da nota ───────────────────────────────────────────
        # Padrão: número_nota  serie  filial  tipo  cod_cliente  NOME_CLIENTE  COND  valor...
        m = re.match(r"^(\d{5,})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s{2,}(\S+)\s+([\d\.,]+)", linha_strip)
        if m:
            nota_atual    = m.group(1)
            cliente_atual = m.group(6).strip()
            cond_atual    = m.group(7).strip()
            continue

        # Fallback: linha começa com 6+ dígitos e tem múltiplos campos numéricos
        if re.match(r"^\d{5,}\s", linha_strip):
            partes = linha_strip.split()
            nums_ini = sum(1 for p in partes[:5] if re.match(r"^\d+$", p))
            if nums_ini >= 3:
                nota_atual    = partes[0]
                # Cliente: palavras não-numéricas após os campos iniciais
                cliente_atual = " ".join(p for p in partes[4:10]
                                         if not re.match(r"^[\d\.,]+$", p))
                cond_atual    = ""
            continue

        # ── Linha de pagamento VENDA PIX ─────────────────────────────────────
        if "VENDA PIX" in up and "MAQUINETA" not in up:
            valor = _ultimo_num_linha(linha_strip)
            if valor > 0 and nota_atual:
                registros.append({
                    "origem":     "Nota Fiscal",
                    "referencia": f"NF {nota_atual}",
                    "valor":      round(valor, 2),
                    "descricao":  (f"VENDA PIX | NF {nota_atual} "
                                   f"| {cliente_atual} | Cond: {cond_atual}"),
                    "status":     "pendente",
                    "par_banco":  "",
                })

    return pd.DataFrame(registros)


# ─────────────────────────────────────────────────────────────────────────────
# PARSER: RECIBOS (PDF)
# ─────────────────────────────────────────────────────────────────────────────
def ler_recibos(path):
    """
    Lê o Relatório de Recibos (PDF).

    Layout de cada recibo:
      Linha cabeçalho:  41  41043849  3  41002201  NOME CLIENTE  DEP. PIX QRCOD  TOTAL :  137,67
      Linha DPP:        DPP  2419196399  2  137,67  127,46  8,21  0,00  2,00  0,00  0,00  0,00  0,00  137,67  0,00  0,00
      Colunas DPP:      TIPO_DOC DOC SERIE RECEBIDO V.DOC JR.DOC JR.CART DESPESAS DINH. CHEQUE CART.DEB CART.CRED DEPOSITO ANTECIPADO DEVCAR

    Regras:
    - Linha iniciando com "41  <número>" → novo recibo; pega partes[1] como número do recibo
    - Apenas recibos com "DEP. PIX QRCOD" ou "DEP. GETNET PIX" são capturados
    - Linha DPP: DEPOSITO é o 13º campo (índice 12 de todos campos, índice 10 após DOC e SERIE)
      Ordem:  [0]DPP [1]DOC [2]SERIE [3]RECEBIDO [4]V.DOC [5]JR.DOC [6]JR.CART
              [7]DESPESAS [8]DINH. [9]CHEQUE [10]CART.DEB [11]CART.CRED [12]DEPOSITO [13]ANTECIPADO [14]DEVCAR
    - Soma o DEPOSITO de todas as DPPs do mesmo recibo
    - Expõe campos "referencia" e "valor" compatíveis com df_vendas
    """
    linhas = _extrair_linhas_pdf(path)

    registros = []
    recibo_atual = None

    for linha in linhas:
        linha_limpa = linha.strip()
        up = linha_limpa.upper()

        # ── Detecta início de recibo ──────────────────────────────────────────
        m_recibo = re.match(r"^(\d+)\s+(\d+)\s+", linha_limpa)
        tem_texto_apos = bool(re.search(r"[A-Za-z]", linha_limpa.split(None, 2)[-1])) \
                 if m_recibo else False
        if m_recibo and tem_texto_apos:

            # Salva recibo anterior se tiver valor
            if recibo_atual and recibo_atual["valor"] > 0:
                registros.append(recibo_atual)
            recibo_atual = None

            # Só captura recibos com DEP. PIX QRCOD.
            # DEP. GETNET PIX é uma forma de pagamento distinta e NÃO deve
            # aparecer na lista de recibos PIX QR Code.
            if "DEP. PIX QRCOD" in up:
                partes = linha_limpa.split()
                numero_recibo = partes[1]   # número após o "41"

                # Extrai o TOTAL da linha de cabeçalho (último número)
                total_cabecalho = _ultimo_num_linha(linha_limpa)

                recibo_atual = {
                    "origem":     "Recibo",
                    "referencia": f"Recibo {numero_recibo}",
                    "valor":      0.0,      # será somado pelas linhas DPP
                    "descricao":  f"RECIBO PIX QRCOD | Recibo {numero_recibo}",
                    "status":     "pendente",
                    "par_banco":  "",
                }
            continue

        # ── Linhas DPP: soma coluna DEPOSITO (índice 12) ──────────────────────
        if recibo_atual and up.startswith("DPP"):
            # Extrai todos os números da linha na ordem em que aparecem
            nums = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", linha_limpa)
            # Estrutura esperada (sem contar "DPP" e DOC/SERIE que são inteiros):
            # Após DPP: DOC(int) SERIE(int) RECEBIDO V.DOC JR.DOC JR.CART DESPESAS DINH. CHEQUE CART.DEB CART.CRED DEPOSITO ANTECIPADO DEVCAR
            # Os números com vírgula começam em RECEBIDO (índice 0 do findall)
            # DEPOSITO é o 9º número com vírgula (índice 9)
            INDICE_DEPOSITO = 9
            if len(nums) > INDICE_DEPOSITO:
                deposito = _num(nums[INDICE_DEPOSITO])
                recibo_atual["valor"] = round(recibo_atual["valor"] + deposito, 2)

    # Adiciona último recibo
    if recibo_atual and recibo_atual["valor"] > 0:
        registros.append(recibo_atual)

    df = pd.DataFrame(registros)
    if not df.empty:
        df["saldo_rest"] = df["valor"]
    return df

def ler_mov_pix(path):
    """
    Lê o Relatório de Movimentação PIX QRCOD — Banco (PDF).

    Layout (texto extraído com layout=True):
      Cabeçalho:  FILIAL  DT RECEB.  HR RECEB.  VENDEDOR  PEDIDO  TXID  DT ENVIO  HR ENVIO  VALOR
      Dados:      41  14/04/2026  8,24  4088  4100889068  41000000004108890680114042026  14/04/2026  8,23  28,32

    Estratégia:
    - Detectar linha de cabeçalho pelo texto "TXID" e "VALOR"
    - Registrar posições horizontais (x) de cada coluna pelo cabeçalho
    - Para cada linha de dado subsequente, extrair os valores por posição x
    - Ignorar linhas de rodapé (TOTAL, página, etc.)
    """
    registros = []

    # Colunas esperadas — ordem importa para definir faixas x
    COLUNAS = ["FILIAL", "DT_RECEB", "HR_RECEB", "VENDEDOR", "PEDIDO", "TXID",
               "DT_ENVIO", "HR_ENVIO", "VALOR"]

    # Mapeamento: primeira palavra da coluna no cabeçalho → nome interno
    # Colunas compostas (DT RECEB., HR RECEB., DT ENVIO, HR ENVIO) começam com DT/HR,
    # então usamos a palavra seguinte para diferenciar.
    # Estratégia: juntar palavras consecutivas da mesma linha e casar sequências.
    SEQUENCIAS = [
        # (sequência de tokens upper, nome_coluna)
        (["DT", "RECEB."], "DT_RECEB"),
        (["HR", "RECEB."], "HR_RECEB"),
        (["DT", "ENVIO"],  "DT_ENVIO"),
        (["HR", "ENVIO"],  "HR_ENVIO"),
        (["FILIAL"],       "FILIAL"),
        (["VENDEDOR"],     "VENDEDOR"),
        (["PEDIDO"],       "PEDIDO"),
        (["TXID"],         "TXID"),
        (["VALOR"],        "VALOR"),
    ]

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # Extrair palavras com suas posições x0 para mapear colunas
            words = page.extract_words()
            if not words:
                continue

            # Agrupar palavras por linha (y arredondado)
            linhas_words = {}
            for w in words:
                y = round(float(w["top"]), 0)
                linhas_words.setdefault(y, []).append(w)

            col_x = {}   # nome_coluna → x0 da primeira palavra da coluna
            header_y = None

            for y in sorted(linhas_words):
                textos = " ".join(w["text"].upper() for w in linhas_words[y])
                if "TXID" in textos and "VALOR" in textos:
                    header_y = y
                    ws_linha = sorted(linhas_words[y], key=lambda w: float(w["x0"]))
                    tokens = [w["text"].upper() for w in ws_linha]
                    # Varrer tokens buscando cada sequência
                    for seq, col_nome in SEQUENCIAS:
                        for i in range(len(tokens) - len(seq) + 1):
                            if tokens[i:i+len(seq)] == seq:
                                if col_nome not in col_x:
                                    col_x[col_nome] = float(ws_linha[i]["x0"])
                                break
                    break

            if not col_x or header_y is None:
                # Fallback: usar extract_text com layout e parsear por texto
                texto = page.extract_text(layout=True) or ""
                _parsear_mov_pix_texto(texto, registros)
                continue

            # Ordenar colunas por posição x
            cols_ordenadas = sorted(col_x.items(), key=lambda kv: kv[1])
            # Para cada coluna, definir faixa x: de x_col até x_prox_col
            faixas = {}
            for i, (col, x) in enumerate(cols_ordenadas):
                x_fim = cols_ordenadas[i+1][1] if i+1 < len(cols_ordenadas) else 9999
                faixas[col] = (x - 5, x_fim)

            # Processar linhas abaixo do cabeçalho
            for y in sorted(linhas_words):
                if y <= header_y:
                    continue
                ws = linhas_words[y]
                texto_linha = " ".join(w["text"] for w in ws).upper()

                # Ignorar rodapés
                if any(t in texto_linha for t in ["TOTAL", "PAGINA", "PÁGINA",
                                                   "EMPRESA", "FILIAL :", "PERIODO",
                                                   "RELATORIO", "EMISSAO"]):
                    continue

                # Extrair valor de cada coluna pela posição x
                reg = {c: "" for c in COLUNAS}
                for w in ws:
                    wx = float(w["x0"])
                    for col, (x_ini, x_fim) in faixas.items():
                        if x_ini <= wx < x_fim:
                            reg[col] = (reg[col] + " " + w["text"]).strip()
                            break

                valor  = _num(reg.get("VALOR", ""))
                txid   = reg.get("TXID",   "").strip()
                pedido = reg.get("PEDIDO", "").strip()
                # Rejeita linhas sem TXID e sem PEDIDO (rodapés/totais)
                if valor > 0 and (txid or pedido):
                    registros.append({
                        "FILIAL":    reg.get("FILIAL",   ""),
                        "DT_RECEB":  reg.get("DT_RECEB", ""),
                        "HR_RECEB":  reg.get("HR_RECEB", ""),
                        "VENDEDOR":  reg.get("VENDEDOR", ""),
                        "PEDIDO":    pedido,
                        "TXID":      txid,
                        "VALOR":     valor,
                        "status":    "pendente",
                        "par_venda": "",
                        "saldo_rest": valor,
                    })

    COLS = ["FILIAL","DT_RECEB","HR_RECEB","VENDEDOR","PEDIDO",
            "TXID","VALOR","status","par_venda","saldo_rest"]
    if not registros:
        df = pd.DataFrame(columns=COLS)
        df["VALOR"]      = pd.Series(dtype=float)
        df["saldo_rest"] = pd.Series(dtype=float)
    else:
        df = pd.DataFrame(registros, columns=COLS)
        df["saldo_rest"] = df["VALOR"]
    return df


def _parsear_mov_pix_texto(texto, registros):
    """
    Fallback: parseia o relatório de mov. PIX como texto puro linha a linha.
    Detecta linhas de dados pelo padrão: FILIAL  DATA  HORA  ...  TXID  ...  VALOR
    """
    cabecalho_encontrado = False
    for linha in texto.splitlines():
        up = linha.strip().upper()
        if not up:
            continue
        if "TXID" in up and "VALOR" in up:
            cabecalho_encontrado = True
            continue
        if not cabecalho_encontrado:
            continue
        if any(t in up for t in ["TOTAL", "PAGINA", "PÁGINA", "EMPRESA",
                                  "PERIODO", "RELATORIO", "EMISSAO"]):
            continue

        # Linha de dado: começa com número de filial (ex: 41)
        # 9 campos: FILIAL  DT_RECEB  HR_RECEB  VENDEDOR  PEDIDO  TXID  DT_ENVIO  HR_ENVIO  VALOR
        m = re.match(
            r"^\s*(\d{2})\s+"           # 1 FILIAL
            r"(\d{2}/\d{2}/\d{4})\s+"  # 2 DT_RECEB
            r"(\S+)\s+"                 # 3 HR_RECEB
            r"(\S+)\s+"                 # 4 VENDEDOR
            r"(\S+)\s+"                 # 5 PEDIDO
            r"(\S+)\s+"                 # 6 TXID
            r"\S+\s+"                   # 7 DT_ENVIO (ignorado)
            r"\S+\s+"                   # 8 HR_ENVIO (ignorado)
            r"([\d\.,]+)\s*$",          # 9 VALOR
            linha.strip())
        if m:
            valor = _num(m.group(7))
            if valor > 0:
                registros.append({
                    "FILIAL":    m.group(1),
                    "DT_RECEB":  m.group(2),
                    "HR_RECEB":  m.group(3),
                    "VENDEDOR":  m.group(4),
                    "PEDIDO":    m.group(5),
                    "TXID":      m.group(6),
                    "VALOR":     valor,
                    "status":    "pendente",
                    "par_venda": "",
                    "saldo_rest": valor,
                })
            continue

        # Fallback mais genérico: pega o último número da linha como VALOR.
        # Exige data válida no 2º campo para evitar capturar linhas de rodapé
        # (ex: número de página, totais parciais) que geram valores fantasmas.
        partes = linha.strip().split()
        if (len(partes) >= 6
                and re.match(r"^\d{2}$", partes[0])
                and len(partes) > 1
                and re.match(r"^\d{2}/\d{2}/\d{4}$", partes[1])):
            valor = _num(partes[-1])
            if valor > 0:
                registros.append({
                    "FILIAL":    partes[0] if len(partes) > 0 else "",
                    "DT_RECEB":  partes[1] if len(partes) > 1 else "",
                    "HR_RECEB":  partes[2] if len(partes) > 2 else "",
                    "VENDEDOR":  partes[3] if len(partes) > 3 else "",
                    "PEDIDO":    partes[4] if len(partes) > 4 else "",
                    "TXID":      partes[5] if len(partes) > 5 else "",
                    "VALOR":     valor,
                    "status":    "pendente",
                    "par_venda": "",
                    "saldo_rest": valor,
                })


# ─────────────────────────────────────────────────────────────────────────────
# CONCILIAÇÃO AUTOMÁTICA
# ─────────────────────────────────────────────────────────────────────────────

def conciliar_automatico(df_vendas, df_banco, tolerancia=0.01):
    """
    Casa cada linha de venda com linha(s) do banco pelo valor.
    Suporta conciliações parciais (um valor vendas → vários pix ou vice-versa).
    """
    dv = df_vendas.copy()
    db = df_banco.copy()

    dv["status"]   = "pendente"
    dv["par_banco"] = ""
    dv["saldo_rest"] = dv["valor"]

    db["status"]    = "pendente"
    db["par_venda"] = ""
    db["saldo_rest"] = db["VALOR"]

    par_counter = [0]

    def novo_par():
        par_counter[0] += 1
        return f"P{par_counter[0]:04d}"

    # Indexar banco por valor para busca rápida
    for iv, row_v in dv.iterrows():
        saldo_v = dv.at[iv, "saldo_rest"]
        if saldo_v <= tolerancia:
            continue

        # Candidatos no banco: valor == saldo_v (exato) ou valor <= saldo_v
        candidatos = db[
            (db["saldo_rest"] > tolerancia) &
            (abs(db["VALOR"] - saldo_v) <= tolerancia)
        ]

        for ib, row_b in candidatos.iterrows():
            saldo_v = dv.at[iv, "saldo_rest"]
            saldo_b = db.at[ib, "saldo_rest"]
            if saldo_v <= tolerancia or saldo_b <= tolerancia:
                continue
            valor_match = min(saldo_v, saldo_b)
            par = novo_par()
            dv.at[iv, "par_banco"]  += ("," if dv.at[iv,"par_banco"] else "") + par
            dv.at[iv, "saldo_rest"]  = round(saldo_v - valor_match, 2)
            db.at[ib, "par_venda"]  += ("," if db.at[ib,"par_venda"] else "") + par
            db.at[ib, "saldo_rest"]  = round(saldo_b - valor_match, 2)

    # Calcular status
    def status_venda(row):
        if not row["par_banco"]:
            return "pendente"
        return "conciliado" if row["saldo_rest"] <= tolerancia else "parcial"

    def status_banco(row):
        if not row["par_venda"]:
            return "pendente"
        return "conciliado" if row["saldo_rest"] <= tolerancia else "parcial"

    dv["status"] = dv.apply(status_venda, axis=1)
    db["status"] = db.apply(status_banco, axis=1)

    return dv, db


# ─────────────────────────────────────────────────────────────────────────────
# INTERFACE
# ─────────────────────────────────────────────────────────────────────────────

class ConciliacaoPixApp(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)

        self.title("Conciliador PIX QRCODE — PMZ Peças e Pneus")
        self.geometry("1600x880")
        self.configure(bg=COR_BG)
        self.resizable(True, True)

        self.df_vendas  = None   # DataFrame unificado de vendas PIX
        self.df_banco   = None   # DataFrame da movimentação PIX banco
        self.paths      = {"cupom": None, "nf": None, "recibo": None, "banco": None}

        # Seleção manual
        self.sel_vendas = []     # lista de idx em df_vendas
        self.sel_bancos = []     # lista de idx em df_banco

        self._build_ui()
        self._aplicar_estilos()

    # ─── Build UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_topbar()
        corpo = tk.Frame(self, bg=COR_BG)
        corpo.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self._build_painel_esq(corpo)
        self._build_tabelas(corpo)
        self._build_statusbar()

    def _build_topbar(self):
        bar = tk.Frame(self, bg=COR_PAINEL, height=58)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="🔵  Conciliador PIX QRCODE — PMZ",
                 bg=COR_PAINEL, fg=COR_TEXTO,
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=16, pady=12)

        btns = [
            ("🔄  Conciliar Auto",   COR_AZUL,    self.conciliar_auto),
            ("🤝  Conciliar Manual", COR_VERDE,   self.conciliar_manual),
            ("🔓  Desconciliar",     COR_AMARELO, self.desconciliar),
            ("🚫  Ignorar",          COR_CINZA,   self.ignorar),
        ]
        for txt, cor, cmd in btns:
            tk.Button(bar, text=txt, bg=cor, fg="white",
                      font=("Segoe UI", 9, "bold"), relief="flat",
                      padx=12, pady=6, cursor="hand2",
                      command=cmd).pack(side="left", padx=4, pady=12)

    def _build_painel_esq(self, parent):
        frame = tk.Frame(parent, bg=COR_PAINEL, width=230)
        frame.pack(side="left", fill="y", padx=(0,10), pady=10)
        frame.pack_propagate(False)

        tk.Label(frame, text="ARQUIVOS", bg=COR_PAINEL, fg=COR_ACENTO,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(14,4))

        self.lbl_paths = {}
        arquivos = [
            ("cupom",  "📄 Cupom Fiscal",  self.carregar_cupom),
            ("nf",     "🧾 Nota Fiscal",   self.carregar_nf),
            ("recibo", "📋 Recibos",       self.carregar_recibo),
            ("banco",  "🏦 Mov. PIX Banco",self.carregar_banco),
        ]
        for chave, label, cmd in arquivos:
            tk.Button(frame, text=label, bg=COR_ACENTO2, fg="white",
                      font=("Segoe UI", 8, "bold"), relief="flat",
                      padx=8, pady=4, cursor="hand2", anchor="w",
                      command=cmd).pack(fill="x", padx=12, pady=(4,0))
            lbl = tk.Label(frame, text="(não carregado)", bg=COR_PAINEL,
                           fg=COR_TEXTO_SEC, font=("Segoe UI", 7),
                           wraplength=200, justify="left")
            lbl.pack(anchor="w", padx=14, pady=(0,4))
            self.lbl_paths[chave] = lbl

        # Resumo
        sep = tk.Frame(frame, bg=COR_BORDA, height=1)
        sep.pack(fill="x", padx=12, pady=10)
        tk.Label(frame, text="RESUMO VENDAS PIX", bg=COR_PAINEL, fg=COR_ACENTO,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12)

        self.lbl_res = {}
        itens_res = [
            ("v_total",      "Total registros:",   COR_TEXTO),
            ("v_conciliado", "✅ Conciliados:",    COR_VERDE),
            ("v_parcial",    "⚠ Parciais:",        COR_AMARELO),
            ("v_pendente",   "❌ Pendentes:",      COR_VERMELHO),
            ("v_ignorado",   "🚫 Ignorados:",      COR_CINZA),
            ("v_soma",       "Σ Valor Vendas:",    COR_TEXTO),
        ]
        for k, lbl, cor in itens_res:
            row = tk.Frame(frame, bg=COR_PAINEL)
            row.pack(fill="x", padx=12, pady=1)
            tk.Label(row, text=lbl, bg=COR_PAINEL, fg=COR_TEXTO_SEC,
                     font=("Segoe UI", 8)).pack(side="left")
            l = tk.Label(row, text="—", bg=COR_PAINEL, fg=cor,
                         font=("Segoe UI", 8, "bold"))
            l.pack(side="right")
            self.lbl_res[k] = l

        sep2 = tk.Frame(frame, bg=COR_BORDA, height=1)
        sep2.pack(fill="x", padx=12, pady=6)
        tk.Label(frame, text="RESUMO BANCO PIX", bg=COR_PAINEL, fg=COR_ACENTO,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12)

        itens_banco = [
            ("b_total",      "Total registros:",   COR_TEXTO),
            ("b_conciliado", "✅ Conciliados:",    COR_VERDE),
            ("b_pendente",   "❌ Pendentes:",      COR_VERMELHO),
            ("b_soma",       "Σ Valor Banco:",     COR_TEXTO),
            ("diferenca",    "Δ Diferença:",       COR_AZUL),
        ]
        for k, lbl, cor in itens_banco:
            row = tk.Frame(frame, bg=COR_PAINEL)
            row.pack(fill="x", padx=12, pady=1)
            tk.Label(row, text=lbl, bg=COR_PAINEL, fg=COR_TEXTO_SEC,
                     font=("Segoe UI", 8)).pack(side="left")
            l = tk.Label(row, text="—", bg=COR_PAINEL, fg=cor,
                         font=("Segoe UI", 8, "bold"))
            l.pack(side="right")
            self.lbl_res[k] = l

        # Conciliação manual
        sep3 = tk.Frame(frame, bg=COR_BORDA, height=1)
        sep3.pack(fill="x", padx=12, pady=8)
        tk.Label(frame, text="MANUAL", bg=COR_PAINEL, fg=COR_ACENTO,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12)
        tk.Label(frame,
                 text="1. Clique em uma ou mais VENDAS (tabela cima)\n"
                      "   (clique novamente para desmarcar)\n"
                      "2. Clique em um ou mais PIX (tabela baixo)\n"
                      "3. Pressione 'Conciliar Manual'",
                 bg=COR_PAINEL, fg=COR_TEXTO_SEC,
                 font=("Segoe UI", 8), justify="left").pack(anchor="w", padx=12, pady=2)

        self.lbl_sel_v = tk.Label(frame, text="Venda: (nenhuma)",
                                  bg=COR_PAINEL, fg=COR_VERDE,
                                  font=("Segoe UI", 8, "italic"), wraplength=210)
        self.lbl_sel_v.pack(anchor="w", padx=12)
        self.lbl_sel_b = tk.Label(frame, text="PIX banco: (nenhum)",
                                  bg=COR_PAINEL, fg=COR_AZUL,
                                  font=("Segoe UI", 8, "italic"), wraplength=210)
        self.lbl_sel_b.pack(anchor="w", padx=12, pady=(2,0))

        tk.Button(frame, text="Limpar seleção", bg=COR_BG, fg=COR_TEXTO_SEC,
                  font=("Segoe UI", 8), relief="flat", cursor="hand2",
                  command=self.limpar_selecao).pack(anchor="w", padx=12, pady=(6,0))

        # Filtro status
        sep4 = tk.Frame(frame, bg=COR_BORDA, height=1)
        sep4.pack(fill="x", padx=12, pady=8)
        tk.Label(frame, text="Filtrar status:", bg=COR_PAINEL, fg=COR_TEXTO_SEC,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=12)
        self.filtro_status = ttk.Combobox(frame, state="readonly",
            values=["Todos","pendente","parcial","conciliado","ignorado"])
        self.filtro_status.set("Todos")
        self.filtro_status.pack(fill="x", padx=12, pady=(2,4))
        self.filtro_status.bind("<<ComboboxSelected>>", lambda _: self.atualizar_tabelas())

    def _build_tabelas(self, parent):
        frame = tk.Frame(parent, bg=COR_BG)
        frame.pack(side="left", fill="both", expand=True, pady=10)

        # ── Tabela superior: Vendas PIX ───────────────────────────────────────
        tk.Label(frame, text="VENDAS PIX  (Cupom Fiscal + Nota Fiscal + Recibos)",
                 bg=COR_BG, fg=COR_ACENTO,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0,2))

        frm_v = tk.Frame(frame, bg=COR_BG)
        frm_v.pack(fill="both", expand=True)

        self.cols_v = ["origem", "referencia", "valor", "saldo_rest", "descricao", "status", "par_banco"]
        self.tree_v = ttk.Treeview(frm_v, columns=self.cols_v, show="headings",
                                   selectmode="extended", height=12)
        largs_v = {"origem":80,"referencia":100,"valor":90,"saldo_rest":90,
                   "descricao":380,"status":90,"par_banco":90}
        for col in self.cols_v:
            self.tree_v.heading(col, text=col.upper())
            self.tree_v.column(col, width=largs_v.get(col,100),
                               anchor="center" if col in ("valor","saldo_rest","status","par_banco") else "w")

        sb_vy = ttk.Scrollbar(frm_v, orient="vertical", command=self.tree_v.yview)
        sb_vx = ttk.Scrollbar(frm_v, orient="horizontal", command=self.tree_v.xview)
        self.tree_v.configure(yscrollcommand=sb_vy.set, xscrollcommand=sb_vx.set)
        sb_vy.pack(side="right", fill="y")
        sb_vx.pack(side="bottom", fill="x")
        self.tree_v.pack(fill="both", expand=True)
        self.tree_v.bind("<ButtonRelease-1>", self._on_click_venda)
        self._cfg_tags(self.tree_v)

        # ── Tabela inferior: Banco PIX ────────────────────────────────────────
        tk.Label(frame, text="MOVIMENTAÇÃO PIX QRCODE — BANCO",
                 bg=COR_BG, fg=COR_LARANJA,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8,2))

        frm_b = tk.Frame(frame, bg=COR_BG)
        frm_b.pack(fill="both", expand=True)

        self.cols_b = ["DT_RECEB","HR_RECEB","VENDEDOR","PEDIDO","TXID","VALOR","saldo_rest","status","par_venda"]
        self.tree_b = ttk.Treeview(frm_b, columns=self.cols_b, show="headings",
                                   selectmode="extended", height=12)
        largs_b = {"DT_RECEB":90,"HR_RECEB":70,"VENDEDOR":70,"PEDIDO":70,
                   "TXID":260,"VALOR":90,"saldo_rest":90,"status":90,"par_venda":90}
        for col in self.cols_b:
            self.tree_b.heading(col, text=col.upper())
            self.tree_b.column(col, width=largs_b.get(col,90),
                               anchor="center" if col in ("VALOR","saldo_rest","status","par_venda","HR_RECEB","DT_RECEB") else "w")

        sb_by = ttk.Scrollbar(frm_b, orient="vertical", command=self.tree_b.yview)
        sb_bx = ttk.Scrollbar(frm_b, orient="horizontal", command=self.tree_b.xview)
        self.tree_b.configure(yscrollcommand=sb_by.set, xscrollcommand=sb_bx.set)
        sb_by.pack(side="right", fill="y")
        sb_bx.pack(side="bottom", fill="x")
        self.tree_b.pack(fill="both", expand=True)
        self.tree_b.bind("<ButtonRelease-1>", self._on_click_banco)
        self._cfg_tags(self.tree_b)

    def _cfg_tags(self, tree):
        tree.tag_configure("conciliado", background="#1a3a2a", foreground="#2ecc71")
        tree.tag_configure("parcial",    background="#3a3010", foreground="#f39c12")
        tree.tag_configure("pendente",   background="#3a1010", foreground="#e74c3c")
        tree.tag_configure("ignorado",   background="#2a2a2a", foreground="#7f8c8d")
        tree.tag_configure("selecionado",background="#1a1a5e", foreground="#ffffff")
        tree.tag_configure("zebra",      background="#252535")

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=COR_PAINEL, height=26)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Pronto. Carregue os relatórios para começar.")
        tk.Label(bar, textvariable=self.status_var, bg=COR_PAINEL, fg=COR_TEXTO_SEC,
                 font=("Segoe UI", 8), anchor="w").pack(side="left", padx=10)

    def _aplicar_estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Treeview", background=COR_BG, fieldbackground=COR_BG,
                    foreground=COR_TEXTO, rowheight=22, font=("Segoe UI", 8))
        s.configure("Treeview.Heading", background=COR_PAINEL, foreground=COR_ACENTO,
                    font=("Segoe UI", 8, "bold"), relief="flat")
        s.map("Treeview", background=[("selected", COR_ACENTO2)])
        s.configure("TScrollbar", background=COR_PAINEL,
                    troughcolor=COR_BG, arrowcolor=COR_TEXTO_SEC)
        s.configure("TCombobox", fieldbackground=COR_BG,
                    background=COR_BG, foreground=COR_TEXTO)

    # ─── Carregamento de arquivos ─────────────────────────────────────────────

    def _carregar(self, chave, func, label, eh_pdf=False):
        ft_pdf   = [("PDF", "*.pdf"), ("Todos", "*.*")]
        ft_excel = [("Excel", "*.xlsx *.xls *.xlsm"), ("Todos", "*.*")]
        path = filedialog.askopenfilename(
            title=f"Selecionar {label}",
            filetypes=ft_pdf if eh_pdf else ft_excel)
        if not path:
            return
        try:
            self.status_var.set(f"⏳ Carregando {label}...")
            self.update()
            df = func(path)
            self.paths[chave] = path
            n = len(df)
            self.lbl_paths[chave].config(
                text=f"✅ {os.path.basename(path)} ({n} reg.)")
            self.status_var.set(
                f"✅ {label} carregado — {n} registros PIX encontrados.")
            return df
        except Exception as e:
            import traceback
            detalhe = traceback.format_exc()
            messagebox.showerror("Erro", f"Erro ao carregar {label}:\n{e}\n\n{detalhe}")
            return None

    def _unificar_vendas(self):
        partes = []
        for chave in ("cupom", "nf", "recibo"):
            if hasattr(self, f"_df_{chave}") and getattr(self, f"_df_{chave}") is not None:
                partes.append(getattr(self, f"_df_{chave}"))
        if not partes:
            return None
        df = pd.concat(partes, ignore_index=True)
        df["status"]    = "pendente"
        df["par_banco"] = ""
        df["saldo_rest"] = df["valor"]
        return df

    def carregar_cupom(self):
        df = self._carregar("cupom", ler_cupom_fiscal, "Cupom Fiscal", eh_pdf=True)
        if df is not None:
            self._df_cupom = df
            self._recarregar_vendas()

    def carregar_nf(self):
        df = self._carregar("nf", ler_nota_fiscal, "Nota Fiscal", eh_pdf=True)
        if df is not None:
            self._df_nf = df
            self._recarregar_vendas()

    def carregar_recibo(self):
        df = self._carregar("recibo", ler_recibos, "Recibos", eh_pdf=True)
        if df is not None:
            self._df_recibo = df
            self._recarregar_vendas()

    def carregar_banco(self):
        df = self._carregar("banco", ler_mov_pix, "Mov. PIX Banco", eh_pdf=True)
        if df is not None:
            self.df_banco = df
            self.atualizar_tabelas()
            self.atualizar_resumo()

    def _recarregar_vendas(self):
        self.df_vendas = self._unificar_vendas()
        self.limpar_selecao()
        self.atualizar_tabelas()
        self.atualizar_resumo()

    # ─── Ações ───────────────────────────────────────────────────────────────

    def conciliar_auto(self):
        if self.df_vendas is None or self.df_banco is None:
            messagebox.showwarning("Aviso", "Carregue os relatórios de vendas e o banco primeiro.")
            return
        self.status_var.set("⏳ Conciliando automaticamente...")
        self.update()
        self.df_vendas, self.df_banco = conciliar_automatico(self.df_vendas, self.df_banco)
        self.limpar_selecao()
        self.atualizar_tabelas()
        self.atualizar_resumo()
        n_cv = (self.df_vendas["status"] == "conciliado").sum()
        n_pv = (self.df_vendas["status"] == "pendente").sum()
        n_cb = (self.df_banco["status"]  == "conciliado").sum()
        n_pb = (self.df_banco["status"]  == "pendente").sum()
        self.status_var.set(
            f"🔄 Auto concluída — Vendas: ✅{n_cv} ❌{n_pv} | Banco: ✅{n_cb} ❌{n_pb}")

    def conciliar_manual(self):
        if not self.sel_vendas or not self.sel_bancos:
            messagebox.showwarning("Seleção incompleta",
                "Selecione pelo menos uma VENDA e pelo menos um registro PIX do banco.")
            return
        tol = 0.01
        for iv in self.sel_vendas:
            for ib in self.sel_bancos:
                sv = self.df_vendas.at[iv, "saldo_rest"]
                sb = self.df_banco.at[ib, "saldo_rest"]
                if sv <= tol or sb <= tol:
                    continue
                val = min(sv, sb)
                par = f"M{iv}-{ib}"
                def ap(df, idx, col, p):
                    a = df.at[idx, col]
                    df.at[idx, col] = f"{a},{p}" if a else p
                ap(self.df_vendas, iv, "par_banco", par)
                ap(self.df_banco,  ib, "par_venda", par)
                self.df_vendas.at[iv, "saldo_rest"] = round(sv - val, 2)
                self.df_banco.at[ib, "saldo_rest"]  = round(sb - val, 2)

        def novo_st_v(idx):
            r = self.df_vendas.loc[idx]
            if not r["par_banco"]: return "pendente"
            return "conciliado" if r["saldo_rest"] <= tol else "parcial"
        def novo_st_b(idx):
            r = self.df_banco.loc[idx]
            if not r["par_venda"]: return "pendente"
            return "conciliado" if r["saldo_rest"] <= tol else "parcial"

        for iv in self.sel_vendas:
            self.df_vendas.at[iv, "status"] = novo_st_v(iv)
        for ib in self.sel_bancos:
            self.df_banco.at[ib, "status"] = novo_st_b(ib)

        self.limpar_selecao()
        self.atualizar_tabelas()
        self.atualizar_resumo()
        self.status_var.set("🤝 Conciliação manual aplicada.")

    def desconciliar(self):
        self._alterar_status_selecionados(None)  # None = desconciliar

    def ignorar(self):
        self._alterar_status_selecionados("ignorado")

    def _alterar_status_selecionados(self, novo_status):
        for tree, df, col_par in [
            (self.tree_v, self.df_vendas, "par_banco"),
            (self.tree_b, self.df_banco,  "par_venda"),
        ]:
            if df is None:
                continue
            for item in tree.selection():
                idx = self._item_id(tree, item)
                if idx is None:
                    continue
                if novo_status is None:  # desconciliar
                    orig = df.at[idx, "valor"] if "valor" in df.columns else df.at[idx, "VALOR"]
                    df.at[idx, "status"]    = "pendente"
                    df.at[idx, col_par]     = ""
                    df.at[idx, "saldo_rest"] = orig
                else:
                    df.at[idx, "status"] = novo_status
        self.atualizar_tabelas()
        self.atualizar_resumo()
        acao = "desconciliados" if novo_status is None else "ignorados"
        self.status_var.set(f"✔ Registros {acao}.")

    # ─── Cliques na tabela ────────────────────────────────────────────────────

    def _on_click_venda(self, event):
        item = self.tree_v.identify_row(event.y)
        if not item:
            return
        idx = self._item_id(self.tree_v, item)
        if idx is None:
            return
        # Alterna seleção: clique novamente remove, clique novo adiciona
        if idx in self.sel_vendas:
            self.sel_vendas.remove(idx)
        else:
            self.sel_vendas.append(idx)
        n = len(self.sel_vendas)
        if n == 0:
            self.lbl_sel_v.config(text="Venda: (nenhuma)")
        elif n == 1:
            row = self.df_vendas.loc[self.sel_vendas[0]]
            self.lbl_sel_v.config(
                text=f"Venda: R$ {row['valor']:,.2f} | {str(row['referencia'])[:30]}")
        else:
            soma = sum(self.df_vendas.at[i, "valor"] for i in self.sel_vendas)
            self.lbl_sel_v.config(
                text=f"Vendas: {n} selecionada(s) | Σ R$ {soma:,.2f}")
        self._destacar()

    def _on_click_banco(self, event):
        item = self.tree_b.identify_row(event.y)
        if not item:
            return
        idx = self._item_id(self.tree_b, item)
        if idx is None:
            return
        if self.sel_vendas:
            if idx in self.sel_bancos:
                self.sel_bancos.remove(idx)
            else:
                self.sel_bancos.append(idx)
            self.lbl_sel_b.config(
                text=f"PIX banco: {len(self.sel_bancos)} selecionado(s)")
        self._destacar()

    def _destacar(self):
        # Vendas
        if self.df_vendas is not None:
            for item in self.tree_v.get_children():
                idx = self._item_id(self.tree_v, item)
                if idx is None: continue
                tag = self.df_vendas.at[idx, "status"]
                self.tree_v.item(item, tags=(tag,))
            for iv in self.sel_vendas:
                it = self._buscar_item(self.tree_v, iv)
                if it:
                    self.tree_v.item(it, tags=("selecionado",))
        # Banco
        if self.df_banco is not None:
            for item in self.tree_b.get_children():
                idx = self._item_id(self.tree_b, item)
                if idx is None: continue
                tag = self.df_banco.at[idx, "status"]
                self.tree_b.item(item, tags=(tag,))
            for ib in self.sel_bancos:
                it = self._buscar_item(self.tree_b, ib)
                if it:
                    self.tree_b.item(it, tags=("selecionado",))

    def limpar_selecao(self):
        self.sel_vendas = []
        self.sel_bancos = []
        self.lbl_sel_v.config(text="Venda: (nenhuma)")
        self.lbl_sel_b.config(text="PIX banco: (nenhum)")

    # ─── Atualização das tabelas ──────────────────────────────────────────────

    def atualizar_tabelas(self):
        self._popular_tree_vendas()
        self._popular_tree_banco()

    def _popular_tree_vendas(self):
        self._map_v = {}  # item → idx
        self._rmap_v = {} # idx → item
        self.tree_v.delete(*self.tree_v.get_children())
        if self.df_vendas is None:
            return
        df = self._filtrar(self.df_vendas, "status")
        for z, (idx, row) in enumerate(df.iterrows()):
            vals = (
                row.get("origem",""),
                row.get("referencia",""),
                f"{row['valor']:,.2f}",
                f"{row['saldo_rest']:,.2f}",
                str(row.get("descricao",""))[:70],
                row.get("status",""),
                row.get("par_banco",""),
            )
            st = row.get("status","pendente")
            tag = st if st in ("conciliado","parcial","pendente","ignorado") else "pendente"
            item = self.tree_v.insert("", "end", values=vals, tags=(tag,))
            self._map_v[item]  = idx
            self._rmap_v[idx]  = item

    def _popular_tree_banco(self):
        self._map_b  = {}
        self._rmap_b = {}
        self.tree_b.delete(*self.tree_b.get_children())
        if self.df_banco is None:
            return
        df = self._filtrar(self.df_banco, "status")
        for z, (idx, row) in enumerate(df.iterrows()):
            vals = (
                str(row.get("DT_RECEB","")),
                str(row.get("HR_RECEB","")),
                str(row.get("VENDEDOR","")),
                str(row.get("PEDIDO","")),
                str(row.get("TXID",""))[:55],
                f"{row['VALOR']:,.2f}",
                f"{row['saldo_rest']:,.2f}",
                row.get("status",""),
                row.get("par_venda",""),
            )
            st  = row.get("status","pendente")
            tag = st if st in ("conciliado","parcial","pendente","ignorado") else "pendente"
            item = self.tree_b.insert("", "end", values=vals, tags=(tag,))
            self._map_b[item]  = idx
            self._rmap_b[idx]  = item

    def _filtrar(self, df, col_status):
        f = self.filtro_status.get()
        if f == "Todos":
            return df
        return df[df[col_status] == f]

    def atualizar_resumo(self):
        if self.df_vendas is not None:
            cnt = self.df_vendas["status"].value_counts()
            self.lbl_res["v_total"].config(text=str(len(self.df_vendas)))
            for s in ("conciliado","parcial","pendente","ignorado"):
                self.lbl_res[f"v_{s}"].config(text=str(cnt.get(s,0)))
            self.lbl_res["v_soma"].config(text=f"R$ {self.df_vendas['valor'].sum():,.2f}")

        if self.df_banco is not None:
            cnt = self.df_banco["status"].value_counts()
            self.lbl_res["b_total"].config(text=str(len(self.df_banco)))
            self.lbl_res["b_conciliado"].config(text=str(cnt.get("conciliado",0)))
            self.lbl_res["b_pendente"].config(text=str(cnt.get("pendente",0)))
            soma_b = self.df_banco["VALOR"].sum()
            self.lbl_res["b_soma"].config(text=f"R$ {soma_b:,.2f}")

            if self.df_vendas is not None:
                soma_v = self.df_vendas["valor"].sum()
                dif    = soma_v - soma_b
                cor    = COR_VERDE if abs(dif) < 0.05 else COR_VERMELHO
                self.lbl_res["diferenca"].config(
                    text=f"R$ {dif:,.2f}", fg=cor)

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _item_id(self, tree, item):
        if tree == self.tree_v:
            return self._map_v.get(item)
        return self._map_b.get(item)

    def _buscar_item(self, tree, idx):
        if tree == self.tree_v:
            return self._rmap_v.get(idx)
        return self._rmap_b.get(idx)
