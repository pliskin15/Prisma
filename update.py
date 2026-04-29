# aplica_patch_analise_lojas.py
# -*- coding: utf-8 -*-
import io, re, sys, shutil, os
from pathlib import Path

ARQ = Path(__file__).with_name("controle.py")
BACKUP = ARQ.with_suffix(".bak")

def load():
    txt = ARQ.read_text(encoding="utf-8", errors="ignore")
    return txt

def save(txt):
    shutil.copy2(ARQ, BACKUP)  # backup
    ARQ.write_text(txt, encoding="utf-8")
    print(f"[ok] Patch aplicado. Backup: {BACKUP.name}")

def ensure_once(txt, needle, add):
    if needle in txt:
        return txt
    # tenta injetar add logo após um ponto de ancoragem semelhante
    return txt + "\n\n" + add

def patch_agregar_dias_planejados(txt):
    # Insere cálculo de dias_planejados dentro de _agregar(...), uma única vez.
    pat_func = r"def\s+_agregar\([^)]*\):"
    if not re.search(pat_func, txt):
        print("[warn] _agregar(...) não encontrado; pulando.")
        return txt

    # ancora após inicialização de loja (chamada a _init_loja) e antes do loop dos dias
    pat_anchor = r"(_init_loja\(reg,\s*nome\)\s*\n)(\s*#\s*===\s*Dias do mês\s*===)"
    if re.search(r"lojas\[\(reg,\s*nome\)\]\[\"dias_planejados\"\]", txt):
        # já tem dias_planejados
        return txt

    add = r"""\1    # >>> DIAS PLANEJADOS (somente vigência da planilha)
    v = (loja.get("vigencia") or {})
    try:
        ini = int(v.get("ini", 1) or 1)
        fim = int(v.get("fim", dias_mes) or dias_mes)
        if ini < 1: ini = 1
        if fim > dias_mes: fim = dias_mes
        dias_planejados = max(0, fim - ini + 1)
    except Exception:
        dias_planejados = dias_mes
    lojas[(reg, nome)]["dias_planejados"] = int(dias_planejados)

\2"""
    new = re.sub(pat_anchor, add, txt, flags=re.M)
    if new != txt:
        print("[ok] _agregar: adicionou dias_planejados")
    else:
        print("[warn] _agregar: âncora não casou; verifique manualmente.")
    return new

def patch_aba_lojas_monta_df(txt):
    # Ajusta montagem de linhas/df_lojas: inclui Pend L3/L2/L1, Pontos (ranking), Dias válidos pela vigência
    pat_block_start = r"linhas\s*=\s*\[\]"
    if not re.search(pat_block_start, txt):
        print("[warn] bloco 'linhas = []' não encontrado; pulando.")
        return txt

    # adiciona cálculo dos pontos ao adicionar cada linha
    pat_append = r"""linhas\.append\(\{\s*?\n\s*"Regional":\s*reg,\s*"Loja":\s*nome,"""
    if re.search(r"\"Pontos \(ranking\)\":", txt):
        # já tem ranking
        return txt

    add_cols = r"""
        c3 = int(d["pend_por_nivel"].get(3, 0))
        c2 = int(d["pend_por_nivel"].get(2, 0))
        c1 = int(d["pend_por_nivel"].get(1, 0))
        pontos = 3*c3 + 2*c2 + 1*c1
"""
    txt = re.sub(pat_block_start, r"linhas = []\n" + add_cols, txt, count=1)

    # troca Dias válidos e % Finalizados para usar dias_planejados, mantendo fallback
    pat_dias_validos = r'"Dias válidos":\s*d\["dias_validos"\]'
    txt = re.sub(pat_dias_validos,
                 r'"Dias válidos": int(d.get("dias_planejados", d.get("dias_validos", 0)))',
                 txt, count=1)

    pat_pct = r'"% Finalizados":\s*\(0 if d\["dias_validos"\] == 0 else d\["finalizados"\]/d\["dias_validos"\]\*100\)'
    txt = re.sub(pat_pct,
                 r'"% Finalizados": (0 if d.get("dias_planejados", d.get("dias_validos", 0)) in (0, None) '
                 r'else d["finalizados"]/float(d.get("dias_planejados", d.get("dias_validos", 0)))*100)',
                 txt, count=1)

    # injeta novas chaves no append
    txt = re.sub(pat_append,
r'''linhas.append({
        "Regional": reg, "Loja": nome,''', txt, count=1)

    # após "GoodCard vencidos": ..., adicionar Pend L3/L2/L1 + Pontos
    txt = txt.replace(
        '"GoodCard vencidos": d.get("goodcard_vencidos", 0),',
        '"GoodCard vencidos": d.get("goodcard_vencidos", 0),\n'
        '        "Pend L3": c3,\n'
        '        "Pend L2": c2,\n'
        '        "Pend L1": c1,\n'
        '        "Pontos (ranking)": pontos,'
    )

    # atualiza hdr para incluir colunas novas antes de "Dias válidos"
    pat_hdr = r'hdr\s*=\s*\[([^\]]+)\]'
    m = re.search(pat_hdr, txt, flags=re.S)
    if m and "Pontos (ranking)" not in m.group(0):
        hdr_new = ('hdr = ["Regional","Loja",'
                   '"Pontos (ranking)","Pend L3","Pend L2","Pend L1",'
                   '"Dias válidos","Finalizados","% Finalizados",'
                   '"Sem pend. — dentro do prazo","Sem pend. — fora do prazo",'
                   '"Com pend. — dentro do prazo","Com pend. — fora do prazo",'
                   '"Pendência principal (mês)","Nível máx. (mês)","GoodCard vencidos"]')
        txt = re.sub(pat_hdr, hdr_new, txt, count=1, flags=re.S)
        print("[ok] cabeçalho (hdr) atualizado com ranking/níveis")
    else:
        print("[info] hdr já contem ranking ou não foi encontrado; mantendo.")

    return txt

def patch_tabela_scroll_e_filtro(txt):
    # Substitui corpo de _render_lojas_table por versão com scroll + filtro seguro + ordenação
    pat_def = r"def\s+_render_lojas_table\(\):"
    if not re.search(pat_def, txt):
        print("[warn] _render_lojas_table() não encontrado; pulando.")
        return txt

    repl = r'''
def _render_lojas_table():
    for w in lojas_tbl.winfo_children():
        w.destroy()

    # -- Container com Canvas + Scrollbar (vertical)
    container = tk.Frame(lojas_tbl, bg="#1e1e1e")
    container.pack(fill="both", expand=True)

    canvas = tk.Canvas(container, bg="#1e1e1e", highlightthickness=0)
    vsb = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    table_frame = tk.Frame(canvas, bg="#1e1e1e")
    canvas.create_window((0, 0), window=table_frame, anchor="nw")

    def _on_configure(_evt=None):
        canvas.configure(scrollregion=canvas.bbox("all"))
    table_frame.bind("<Configure>", _on_configure)

    # Cabeçalho
    header = tk.Frame(table_frame, bg="#1e1e1e")
    header.pack(fill="x")
    for j, col in enumerate(hdr):
        tk.Label(header, text=col, bg="#000000", fg="#ffffff", font=("Segoe UI", 9),
                 relief="solid", bd=1, width=18).grid(row=0, column=j, sticky="nsew")

    # Corpo
    body = tk.Frame(table_frame, bg="#1e1e1e")
    body.pack(fill="both", expand=True)

    df_view = df_lojas.copy()
    rf = reg_var.get().strip()
    if rf and "Regional" in df_view.columns:
        df_view = df_view[df_view["Regional"] == rf]

    # Blindagem de colunas para a ordenação
    for col in ["Pontos (ranking)", "Pend L3", "Pend L2", "Pend L1", "% Finalizados", "Regional"]:
        if col not in df_view.columns:
            df_view[col] = 0

    if not df_view.empty:
        df_view = df_view.sort_values(
            by=["Pontos (ranking)", "Pend L3", "Pend L2", "Pend L1", "% Finalizados"],
            ascending=[False, False, False, False, False]
        )

    for i, row in df_view.iterrows():
        for j, col in enumerate(hdr):
            val = f"{row[col]:.1f}" if isinstance(row[col], float) else f"{row[col]}"
            tk.Label(body, text=val, bg="#2e2e2e", fg="#ffffff", font=("Segoe UI", 9),
                     relief="solid", bd=1, width=18).grid(row=i+1, column=j, sticky="nsew")

    def _on_mousewheel(e):
        canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
    canvas.bind("<MouseWheel>", _on_mousewheel)
'''
    txt = re.sub(pat_def + r"[\s\S]*?(?=\ndef\s+_render_lojas_barras|\n\s*#|\Z)", repl, txt, count=1)
    print("[ok] _render_lojas_table() substituída (scroll + filtro seguro + ordenação)")
    return txt

def patch_pareto_regional(txt):
    # Blindar regionais[...] no _render_pareto_reg
    pat_def = r"def\s+_render_pareto_reg\(_evt=None\):"
    if not re.search(pat_def, txt):
        print("[warn] _render_pareto_reg() não encontrado; pulando.")
        return txt
    repl = r'''
def _render_pareto_reg(_evt=None):
    reg_sel = reg_sel_var.get().strip()
    d_reg = regionais.get(reg_sel)
    if not reg_sel or not d_reg:
        pareto_reg_lbl.config(image="", text="Selecione uma regional.", fg="#cccccc")
        return
    pend = d_reg.get("pend_por_rotulo", {})
    if not pend:
        pareto_reg_lbl.config(image="", text=f"{reg_sel}: sem pendências.", fg="#cccccc")
        return
    # (resto do desenho permanece igual ao original abaixo)
'''
    # Só injeta cabeçalho com checagens; não remove o resto.
    txt = txt.replace("def _render_pareto_reg(_evt=None):", repl)
    print("[ok] _render_pareto_reg() blindado com .get()")
    return txt

def patch_select_after_idle(txt):
    # Substitui nb.select(tab_geral) por after_idle seguro
    txt_new = txt
    txt_new = txt_new.replace(
        "_render()\n    nb.select(tab_geral)",
        "_render()\n\n    def _select_safe():\n        try:\n            if nb.winfo_exists() and tab_geral.winfo_exists():\n                nb.select(tab_geral)\n        except tk.TclError:\n            pass\n    win.after_idle(_select_safe)"
    )
    if txt_new != txt:
        print("[ok] nb.select(tab_geral) convertido para after_idle seguro")
    else:
        print("[info] chamada nb.select(tab_geral) não encontrada/alterada; ok se já ajustou antes.")
    return txt_new

def main():
    if not ARQ.exists():
        print(f"[erro] {ARQ} não encontrado.")
        sys.exit(1)
    txt = load()
    txt = patch_agregar_dias_planejados(txt)
    txt = patch_aba_lojas_monta_df(txt)
    txt = patch_tabela_scroll_e_filtro(txt)
    txt = patch_pareto_regional(txt)
    txt = patch_select_after_idle(txt)
    save(txt)

if __name__ == "__main__":
    main()