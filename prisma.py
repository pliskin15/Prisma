import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import date, datetime
from calendar import monthrange
import pandas as pd
import pdfplumber
import re
import os
from unidecode import unidecode
from PIL import Image, ImageTk
from controle import ControleLojas
import sys
from pathlib import Path
from PIL import Image, ImageTk
import json
from controle import ARQUIVO_PERFIS, MESES_PTBR, normalizar_loja_valor
from conciliador import ConciliacaoApp
from conciliador_pix_qrcode import ConciliacaoPixApp
from conciliador_pix_maquineta import ConciliacaoPixMaquinetaApp
import shutil
from supabase_config import supabase
from datetime import datetime


def _to_iso(data_txt: str) -> str:
    """Converte 'dd/mm/aaaa' ou 'aaaa-mm-dd' para 'aaaa-mm-dd'."""
    try:
        return datetime.strptime(data_txt, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return datetime.strptime(data_txt, "%d/%m/%Y").strftime("%Y-%m-%d")

def extrair_cabecalho_memorando(pdf_path: str):
    """Retorna (loja, data_ddmmyyyy) da 1ª página do PDF (título/cabeçalho)."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return None, None
            raw = pdf.pages[0].extract_text() or ""
    except Exception:
        return None, None
    txt = " ".join(raw.split())
    up = unidecode(txt).upper()

    loja = None
    m = re.search(r"\bLOJA\s+(\d{1,4})\b", up) or \
        re.search(r"\bMEMORANDO\s+DE\s+CAIXA\s+LOJA\s+(\d{1,4})\b", up)
    if m:
        loja = m.group(1)

    data_br = None
    m = re.search(r"\bDATA\s+DO\s+CAIXA\s*:\s*(\d{2}/\d{2}/\d{4})\b", up) or \
        re.search(r"\bEMISSAO\s*[: ]\s*(\d{2}/\d{2}/\d{4})\b", up)
    if m:
        data_br = m.group(1)

    return loja, data_br



if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent

ICON_PATH = BASE_DIR / "assets" / "icone.png"
LOGO_PATH = BASE_DIR / "assets" / "logo.png"

icone = None
logo = None
menu_principal_frame = None
controle_frame = None
pix_frame = None
submenu_conc_frame = None


# >>> INÍCIO PATCH 1575 - helper de classificação por "número - xxx -"
def _classificar_1575_por_codigo_final(doc_str: str):
    """
    Para DOC no formato 'número - xxx -' (ou 'número-xxx' etc.):
      - Se xxx < 025  -> 'Cupom Fiscal'
      - Se xxx >= 025 -> 'Nota Fiscal'
    Retorna 'Cupom Fiscal'/'Nota Fiscal' ou None se não encontrar o padrão.
    """
    try:
        s = str(doc_str)
        # Aceita variações de espaços e hífen final opcional
        # Exemplos válidos: "12345 - 007 -", "12345-007", "12345 - 025"
        m = re.search(r'(\d+)\s*-\s*(\d{3})\s*-?', s)
        if not m:
            return None
        codigo = int(m.group(2))  # '007' -> 7; '025' -> 25
        return "Cupom Fiscal" if codigo < 25 else "Nota Fiscal"
    except Exception:
        return None


def carregar_imagens():
    global icone, logo
    try:
        icone_img = Image.open(ICON_PATH)
        icone = ImageTk.PhotoImage(icone_img)
    except Exception as e:
        print("⚠️ Ícone não encontrado:", e)

    try:
        logo_img = Image.open(LOGO_PATH)
        logo_img = logo_img.resize((200, 200))
        logo = ImageTk.PhotoImage(logo_img)
    except Exception as e:
        print("⚠️ Logo não encontrado:", e)

def carregar_logo():
    try:
        logo_img = Image.open(LOGO_PATH)
        logo_img = logo_img.resize((200, 200))
        return ImageTk.PhotoImage(logo_img)
    except Exception:
        return None


# === LOGIN LOCAL (USUÁRIOS) ===
import os, json, base64, hashlib, hmac, secrets

ARQUIVO_USUARIOS = "usuarios.json"
PBKDF_ITER = 150_000  # segurança x desempenho
current_user = None   # sessão atual
usuarios_cache = []   # cache em memória

def _hash_password(senha: str, salt_b64: str | None = None):
    """Gera/usa salt (base64) e retorna (salt_b64, hash_b64) com PBKDF2-HMAC-SHA256."""
    if salt_b64 is None:
        salt = os.urandom(16)
        salt_b64 = base64.b64encode(salt).decode()
    else:
        salt = base64.b64decode(salt_b64)
    dk = hashlib.pbkdf2_hmac('sha256', senha.encode('utf-8'), salt, PBKDF_ITER)
    return salt_b64, base64.b64encode(dk).decode()

def _verify_password(senha: str, salt_b64: str, hash_b64: str) -> bool:
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac('sha256', senha.encode('utf-8'), salt, PBKDF_ITER)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


from supabase_config import supabase

def load_users() -> list[dict]:
    global usuarios_cache
    res = supabase.table("usuarios").select("*").execute()
    usuarios_cache = res.data or []
    return usuarios_cache


def save_users(users: list[dict]):
    if not users:
        return
    supabase.table("usuarios").upsert(users, on_conflict="username").execute()


def find_user(username: str, only_active=True) -> dict | None:
    uname = (username or "").lower()
    q = supabase.table("usuarios").select("*").eq("username", uname)
    if only_active:
        q = q.eq("ativo", True)
    res = q.limit(1).execute()
    data = res.data or []
    return data[0] if data else None


def create_user(*args, **kwargs):
    # mantém seu parser original
    username = kwargs.get("username") or args[0]
    senha = kwargs.get("senha") or args[1]
    email = kwargs.get("email")
    role = kwargs.get("role") or "auditor"
    nome = kwargs.get("nome") or username
    foto = kwargs.get("foto")

    username = username.lower()
    if email:
        email = email.lower()

    exists = supabase.table("usuarios").select("username") \
            .eq("username", username).execute().data
    if exists:
        raise ValueError("Já existe um usuário com esse username.")

    if email:
        dup = supabase.table("usuarios").select("email") \
              .eq("email", email).execute().data
        if dup:
            raise ValueError("Já existe um usuário com esse e-mail.")

    salt_b64, hash_b64 = _hash_password(senha)

    novo = {
        "username": username,
        "nome": nome,
        "email": email,
        "role": "auditor" if role == "operador" else role,
        "salt": salt_b64,
        "hash": hash_b64,
        "foto": foto,
        "ativo": True,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    save_users([novo])
    load_users()
    return novo


def update_user(orig_username: str, new_username=None, email=None,
                role=None, nome=None, nova_senha=None, foto=None):

    u = find_user(orig_username, only_active=False)
    if not u:
        raise ValueError("Usuário original não encontrado.")

    # username
    if new_username and new_username.strip().lower() != u["username"]:
        new_username = new_username.lower().strip()
        exists = supabase.table("usuarios").select("username") \
                .eq("username", new_username).execute().data
        if exists:
            raise ValueError("Já existe um usuário com esse username.")
        u["username"] = new_username

    # email
    if email is not None:
        email = email.strip().lower()
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            raise ValueError("E-mail inválido.")

        dup = supabase.table("usuarios").select("email","username") \
                .eq("email", email).execute().data or []
        if any(item["username"] != u["username"] for item in dup):
            raise ValueError("Já existe um usuário com esse e-mail.")

        u["email"] = email

    # nome
    if nome is not None:
        u["nome"] = nome.strip() or u["username"]

    # role
    if role is not None:
        role = role.lower().strip()
        if role == "operador":
            role = "auditor"
        if role not in ("auditor","gestor","admin"):
            role = "auditor"
        u["role"] = role

    # foto
    if foto is not None:
        u["foto"] = foto or None

    # senha
    if nova_senha:
        if len(nova_senha) < 6:
            raise ValueError("Nova senha deve ter pelo menos 6 caracteres.")
        salt_b64, hash_b64 = _hash_password(nova_senha)
        u["salt"], u["hash"] = salt_b64, hash_b64

    save_users([u])
    load_users()
    return u

def set_password(username: str, nova_senha: str):
    u = find_user(username, only_active=False)
    if not u:
        raise ValueError("Usuário não encontrado.")

    salt_b64, hash_b64 = _hash_password(nova_senha)
    u["salt"], u["hash"] = salt_b64, hash_b64
    save_users([u])
    load_users()

def authenticate(username: str, senha: str):
    u = find_user(username, only_active=True)
    if not u:
        return None
    return u if _verify_password(senha, u["salt"], u["hash"]) else None

def editar_perfil_modal(master):
    """
    Permite ao usuário logado editar o próprio perfil.
    - Qualquer papel pode usar (não exige admin).
    - Admin pode mudar o próprio PERFIL; demais veem o perfil apenas como leitura.
    - Senha é opcional (em branco = manter).
    - Foto: apenas salva o caminho escolhido no Windows (com pré-visualização).
    """
    global current_user
    if not current_user:
        messagebox.showwarning("Sessão", "Faça login para editar seu perfil.")
        return

    u = current_user.copy()

    top = tk.Toplevel(master)
    top.title("Editar meu perfil")
    top.configure(bg="#1e1e1e")
    top.grab_set()

    # ---------- Labels ----------
    tk.Label(top, text="Username:",   bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="e", padx=6, pady=4)
    tk.Label(top, text="E-mail:",     bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="e", padx=6, pady=4)
    tk.Label(top, text="Nova Senha:", bg="#1e1e1e", fg="#ffffff").grid(row=2, column=0, sticky="e", padx=6, pady=4)
    tk.Label(top, text="Perfil:",     bg="#1e1e1e", fg="#ffffff").grid(row=3, column=0, sticky="e", padx=6, pady=4)
    tk.Label(top, text="Foto:",       bg="#1e1e1e", fg="#ffffff").grid(row=4, column=0, sticky="e", padx=6, pady=4)

    # ---------- Vars ----------
    user_var  = tk.StringVar(top, value=u.get("username", ""))
    email_var = tk.StringVar(top, value=u.get("email", "") or "")
    pwd_var   = tk.StringVar(top, value="")  # em branco = manter senha
    role_var  = tk.StringVar(top, value=(u.get("role") or "auditor"))
    foto_var  = tk.StringVar(top, value=u.get("foto", "") or "")
    msg_var   = tk.StringVar(top, value="")

    # ---------- Inputs ----------
    e_user  = tk.Entry(top, textvariable=user_var,  bg="#2e2e2e", fg="#ffffff", relief="flat", width=28)
    e_email = tk.Entry(top, textvariable=email_var, bg="#2e2e2e", fg="#ffffff", relief="flat", width=28)
    e_pwd   = tk.Entry(top, textvariable=pwd_var,   bg="#2e2e2e", fg="#ffffff", relief="flat", width=28, show="•")
    e_user.grid(row=0, column=1, padx=6, pady=4)
    e_email.grid(row=1, column=1, padx=6, pady=4)
    e_pwd.grid(row=2, column=1, padx=6, pady=4)

    # Perfil: admin pode alterar; demais só visualizam
    is_admin = (u.get("role") == "admin")
    if is_admin:
        cb_role = ttk.Combobox(top, textvariable=role_var, values=["auditor","gestor","admin"], width=25)
        cb_role.grid(row=3, column=1, padx=6, pady=4)
    else:
        tk.Label(top, textvariable=role_var, bg="#1e1e1e", fg="#cccccc").grid(row=3, column=1, sticky="w", padx=6, pady=4)

    # ---------- Foto / Preview ----------
    foto_frame = tk.Frame(top, bg="#1e1e1e")
    foto_frame.grid(row=4, column=1, sticky="w", padx=6, pady=4)

    preview_lbl = tk.Label(foto_frame, bg="#2e2e2e")
    preview_lbl.pack(side="left", padx=5, pady=5)

    def carregar_preview(caminho):
        if not caminho or not os.path.exists(caminho):
            preview_lbl.config(image="", text="(sem imagem)", fg="#cccccc", bg="#2e2e2e")
            preview_lbl.image = None
            return
        try:
            img = Image.open(caminho)
            resample = getattr(Image, "LANCZOS", Image.BICUBIC)
            img = img.resize((96, 96), resample)
            ph = ImageTk.PhotoImage(img)
            preview_lbl.config(image=ph, text="")
            preview_lbl.image = ph
        except Exception:
            preview_lbl.config(text="Prévia indisponível", fg="#cccccc", bg="#2e2e2e")

    def selecionar_foto():
        caminho = filedialog.askopenfilename(
            title="Selecione a foto",
            filetypes=[("Imagens","*.png;*.jpg;*.jpeg;*.gif;*.bmp"),("Todos","*.*")]
        )
        if caminho:
            foto_var.set(caminho)
            carregar_preview(caminho)

    tk.Button(
        foto_frame, text="Selecionar...", command=selecionar_foto,
        bg="#2e2e2e", fg="#ffffff", relief="flat", padx=10, pady=6
    ).pack(side="left", padx=8)

    carregar_preview(foto_var.get())

    # ---------- Mensagens ----------
    tk.Label(top, textvariable=msg_var, bg="#1e1e1e", fg="#ff8080").grid(row=5, column=0, columnspan=2)

    # ---------- Botões ----------
    btn_frame = tk.Frame(top, bg="#1e1e1e")
    btn_frame.grid(row=6, column=0, columnspan=2, pady=12)

    def salvar():
        new_username = user_var.get().strip()
        email = email_var.get().strip()
        nova_senha = pwd_var.get()  # vazio = não trocar
        foto_caminho = foto_var.get().strip()
        role_to_send = role_var.get().strip() if is_admin else None  # só admin pode alterar o próprio papel
        nome = new_username  # se quiser expor campo "Nome", dá para incluir depois

        if not new_username:
            msg_var.set("Informe o username.")
            return
        if not email or not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            msg_var.set("E-mail inválido.")
            return
        if nova_senha and len(nova_senha) < 6:
            msg_var.set("A nova senha deve ter pelo menos 6 caracteres.")
            return

        try:
            update_user(
                orig_username=u.get("username"),
                new_username=new_username,
                email=email,
                role=role_to_send,
                nome=nome,
                nova_senha=nova_senha if len(nova_senha) > 0 else None,
                foto=foto_caminho
            )
            # Atualiza sessão e UI (avatar, header, etc.)
            from tkinter import TclError
            try:
                # recarrega o usuário atualizado
                updated = find_user(new_username, only_active=False)
                if updated:
                    current_user = updated
            except Exception:
                pass

            try:
                construir_menu_principal()  # re-renderiza o header/avatar e menu
            except TclError:
                pass

            messagebox.showinfo("Sucesso", "Perfil atualizado com sucesso.")
            top.destroy()
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    tk.Button(
        btn_frame, text="OK", command=salvar,
        bg="#2e2e2e", fg="#ffffff", relief="flat", padx=16, pady=6
    ).pack(side="right", padx=6)

    tk.Button(
        btn_frame, text="Voltar", command=top.destroy,
        bg="#8b0000", fg="#ffffff", relief="flat", padx=16, pady=6
    ).pack(side="right", padx=6)

    e_user.focus_set()


def recuperar_senha_modal(master):
    """
    Modal de recuperação de senha (sem senha atual):
    - Solicita username, nova senha e confirmação.
    - Valida existência do usuário e altera via set_password.
    """
    win = tk.Toplevel(master)
    win.title("Recuperar senha")
    win.configure(bg="#1e1e1e")
    win.resizable(False, False)
    win.grab_set()

    def centralizar(janela, w=380, h=210):
        janela.update_idletasks()
        sw, sh = janela.winfo_screenwidth(), janela.winfo_screenheight()
        x, y = (sw - w)//2, (sh - h)//2
        janela.geometry(f"{w}x{h}+{x}+{y}")

    frm = tk.Frame(win, bg="#1e1e1e")
    frm.pack(padx=16, pady=16, fill="x")

    tk.Label(frm, text="Username:", bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="e", padx=6, pady=6)
    tk.Label(frm, text="Nova senha:", bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="e", padx=6, pady=6)
    tk.Label(frm, text="Confirmar nova senha:", bg="#1e1e1e", fg="#ffffff").grid(row=2, column=0, sticky="e", padx=6, pady=6)

    user_var = tk.StringVar()
    new_var  = tk.StringVar()
    rep_var  = tk.StringVar()

    e_user = tk.Entry(frm, textvariable=user_var, bg="#2e2e2e", fg="#ffffff",
                      relief="flat", insertbackground="#ffffff", width=28)
    e_new  = tk.Entry(frm, textvariable=new_var,  bg="#2e2e2e", fg="#ffffff",
                      relief="flat", insertbackground="#ffffff", width=28, show="•")
    e_rep  = tk.Entry(frm, textvariable=rep_var,  bg="#2e2e2e", fg="#ffffff",
                      relief="flat", insertbackground="#ffffff", width=28, show="•")

    e_user.grid(row=0, column=1, padx=6, pady=6)
    e_new.grid(row=1, column=1, padx=6, pady=6)
    e_rep.grid(row=2, column=1, padx=6, pady=6)
    e_user.focus_set()

    msg_var = tk.StringVar(value="")
    tk.Label(win, textvariable=msg_var, bg="#1e1e1e", fg="#ff8080").pack(pady=(0, 4))

    btns = tk.Frame(win, bg="#1e1e1e")
    btns.pack(pady=6)

    def fechar():
        win.destroy()

    def salvar():
        username = user_var.get().strip()
        nova     = new_var.get()
        repetir  = rep_var.get()

        if not username or not nova or not repetir:
            msg_var.set("Preencha todos os campos.")
            return

        u = find_user(username, only_active=True)
        if not u:
            msg_var.set("Usuário não encontrado ou inativo.")
            return

        if len(nova) < 6:
            msg_var.set("A nova senha deve ter pelo menos 6 caracteres.")
            return

        if nova != repetir:
            msg_var.set("A confirmação não confere com a nova senha.")
            return

        try:
            set_password(username, nova)
            messagebox.showinfo("Sucesso", "Senha alterada com sucesso.")
            win.destroy()
        except Exception as e:
            msg_var.set(f"Erro ao alterar senha: {e}")

    tk.Button(btns, text="Salvar",   command=salvar,  font=("Segoe UI", 10),
              bg="#2e2e2e", fg="#ffffff", relief="flat", padx=12, pady=6).grid(row=0, column=0, padx=6)
    tk.Button(btns, text="Cancelar", command=fechar,  font=("Segoe UI", 10),
              bg="#8b0000", fg="#ffffff", relief="flat", padx=12, pady=6).grid(row=0, column=1, padx=6)

    win.bind("<Return>", lambda e: salvar())
    win.bind("<Escape>", lambda e: fechar())
    centralizar(win, 380, 210)

# === UI de Autenticação (Tkinter) ===
def wizard_primeiro_admin(master):
    """
    Abre um modal para criar o 1º admin se não existir usuarios.json (ou vazio).
    """
    win = tk.Toplevel(master)
    win.title("Criar Administrador")
    win.configure(bg="#1e1e1e")
    win.grab_set()

    tk.Label(win, text="Criar usuário Administrador", font=("Segoe UI", 12, "bold"),
             bg="#1e1e1e", fg="#ffffff").pack(pady=(12,6))

    frm = tk.Frame(win, bg="#1e1e1e"); frm.pack(padx=16, pady=10)
    tk.Label(frm, text="Nome:", bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="e", padx=6, pady=4)
    tk.Label(frm, text="Username:", bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="e", padx=6, pady=4)
    tk.Label(frm, text="Senha:", bg="#1e1e1e", fg="#ffffff").grid(row=2, column=0, sticky="e", padx=6, pady=4)

    nome_var = tk.StringVar(); user_var = tk.StringVar(); pwd_var = tk.StringVar()
    e1 = tk.Entry(frm, textvariable=nome_var, bg="#2e2e2e", fg="#ffffff", relief="flat", insertbackground="#ffffff", width=28)
    e2 = tk.Entry(frm, textvariable=user_var, bg="#2e2e2e", fg="#ffffff", relief="flat", insertbackground="#ffffff", width=28)
    e3 = tk.Entry(frm, textvariable=pwd_var,  bg="#2e2e2e", fg="#ffffff", relief="flat", insertbackground="#ffffff", width=28, show="•")
    e1.grid(row=0, column=1, padx=6, pady=4); e2.grid(row=1, column=1, padx=6, pady=4); e3.grid(row=2, column=1, padx=6, pady=4)
    e1.focus_set()

    def salvar():
        try:
            create_user(user_var.get().strip(), nome_var.get().strip(), pwd_var.get(), role="admin")
            messagebox.showinfo("Sucesso", "Administrador criado. Faça login em seguida.")
            win.destroy()
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao criar administrador:\n{e}")

    tk.Button(win, text="Salvar", command=salvar, font=("Segoe UI", 10),
              bg="#2e2e2e", fg="#ffffff", relief="flat", padx=12, pady=6).pack(pady=(4,12))

    win.wait_window(win)


# --- Utilitário: carrega e redimensiona a foto de perfil para o avatar ---
def _load_user_avatar(path: str, size=(32, 32)):
    """
    Tenta abrir a imagem 'path', redimensiona para 'size' e retorna um PhotoImage.
    Em caso de falha, retorna None.
    """
    if not path or not os.path.exists(path):
        return None
    try:
        img = Image.open(path)
        # Pillow compat: usa LANCZOS quando existir
        resample = getattr(Image, "LANCZOS", Image.BICUBIC)
        img = img.resize(size, resample)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


def login_modal(master) -> dict | None:
    """
    Modal de login (estável, sem flicker):
    - Mostra 1 único logo (sem duplicação).
    - Link 'Esqueci minha senha' (placeholder).
    - Centraliza e usa grab_set (sem transient/topmost).
    """
    # Crie sem master para não depender da visibilidade do root (withdraw)
    win = tk.Toplevel()
    win.title("Entrar")
    win.configure(bg="#1e1e1e")
    win.resizable(False, False)

    result = {"user": None}
    def _cancel():
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", _cancel)

    # --- centralizador sem topmost ---
    def centralizar(janela, w=360, h=300):
        janela.update_idletasks()
        sw = janela.winfo_screenwidth()
        sh = janela.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        janela.geometry(f"{w}x{h}+{x}+{y}")

    # =======================
    #       HEADER / LOGO
    # =======================
    header = tk.Frame(win, bg="#1e1e1e")
    header.pack(fill="x", pady=(12, 6))

    LOGO_LOGIN_SIZE = (100, 100)  # <<< ajuste o tamanho aqui

    logo_img = None
    try:
        im = Image.open(LOGO_PATH)  # usa o mesmo LOGO_PATH do seu arquivo
        # Compatível com Pillow novo/antigo
        try:
            from PIL import Image as _PIL_Image
            resample = getattr(_PIL_Image, "LANCZOS", 1)
        except Exception:
            resample = 1
        im = im.resize(LOGO_LOGIN_SIZE, resample)
        logo_img = ImageTk.PhotoImage(im)
    except Exception as e:
        print("⚠ Falha ao carregar/redimensionar logo:", e)
        logo_img = None

    if logo_img:
        # >>> ÚNICO logo: apenas este Label <<<
        lbl_logo = tk.Label(header, image=logo_img, bg="#1e1e1e")
        lbl_logo.image = logo_img  # evita GC
        lbl_logo.pack()
    else:
        # Se não conseguir carregar o logo, mostra um título simples
        tk.Label(header, text="Entrar", font=("Segoe UI", 12, "bold"),
                 bg="#1e1e1e", fg="#ffffff").pack()

    # =======================
    #        FORM
    # =======================
    frm = tk.Frame(win, bg="#1e1e1e")
    frm.pack(padx=16, pady=2, fill="x")

    tk.Label(frm, text="Username:", bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="e", padx=6, pady=6)
    user_var = tk.StringVar()
    e_user = tk.Entry(frm, textvariable=user_var, bg="#2e2e2e", fg="#ffffff",
                      relief="flat", insertbackground="#ffffff", width=26)
    e_user.grid(row=0, column=1, padx=6, pady=6)

    tk.Label(frm, text="Senha:", bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="e", padx=6, pady=6)
    pwd_var = tk.StringVar()
    e_pwd = tk.Entry(frm, textvariable=pwd_var, bg="#2e2e2e", fg="#ffffff",
                     relief="flat", insertbackground="#ffffff", width=26, show="•")
    e_pwd.grid(row=1, column=1, padx=6, pady=6)

    # Link "Esqueci minha senha" (placeholder)
    def on_esqueci():
        recuperar_senha_modal(master)
    link = tk.Label(frm, text="Esqueci minha senha", bg="#1e1e1e", fg="#00bfff",
                    cursor="hand2", font=("Segoe UI", 9, "underline"))
    link.grid(row=2, column=1, sticky="w", padx=6, pady=(0, 8))
    link.bind("<Button-1>", lambda e: on_esqueci())

    # Mensagens de erro
    msg_var = tk.StringVar()
    tk.Label(win, textvariable=msg_var, bg="#1e1e1e", fg="#ff8080").pack(pady=(2, 0))

    # =======================
    #       BOTÕES
    # =======================
    btns = tk.Frame(win, bg="#1e1e1e")
    btns.pack(pady=10)

    def entrar():
        try:
            u = authenticate(user_var.get().strip(), pwd_var.get())
        except Exception as e:
            msg_var.set(f"Erro: {e}")
            return
        if u:
            result["user"] = u
            win.destroy()
        else:
            msg_var.set("Usuário ou senha inválidos.")

    tk.Button(btns, text="Entrar", command=entrar, font=("Segoe UI", 10),
              bg="#2e2e2e", fg="#ffffff", relief="flat", padx=12, pady=6).grid(row=0, column=0, padx=6)
    tk.Button(btns, text="Cancelar", command=_cancel, font=("Segoe UI", 10),
              bg="#8b0000", fg="#ffffff", relief="flat", padx=12, pady=6).grid(row=0, column=1, padx=6)

    # Exibir centralizado, com foco e grab_set (modal)
    centralizar(win, w=360, h=300)
    win.update_idletasks()
    e_user.focus_set()
    win.grab_set()
    win.focus_force()
    win.wait_window(win)
    return result["user"]

def exigir_login(master):
    """
    1) Se não houver usuários -> abre o wizard do 1º Admin (modal, bloqueante).
    2) Em seguida, abre o login (modal, bloqueante).
    Retorna o usuário autenticado (dict) ou None se cancelado.
    """
    global current_user

    users = load_users()
    if not users:
        # Wizard do 1º admin: só depois do wizard concluído seguimos para o login.
        wizard_primeiro_admin(master)
        users = load_users()
        if not users:
            return None  # usuário fechou o wizard sem criar admin

    user = login_modal(master)
    if user:
        current_user = user
        return current_user
    return None

def gerenciar_usuarios(master):
    if not current_user or current_user.get("role") != "admin":
        messagebox.showwarning("Acesso negado", "Somente administradores podem gerenciar usuários.")
        return

    win = tk.Toplevel(master)
    win.title("Gerenciar Usuários")
    win.configure(bg="#1e1e1e")
    win.geometry("520x380")
    win.grab_set()

    lista = tk.Listbox(win, bg="#2e2e2e", fg="#ffffff")
    lista.pack(fill="both", expand=True, padx=10, pady=(10,6))

    def refresh():
        lista.delete(0, tk.END)
        for u in load_users():
            status = "ativo" if u.get("ativo", True) else "inativo"
            lista.insert(tk.END, f"{u.get('username')} — {u.get('nome')} [{u.get('role')}] ({status})")

    refresh()

    panel = tk.Frame(win, bg="#1e1e1e"); panel.pack(fill="x", padx=10, pady=6)

    def sel_user():
        idx = lista.curselection()
        if not idx: return None
        username = (lista.get(idx[0]).split(" — ")[0]).strip()
        return find_user(username, only_active=False)


    def criar():
        top = tk.Toplevel(win)
        top.title("Novo Usuário")
        top.configure(bg="#1e1e1e")
        top.grab_set()

        # ----------- Labels ------------
        tk.Label(top, text="Username:", bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        tk.Label(top, text="E-mail:",   bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        tk.Label(top, text="Senha:",    bg="#1e1e1e", fg="#ffffff").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        tk.Label(top, text="Perfil:",   bg="#1e1e1e", fg="#ffffff").grid(row=3, column=0, sticky="e", padx=6, pady=4)
        tk.Label(top, text="Foto:",     bg="#1e1e1e", fg="#ffffff").grid(row=4, column=0, sticky="e", padx=6, pady=4)

        # ----------- Vars -------------
        user_var  = tk.StringVar(top)
        email_var = tk.StringVar(top)
        pwd_var   = tk.StringVar(top)
        role_var  = tk.StringVar(top, value="auditor")
        foto_var  = tk.StringVar(top, value="")
        msg_var = tk.StringVar(top)

        # ----------- Entradas ----------
        e_user = tk.Entry(top, textvariable=user_var,  bg="#2e2e2e", fg="#ffffff", relief="flat", width=28)
        e_email = tk.Entry(top, textvariable=email_var, bg="#2e2e2e", fg="#ffffff", relief="flat", width=28)
        e_pwd = tk.Entry(top, textvariable=pwd_var, bg="#2e2e2e", fg="#ffffff", relief="flat", width=28, show="•")
        cb_role = ttk.Combobox(top, textvariable=role_var, values=["auditor","gestor","admin"], width=25)

        e_user.grid(row=0, column=1, padx=6, pady=4)
        e_email.grid(row=1, column=1, padx=6, pady=4)
        e_pwd.grid(row=2, column=1, padx=6, pady=4)
        cb_role.grid(row=3, column=1, padx=6, pady=4)

        # ----------- Foto / Preview -----------
        foto_frame = tk.Frame(top, bg="#1e1e1e")
        foto_frame.grid(row=4, column=1, sticky="w", padx=6, pady=4)

        preview_lbl = tk.Label(foto_frame, bg="#2e2e2e")
        preview_lbl.pack(side="left", padx=5, pady=5)

        def carregar_preview(caminho):
            if not caminho or not os.path.exists(caminho):
                preview_lbl.config(image="", text="(sem imagem)", fg="#cccccc", bg="#2e2e2e")
                preview_lbl.image = None
                return
            try:
                img = Image.open(caminho)
                resample = getattr(Image, "LANCZOS", Image.BICUBIC)
                img = img.resize((96, 96), resample)
                ph = ImageTk.PhotoImage(img)
                preview_lbl.config(image=ph, text="")
                preview_lbl.image = ph
            except:
                preview_lbl.config(text="Prévia indisponível", fg="#cccccc", bg="#2e2e2e")

        def selecionar_foto():
            caminho = filedialog.askopenfilename(
                title="Selecione a foto",
                filetypes=[("Imagens","*.png;*.jpg;*.jpeg;*.gif;*.bmp"),("Todos","*.*")]
            )
            if caminho:
                foto_var.set(caminho)
                carregar_preview(caminho)

        tk.Button(foto_frame, text="Selecionar...", command=selecionar_foto,
                bg="#2e2e2e", fg="#ffffff", relief="flat", padx=10, pady=6
        ).pack(side="left", padx=8)

        # mensagem de erro
        msg_var = tk.StringVar(top)
        tk.Label(top, textvariable=msg_var, bg="#1e1e1e", fg="#ff8080").grid(row=5, column=0, columnspan=2)

        # ----------- Salvar / Cancelar -----------
        btn_frame = tk.Frame(top, bg="#1e1e1e")
        btn_frame.grid(row=6, column=0, columnspan=2, pady=12)

        def salvar():
            username = user_var.get().strip()
            email = email_var.get().strip()
            senha = pwd_var.get()
            perfil = role_var.get().strip()
            foto_caminho = foto_var.get().strip()

            if not username or not senha or not email:
                msg_var.set("Preencha username, e-mail e senha.")
                return
            if len(senha) < 6:
                msg_var.set("A senha deve ter ao menos 6 caracteres.")
                return
            if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                msg_var.set("E-mail inválido.")
                return

            try:
                create_user(
                    username=username,
                    senha=senha,
                    email=email,
                    role=perfil,
                    nome=username,
                    foto=foto_caminho
                )
                refresh()
                top.destroy()
            except Exception as e:
                messagebox.showerror("Erro", str(e))

        tk.Button(
            btn_frame, text="OK", command=salvar,
            bg="#2e2e2e", fg="#ffffff", relief="flat", padx=16, pady=6
        ).pack(side="right", padx=6)

        tk.Button(
            btn_frame, text="Voltar", command=top.destroy,
            bg="#8b0000", fg="#ffffff", relief="flat", padx=16, pady=6
        ).pack(side="right", padx=6)

        e_user.focus_set()
        carregar_preview(foto_var.get())

    def editar():
        u = sel_user()
        if not u:
            messagebox.showwarning("Atenção", "Selecione um usuário na lista para editar.")
            return

        top = tk.Toplevel(win)
        top.title(f"Editar Usuário — {u.get('username')}")
        top.configure(bg="#1e1e1e")
        top.grab_set()

        # ---------- Labels ----------
        tk.Label(top, text="Username:", bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        tk.Label(top, text="E-mail:",   bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        tk.Label(top, text="Nova Senha:", bg="#1e1e1e", fg="#ffffff").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        tk.Label(top, text="Perfil:",   bg="#1e1e1e", fg="#ffffff").grid(row=3, column=0, sticky="e", padx=6, pady=4)
        tk.Label(top, text="Foto:",     bg="#1e1e1e", fg="#ffffff").grid(row=4, column=0, sticky="e", padx=6, pady=4)

        # ---------- Vars ----------
        user_var  = tk.StringVar(top, value=u.get("username",""))
        email_var = tk.StringVar(top, value=u.get("email","") or "")
        pwd_var   = tk.StringVar(top, value="")  # em branco = manter senha
        role_var  = tk.StringVar(top, value=(u.get("role") or "auditor"))
        foto_var  = tk.StringVar(top, value=u.get("foto","") or "")
        msg_var   = tk.StringVar(top, value="")

        # ---------- Inputs ----------
        e_user  = tk.Entry(top, textvariable=user_var,  bg="#2e2e2e", fg="#ffffff", relief="flat", width=28)
        e_email = tk.Entry(top, textvariable=email_var, bg="#2e2e2e", fg="#ffffff", relief="flat", width=28)
        e_pwd   = tk.Entry(top, textvariable=pwd_var,   bg="#2e2e2e", fg="#ffffff", relief="flat", width=28, show="•")
        cb_role = ttk.Combobox(top, textvariable=role_var, values=["auditor","gestor","admin"], width=25)

        e_user.grid(row=0, column=1, padx=6, pady=4)
        e_email.grid(row=1, column=1, padx=6, pady=4)
        e_pwd.grid(row=2, column=1, padx=6, pady=4)
        cb_role.grid(row=3, column=1, padx=6, pady=4)

        # ---------- Foto / Preview ----------
        foto_frame = tk.Frame(top, bg="#1e1e1e")
        foto_frame.grid(row=4, column=1, sticky="w", padx=6, pady=4)

        preview_lbl = tk.Label(foto_frame, bg="#2e2e2e")
        preview_lbl.pack(side="left", padx=5, pady=5)

        def carregar_preview(caminho):
            if not caminho or not os.path.exists(caminho):
                preview_lbl.config(image="", text="(sem imagem)", fg="#cccccc", bg="#2e2e2e")
                preview_lbl.image = None
                return
            try:
                img = Image.open(caminho)
                resample = getattr(Image, "LANCZOS", Image.BICUBIC)
                img = img.resize((96, 96), resample)
                ph = ImageTk.PhotoImage(img)
                preview_lbl.config(image=ph, text="")
                preview_lbl.image = ph
            except Exception:
                preview_lbl.config(text="Prévia indisponível", fg="#cccccc", bg="#2e2e2e")

        def selecionar_foto():
            caminho = filedialog.askopenfilename(
                title="Selecione a foto",
                filetypes=[("Imagens","*.png;*.jpg;*.jpeg;*.gif;*.bmp"),("Todos","*.*")]
            )
            if caminho:
                foto_var.set(caminho)
                carregar_preview(caminho)

        tk.Button(foto_frame, text="Selecionar...", command=selecionar_foto,
                bg="#2e2e2e", fg="#ffffff", relief="flat", padx=10, pady=6
        ).pack(side="left", padx=8)

        carregar_preview(foto_var.get())

        # ---------- Mensagens ----------
        tk.Label(top, textvariable=msg_var, bg="#1e1e1e", fg="#ff8080").grid(row=5, column=0, columnspan=2)

        # ---------- Botões ----------
        btn_frame = tk.Frame(top, bg="#1e1e1e")
        btn_frame.grid(row=6, column=0, columnspan=2, pady=12)

        def salvar_edicao():
            new_username = user_var.get().strip()
            email = email_var.get().strip()
            nova_senha = pwd_var.get()  # vazio = não trocar
            perfil = role_var.get().strip()
            foto_caminho = foto_var.get().strip()
            # nome: se quiser expor campo Nome depois, basta incluir; aqui usamos username
            nome = new_username

            if not new_username:
                msg_var.set("Informe o username.")
                return
            if email and not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                msg_var.set("E-mail inválido.")
                return
            if nova_senha and len(nova_senha) < 6:
                msg_var.set("A nova senha deve ter pelo menos 6 caracteres.")
                return

            try:
                update_user(
                    orig_username=u.get("username"),
                    new_username=new_username,
                    email=email,
                    role=perfil,
                    nome=nome,
                    nova_senha=nova_senha if len(nova_senha) > 0 else None,
                    foto=foto_caminho
                )
                refresh()
                # Se o usuário editado é o usuário logado, atualiza sessão/head
                global current_user
                if current_user and current_user.get("username","").lower() == u.get("username","").lower():
                    current_user = find_user(new_username)  # recarrega o atualizado
                    try:
                        construir_menu_principal()  # recarrega avatar/topo
                    except Exception:
                        pass
                top.destroy()
            except Exception as e:
                messagebox.showerror("Erro", str(e))

        tk.Button(
            btn_frame, text="OK", command=salvar_edicao,
            bg="#2e2e2e", fg="#ffffff", relief="flat", padx=16, pady=6
        ).pack(side="right", padx=6)

        tk.Button(
            btn_frame, text="Voltar", command=top.destroy,
            bg="#8b0000", fg="#ffffff", relief="flat", padx=16, pady=6
        ).pack(side="right", padx=6)

        e_user.focus_set()


    def trocar_senha():
        u = sel_user()
        if not u: return
        top = tk.Toplevel(win); top.title(f"Trocar senha — {u.get('username')}"); top.configure(bg="#1e1e1e"); top.grab_set()
        tk.Label(top, text="Nova senha:", bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        pwd = tk.StringVar()
        e = tk.Entry(top, textvariable=pwd, bg="#2e2e2e", fg="#ffffff", relief="flat", insertbackground="#ffffff", width=28, show="•"); e.grid(row=0, column=1, padx=6, pady=6)
        e.focus_set()
        tk.Button(top, text="Salvar", command=lambda:(set_password(u["username"], pwd.get()), refresh(), top.destroy()),
                  bg="#2e2e2e", fg="#ffffff", relief="flat", padx=10, pady=6).grid(row=1, column=1, sticky="e", pady=8)

    def ativar_desativar():
        u = sel_user()
        if not u: return
        u["ativo"] = not u.get("ativo", True)
        save_users(usuarios_cache); refresh()

    tk.Button(panel, text="Novo Usuário", command=criar, bg="#2e2e2e", fg="#ffffff", relief="flat", padx=10, pady=6).pack(side="left", padx=4)
    tk.Button(panel, text="Trocar Senha", command=trocar_senha, bg="#2e2e2e", fg="#ffffff", relief="flat", padx=10, pady=6).pack(side="left", padx=4)
    tk.Button(panel, text="Ativar/Desativar", command=ativar_desativar, bg="#2e2e2e", fg="#ffffff", relief="flat", padx=10, pady=6).pack(side="left", padx=4)
    tk.Button(panel, text="Fechar", command=win.destroy, bg="#8b0000", fg="#ffffff", relief="flat", padx=10, pady=6).pack(side="right", padx=4)
    tk.Button(panel, text="Editar Usuário", command=editar,
            bg="#2e2e2e", fg="#ffffff", relief="flat", padx=10, pady=6
    ).pack(side="left", padx=4)

CFOPS_VALIDOS   = {"5.102", "5.403", "5.405", "5.646", "5.655", "5.656", "6.108", "6.403", "6.404", "7.102"}
CFOPS_EXCLUIDOS = {"5.409"} 

def normalizar(valor):
    valor = re.sub(r"[^0-9-]", "", valor)
    return valor.lstrip("0") or "0"


# --- ÁREA DE CONTEÚDO GLOBAL (abaixo do header) ---
menu_content_area = None
_show_main_menu_func = None


def _clear_menu_content():
    """Remove tudo que estiver atualmente no content area."""
    global menu_content_area
    if not menu_content_area or not menu_content_area.winfo_exists():
        return
    for w in menu_content_area.winfo_children():
        try:
            w.destroy()
        except Exception:
            try:
                w.pack_forget()
            except Exception:
                pass


def canonicalizar(serie, numero):
    def clean(x):
        x = re.sub(r"[^\d-]", "", str(x))
        x = x.split("-")[0]  
        return x
    try:
        s = str(int(clean(serie)))
        n = str(int(clean(numero)))
        return (s, n)
    except:
        return None

def eh_numero(t):
    return re.fullmatch(r"\d+", t) is not None

def linha_parece_nota(tokens):
    if len(tokens) < 5: return False
    if not eh_numero(tokens[0]): return False
    if sum(1 for t in tokens[:5] if eh_numero(t)) < 2: return False
    if not any(t in CFOPS_VALIDOS for t in tokens):
        return False
    return True


def extrair_valor_linha_livro(tokens):
    """
    Retorna EXATAMENTE o valor contábil conforme o layout RS P2/A:
    é sempre o valor monetário imediatamente antes do CFOP.

    Correção: quando o mesmo texto do CFOP também aparece no "NÚMERO"
    (ex.: cupom 5.405 e CFOP 5.405), usamos a ocorrência do CFOP que
    está mais próxima de 'ICMS' (ou, na falta, a última ocorrência do token).
    """
    def eh_moeda(s):
        if not s:
            return False
        s = str(s).strip()
        return bool(
            re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}", s)
            or re.fullmatch(r"[0-9]+[.,][0-9]{2}", s)
        )

    # localizar o CFOP real (já ignora colunas de número/série)
    cfop_tok = extrair_cfop(tokens)
    if cfop_tok is None:
        return None

    # 1) Tentar achar o CFOP correto "colado" ao ICMS (busca regressiva a partir do ICMS)
    cfop_idx = None
    if "ICMS" in tokens:
        # posição do último 'ICMS' (algumas linhas podem ter mais de um)
        icms_pos = len(tokens) - 1 - tokens[::-1].index("ICMS")
        # janela regressiva curta (8 tokens) até encontrar o CFOP desejado
        start = max(0, icms_pos - 8)
        for i in range(icms_pos, start - 1, -1):
            if tokens[i] == cfop_tok:
                cfop_idx = i
                break

    # 2) Fallback: se não encontrou via ICMS, usa a ÚLTIMA ocorrência do token do CFOP
    if cfop_idx is None:
        for i in range(len(tokens) - 1, -1, -1):
            if tokens[i] == cfop_tok:
                cfop_idx = i
                break

    if cfop_idx is None:
        return None

    # 3) Valor CONTÁBIL é exatamente o token monetário anterior ao CFOP escolhido
    for j in range(cfop_idx - 1, -1, -1):
        if eh_moeda(tokens[j]):
            return tokens[j]

    # 4) Se ainda não encontrou, não inventa: retorna None
    return None

def extrair_data(tokens):
    for t in tokens:
        if re.match(r"\d{2}[/\-]\d{2}[/\-]\d{4}", t) or re.match(r"\d{4}[/\-]\d{2}[/\-]\d{2}", t):
            return t
    return None

def parse_valor(valor_str):
    if not valor_str: return 0.0
    v = valor_str.replace(".", "").replace(",", ".")
    try: return float(v)
    except: return 0.0


def extrair_cfop(tokens):
    """
    Extrai o CFOP real ignorando colunas de NÚMERO, DIA, SERIE etc.
    O CFOP verdadeiro sempre aparece:
       - após 'ICMS', ou
       - nos últimos tokens da linha (layout comum do livro)
    Assim, elimina a chance do número da nota ser confundido com CFOP.
    """
    # 1) CFOP normalmente vem logo após ICMS
    if "ICMS" in tokens:
        idx = tokens.index("ICMS")
        # examina os próximos 6 tokens
        for t in tokens[idx:idx+6]:
            if t in CFOPS_VALIDOS or t in CFOPS_EXCLUIDOS:
                return t

    # 2) busca CFOP somente na "cauda" da linha
    for t in tokens[-6:]:
        if t in CFOPS_VALIDOS or t in CFOPS_EXCLUIDOS:
            return t

    return None


def extrair_livro(pdf_path):
    documentos = {}
    if not os.path.exists(pdf_path): return documentos
    with pdfplumber.open(pdf_path) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if not texto: continue
            for linha in texto.splitlines():
                tokens = linha.split()
               
                cfop = extrair_cfop(tokens)

                if cfop is None:
                    continue

                if cfop in CFOPS_EXCLUIDOS:
                    continue

                tokens_upper = [t.upper() for t in tokens]
                if any(t in tokens_upper for t in ["NFCE", "NFE", "NFS", "MFE"]):
                    for i, t in enumerate(tokens_upper):
                        if t in ["NFCE", "NFE", "NFS", "MFE"] and i+2 < len(tokens):
                            serie, numero = tokens[i+1], tokens[i+2]
                            par = canonicalizar(serie, numero)
                            if par:
                                valor = extrair_valor_linha_livro(tokens)
                                if not valor:
                                    continue 
                                data = extrair_data(tokens)
                                documentos[par] = {
                                    "tipo": "Livro",
                                    "valor": valor,
                                    "data": data,
                                    "fiscal": t
                                }
                            break
    return documentos

def extrair_livro_como_lista(pdf_path):
    registros = []
    if not os.path.exists(pdf_path): return registros
    with pdfplumber.open(pdf_path) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if not texto: continue
            for linha in texto.splitlines():
                tokens = linha.split()

                cfop = extrair_cfop(tokens)

                if cfop is None:
                    continue

                if cfop in CFOPS_EXCLUIDOS:
                    continue

                tokens_upper = [t.upper() for t in tokens]
                if any(t in tokens_upper for t in ["NFCE","NFE","NFS","MFE"]):
                    for i,t in enumerate(tokens_upper):
                        if t in ["NFCE","NFE","NFS","MFE"] and i+2 < len(tokens):
                            serie, numero = tokens[i+1], tokens[i+2]
                            valor = extrair_valor_linha_livro(tokens)
                            if not valor:
                                break  
                            registros.append({
                                "serie": serie,
                                "numero": numero,
                                "fiscal": t,
                                "valor_float": parse_valor(valor),
                                "data": extrair_data(tokens)
                            })
                            break
    return registros

def extrair_cupons(pdf_path):
    documentos = {}
    if not os.path.exists(pdf_path):
        return documentos
    CANCEL_KEYS = ("CANCEL", "CANCELADO", "CANCELADA", "CANC.", "CANC")
    TOTAL_KEYS  = ("TOTAL", "TOTAIS")
    VENDA_KEYS  = ("VENDA PIX", "VENDA A CARTAO", "VENDA DINHEIRO", "DESCRICAO TOTAIS")

    with pdfplumber.open(pdf_path) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if not texto:
                continue
            for linha in texto.splitlines():

                tokens = linha.split()
                if any(t in CFOPS_EXCLUIDOS for t in tokens):
                    continue
                if not any(t in CFOPS_VALIDOS for t in tokens):
                    continue

                up = linha.upper()
                if any(key in up for key in CANCEL_KEYS):  continue
                if any(key in up for key in TOTAL_KEYS):   continue
                if any(key in up for key in VENDA_KEYS):   continue
                if "NORMAL" not in up:                     continue
                numeros = re.findall(r"\d+", linha)
                if len(numeros) < 3:                       continue

                tokens = linha.split()
                cupom = numeros[0]
                impr  = numeros[2]
                par = canonicalizar(impr, cupom)
                if par:
                    def eh_moeda(s):
                        return (
                            re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}", s) or
                            re.fullmatch(r"[0-9]+[.,][0-9]{2}", s)
                        )
                    val = None
                    for j in range(len(tokens)-1, -1, -1):
                        if eh_moeda(tokens[j]):
                            val = tokens[j]
                            break
                    if not val:
                        continue
                    data = extrair_data(tokens)
                    documentos[par] = {"tipo": "Cupom", "valor": val, "data": data}
    return documentos

def extrair_notas(pdf_path):
    documentos = {}
    if not os.path.exists(pdf_path):
        return documentos

    def eh_moeda_any(s):
        if not s:
            return False
        s = str(s).strip()
        return bool(re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}", s) or
                    re.fullmatch(r"[0-9]+[.,][0-9]{2}", s))

    with pdfplumber.open(pdf_path) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            if not texto:
                continue

            for linha in texto.splitlines():
                up = linha.upper().strip()
                if not up:
                    continue
                if up.startswith("VENDA") or up.startswith("CODPOS") or up.startswith("TOTAL"):
                    continue

                tokens_raw = linha.split()
                if any(t in CFOPS_EXCLUIDOS for t in tokens_raw):
                    continue
                if not any(t in CFOPS_VALIDOS for t in tokens_raw):
                    continue

                tokens = [t.strip().strip(".,;:/\\()[]") for t in tokens_raw]
                tokens_upper = [t.upper() for t in tokens]
                if len(tokens) < 2:
                    continue

                tokens_norm = []
                for t in tokens:
                    m = re.match(r"^([A-Za-z\-]+[A-Za-z0-9]*)(\d{1,3}(?:\.\d{3})*,\d{2})$", t)
                    if m:
                        tokens_norm.append(m.group(1))
                        tokens_norm.append(m.group(2))
                    else:
                        tokens_norm.append(t)
                tokens = tokens_norm

                recombined = []
                i = 0
                while i < len(tokens):
                    t = tokens[i]
                    if i + 1 < len(tokens):
                        nxt = tokens[i + 1]
                        if re.fullmatch(r"\d{1,3},\d{2}", nxt) and re.fullmatch(r"\d{1,3}", t):
                            merged = (t + nxt).replace(".", "")
                            recombined.append(merged)
                            i += 2
                            continue
                    recombined.append(t)
                    i += 1
                tokens = recombined
                tokens_upper = [t.upper() for t in tokens]
         
                cond = None
                for t in tokens_upper:
                    if re.fullmatch(r'GOOD[A-Z]*', t):
                        cond = "GOOD"
                        break

                nota = tokens[0] if len(tokens) > 0 else None
                serie = tokens[1] if len(tokens) > 1 else None
                if not nota or not serie or not re.fullmatch(r"\d{6}", str(nota)) or not re.fullmatch(r"\d{3}", str(serie)):
                    nums = [t for t in tokens if re.fullmatch(r"\d+", t)]
                    if len(nums) >= 2:
                        nota = nums[0]
                        serie = nums[1]
                    else:
                        continue

                valor_liquido = None

                def eh_moeda(s):
                    return (re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}", s) or
                            re.fullmatch(r"[0-9]+[.,][0-9]{2}", s))

                forma_idx = None
                for i, t in enumerate(tokens):
                    if re.fullmatch(r"[A-Z]{2,}\-?[A-Z0-9]*", t):
                        forma_idx = i
                        break
                if forma_idx is not None:
                    for j in range(forma_idx + 1, len(tokens)):
                        if eh_moeda(tokens[j]) and parse_valor(tokens[j]) > 0:
                            valor_liquido = tokens[j]
                            break

                if not valor_liquido:
                    cfop_idx = None
                    for i, t in enumerate(tokens):
                        if t in CFOPS_VALIDOS:
                            cfop_idx = i
                            break
                    if cfop_idx is not None:
                        valores_antes = [
                            (parse_valor(tokens[j]), tokens[j])
                            for j in range(cfop_idx - 1, -1, -1)
                            if eh_moeda(tokens[j]) and parse_valor(tokens[j]) > 0
                        ]
                        if valores_antes:
                            valores_antes.sort(reverse=True)
                            valor_liquido = valores_antes[0][1]

                if not valor_liquido:
                    for j in range(len(tokens) - 1, -1, -1):
                        if eh_moeda(tokens[j]) and parse_valor(tokens[j]) > 0:
                            valor_liquido = tokens[j]
                            break

                if not valor_liquido:
                    continue

                par = canonicalizar(serie, nota)
                if not par:
                    continue

                documentos[par] = {
                    "tipo": "Nota",
                    "valor": valor_liquido,
                    "data": extrair_data(tokens),
                    "cond": cond,
                }

    return documentos

def extrair_prazo(pdf_path):
    documentos = {}
    if not os.path.exists(pdf_path):
        return documentos

    pad = re.compile(
        r'(?:^|\s)'                         
        r'(\d{1,2})\s+'                     
        r'(\d{4,6})\s+'                     
        r'(\d{1,3})\s+'                     
        r'.*?'                             
        r'([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})'  
        r'(?:\s+[Ss])?\b'                   
    )

    def canonicalizar(serie, nota):
        return (str(serie).strip(), str(nota).strip())

    with pdfplumber.open(pdf_path) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            for linha in texto.splitlines():
                u = (linha or "").upper().strip()

                # Ignorar SÓ cabeçalhos/rodapés de resumo que começam por "TOTAL",
                # ou linhas bem conhecidas do fechamento.
                eh_total_resumo = (
                    u.startswith("TOTAL") or                      # "TOTAL ..."
                    u.startswith("TOTAL FILIAL") or
                    u.startswith("TOTAL GERAL") or
                    u.startswith("TOTAL EMPRESA") or
                    u.startswith("TOTAL SALDO") or
                    "TOTAL FILIAL" in u or
                    "TOTAL GERAL" in u or
                    u.startswith("CLIENTE TOTAL")                 # bloco de consolidação por cliente
                )

                if eh_total_resumo:
                    continue

                m = pad.search(linha)
                if not m:
                    continue

                filial, nota, serie, valor = m.groups()
                par = (str(serie).strip(), str(nota).strip())
                documentos[par] = {
                    "tipo": "Prazo",
                    "valor": valor,
                    "data": None
                }

    return documentos

def iniciar_analise_contabil():
    janela = tk.Toplevel(root)
    janela.title("Contabilidade Detalhada")
    janela.configure(bg="#1e1e1e")
    if icone:
        janela.iconphoto(False, icone)
    janela.transient(root)

    def selecionar_arquivo(entry):
        caminho = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx *.xls")])
        if caminho:
            entry.delete(0, tk.END)
            entry.insert(0, caminho)

    frame = tk.Frame(janela, bg="#1e1e1e")
    frame.pack(padx=20, pady=20)

    tk.Label(frame, text="Razão Modelo I - Intervalo de Contas:", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="w")
    entry_arquivo = tk.Entry(frame, font=("Segoe UI", 10), width=40)
    entry_arquivo.grid(row=0, column=1, sticky="w", padx=10)
    tk.Button(frame, text="Selecionar", command=lambda: selecionar_arquivo(entry_arquivo),
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=2, sticky="w", padx=5)


    def executar():
        total_nota_vista = 0.0
        total_nota_prazo = 0.0
        try:
            caminho = entry_arquivo.get()
            if not caminho:
                messagebox.showwarning("Aviso", "Selecione o arquivo antes de executar.")
                return

            df = pd.read_excel(caminho, header=None)
            
            COL_DATA = 0
            COL_DOC = 4
            COL_HISTORICO = 5
            COL_CONTA_PARTIDA = 7
            COL_DEBITO = 8
            COL_CREDITO = 9
            HIDE_DEVOLUCAO_NA_LISTAGEM = False

            formas_pagamento = {
                6:    "Dinheiro",
                11:   "Pix QrCode",
                2074: "Pix Maquineta",
                1698: "Venda a Prazo",
                1022: "Coligadas",
                2157: "Cartão",
                1253: "GoodCard",
                1179: "Cartão Link",
                989:  "Depósito",
                1677: "Nota Fiscal Depósito",
                1575: "Transitórias",
            }

            contas_vista  = [417, 419, 421, 422]
            contas_prazo  = [418, 420, 430]
            conta_devol   = 446 

            ordem_formas = [
                "Dinheiro", "Pix QrCode", "Pix Maquineta", "Venda a Prazo", "Coligadas",
                "Cartão", "GoodCard", "Cartão Link", "Depósito", "Nota Fiscal Depósito",
                "Devolução",
            ]

            blocos = {} 
            bloco_atual = None
            conta_bloco_atual = None
            valores_por_forma = {}

            resumo_por_tipo = {'À Vista': 0.0, 'À Prazo': 0.0, 'Devolução': 0.0}
            total_cupom = 0.0
            total_nota = 0.0
            totais_por_forma = {}
            resumo_por_dia = {}

            def extrair_num_bloco(texto):
                if not texto:
                    return None
                t = str(texto).upper()
                for padrao in [r'\((\d{3,4})\)', r'CONTA\s*(\d{3,4})', r'\b(\d{3,4})\s*-\s*']:
                    m = re.search(padrao, t)
                    if m:
                        try:
                            return int(m.group(1))
                        except:
                            pass
                return None

            def extrair_conta_partida(celula):
                s = str(celula) if not pd.isna(celula) else ""
                m = re.search(r'^\s*\d+\s*-\s*(\d+)\s*$', s)
                if m:
                    try:
                        return int(m.group(1))
                    except:
                        pass
                m2 = re.search(r'(\d+)', s)
                if m2:
                    try:
                        return int(m2.group(1))
                    except:
                        pass
                return None

            def classificar_doc(doc_raw):
                """Define Cupom/Nota a partir do DOC.NRO.; aceita formatos como '6982/920'."""
                tokens = re.findall(r'\d+', str(doc_raw))
                if tokens:
                    try:
                        doc_num = int(tokens[-1])  # pega o último grupo numérico
                        return "Cupom Fiscal" if 1 <= doc_num <= 100 else "Nota Fiscal"
                    except:
                        pass
                return "Documento Desconhecido"

            for _, row in df.iterrows():
                texto_bloco_candidato = (
                    ("" if pd.isna(row[COL_CONTA_PARTIDA]) else str(row[COL_CONTA_PARTIDA])) + " " +
                    ("" if pd.isna(row[COL_HISTORICO]) else str(row[COL_HISTORICO]))
                ).upper()

                if any(x in texto_bloco_candidato for x in ["VENDA", "DEVOLU", "CONTA "]):
                    num_bloco = extrair_num_bloco(texto_bloco_candidato)
                    if num_bloco in (417, 418, 419, 420, 421, 422, 430, 446):
                        if bloco_atual is not None and valores_por_forma:
                            blocos[bloco_atual] = {"formas": valores_por_forma, "conta": conta_bloco_atual}
                        bloco_atual = texto_bloco_candidato.strip()
                        conta_bloco_atual = num_bloco
                        valores_por_forma = {}
                        continue 

                if bloco_atual is not None:
                    cp = "" if pd.isna(row[COL_CONTA_PARTIDA]) else str(row[COL_CONTA_PARTIDA]).upper()
                    if "TOTAL DEB" in cp or "TOTAL CRED" in cp:
                        continue

                    # Débito para bloco de devolução; caso contrário, Crédito
                    if conta_bloco_atual == conta_devol:
                        valor = pd.to_numeric(row[COL_DEBITO], errors='coerce')
                    else:
                        valor = pd.to_numeric(row[COL_CREDITO], errors='coerce')
                    if pd.isna(valor) or float(valor) == 0.0:
                        continue
                    valor = float(valor)

                    conta_partida = extrair_conta_partida(row[COL_CONTA_PARTIDA])

                    # -------- FIX #1: FORMA = "Cartão" para 2157/1253/1179 --------
                    if conta_partida in (2157, 1253, 1179):
                        forma = "Cartão"
                    else:
                        if conta_partida in (2157, 1253, 1179):
                            forma = "Cartão"
                        else:
                            forma = formas_pagamento.get(
                                conta_partida,
                                "Reembolso Manaus" if bloco_atual == 446 else "Outros"
                            )


                    # -------- FIX #2: TIPO = "Nota Fiscal" para 1253/1179 --------
                    # (demais contas usam a sua regra original classificar_doc)
                    if conta_partida in (1253, 1179):
                        tipo_doc = "NFS"
                    else:
                        try:
                            doc_num = int(doc_raw)  # ou use sua classificar_doc se preferir
                            tipo_doc = "NFCE" if 1 <= doc_num <= 100 else "NFS"
                        except Exception:
                            tipo_doc = "NFS"


                    # ------ mantém SUA exibição/estrutura ------
                    chave = f"{forma} ({tipo_doc})"
                    valores_por_forma[chave] = valores_por_forma.get(chave, 0.0) + valor

                    if not (HIDE_DEVOLUCAO_NA_LISTAGEM and forma == "Devolução"):
                        totais_por_forma[chave] = totais_por_forma.get(chave, 0.0) + valor

                    if conta_bloco_atual != conta_devol:
                        if tipo_doc == "Cupom Fiscal":
                            total_cupom += valor
                        elif tipo_doc == "Nota Fiscal":
                            total_nota += valor
                            if conta_bloco_atual in contas_vista:
                                total_nota_vista += valor
                            elif conta_bloco_atual in contas_prazo:
                                total_nota_prazo += valor

                    data_raw = str(row[COL_DATA]).strip()
                    try:
                        data_corrigida = re.sub(r"[^0-9]", "/", data_raw)
                        data_com_ano = f"{data_corrigida}/2025"
                        data_formatada = pd.to_datetime(data_com_ano, dayfirst=True).strftime('%d/%m')
                    except:
                        data_formatada = "Desconhecido"

                    if data_formatada not in resumo_por_dia:
                        resumo_por_dia[data_formatada] = {}
                    resumo_por_dia[data_formatada][chave] = resumo_por_dia[data_formatada].get(chave, 0.0) + valor

            if bloco_atual is not None and valores_por_forma:
                blocos[bloco_atual] = {"formas": valores_por_forma, "conta": conta_bloco_atual}

            # ======= SUA EXIBIÇÃO MANTIDA =======
            saida.delete(1.0, tk.END)
            saida.insert(tk.END, "\nResumo por Bloco e Forma de Pagamento:\n")

            for nome_bloco, info in blocos.items():
                formas = info["formas"]
                conta_bloco = info["conta"]

                saida.insert(tk.END, f"\n{nome_bloco}:\n")
                total_bloco = 0.0
                for forma, total in formas.items():
                    saida.insert(tk.END, f" - {forma}: R$ {total:,.2f}\n")
                    total_bloco += total
                saida.insert(tk.END, f"Total: R$ {total_bloco:,.2f}\n")

                if conta_bloco in contas_vista:
                    resumo_por_tipo['À Vista'] += total_bloco
                elif conta_bloco in contas_prazo:
                    resumo_por_tipo['À Prazo'] += total_bloco
                elif conta_bloco == conta_devol:
                    resumo_por_tipo['Devolução'] += total_bloco

            saida.insert(tk.END, "\nTotais por Tipo de Venda:\n")
            saida.insert(tk.END, f"À Vista: R$ {resumo_por_tipo['À Vista']:,.2f}\n")
            saida.insert(tk.END, f"À Prazo: R$ {resumo_por_tipo['À Prazo']:,.2f}\n")
            saida.insert(tk.END, f"Devolução: R$ {resumo_por_tipo['Devolução']:,.2f}\n")
            saida.insert(
                tk.END,
                f"Total Geral (exceto Devolução): R$ {(resumo_por_tipo['À Vista'] + resumo_por_tipo['À Prazo']):,.2f}\n"
            )

            saida.insert(tk.END, "\nTotais por Tipo de Documento:\n")
            saida.insert(tk.END, f"Total Cupom Fiscal: R$ {total_cupom:,.2f}\n")
            saida.insert(tk.END, f"Nota Fiscal à Vista: R$ {total_nota_vista:,.2f}\n")
            saida.insert(tk.END, f"Nota Fiscal a Prazo: R$ {total_nota_prazo:,.2f}\n")
            saida.insert(tk.END, f"Total Nota Fiscal: R$ {total_nota:,.2f}\n")
            saida.insert(tk.END, "\nTotais por Forma de Pagamento (separados por tipo de documento):\n")
            for forma_base in ordem_formas:
                for chave, total in sorted(totais_por_forma.items()):
                    if chave.startswith(forma_base):
                        saida.insert(tk.END, f"{chave}: R$ {total:,.2f}\n")

            saida.insert(tk.END, "\nResumo por Dia:\n")
            dias_ordenados = sorted(
                resumo_por_dia.keys(),
                key=lambda d: datetime.strptime(d, "%d/%m") if d != "Desconhecido" else datetime(1900, 1, 1)
            )
            for dia in dias_ordenados:
                formas = resumo_por_dia[dia]
                saida.insert(tk.END, f"\nDia {dia}:\n")
                total_dia = 0.0
                total_cupom_dia = 0.0
                total_nota_dia = 0.0
                for forma, valor in formas.items():
                    saida.insert(tk.END, f" - {forma}: R$ {valor:,.2f}\n")
                    total_dia += valor
                    fl = forma.lower()
                    if "cupom fiscal" in fl:
                        total_cupom_dia += valor
                    elif "nota fiscal" in fl:
                        total_nota_dia += valor
                saida.insert(tk.END, f" Total Cupom Fiscal: R$ {total_cupom_dia:,.2f}\n")
                saida.insert(tk.END, f" Total Nota Fiscal : R$ {total_nota_dia:,.2f}\n")
                saida.insert(tk.END, f" Total Geral do Dia: R$ {total_dia:,.2f}\n")

        except Exception as e:
            messagebox.showerror("Erro", str(e))



def iniciar_conciliacao():
    janela = tk.Toplevel(root)
    janela.title("Livro Fiscal x Relatórios")
    janela.configure(bg="#1e1e1e")
    if icone:
        janela.iconphoto(False, icone)
    janela.transient(root)

    def selecionar_arquivo(entry):
        caminho = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if caminho:
            entry.delete(0, tk.END)
            entry.insert(0, caminho)

    def executar():
        try:
            livro = entry_livro.get()
            cupons = entry_cupons.get()
            notas = entry_notas.get()
            prazo = entry_prazo.get()
            if not all([livro, cupons, notas, prazo]):
                messagebox.showwarning("Aviso", "Selecione todos os arquivos antes de executar.")
                return

            registros_livro = extrair_livro_como_lista(livro) 
            docs_cupons = extrair_cupons(cupons)
            docs_notas = extrair_notas(notas)      
            docs_prazo = extrair_prazo(prazo)

            docs_outros = {**docs_cupons, **docs_notas, **docs_prazo}
            docs_outros = {k: v for k, v in docs_outros.items() if isinstance(k, tuple) and len(k) >= 2}

            def normalizar_numero(n):
                n = str(n)
                n = n.replace(".", "").replace(",", "").strip()
                return n.lstrip("0") or "0"

            def agrupar_por_numero_lista(registros):
                agrupado = {}
                mapa_series = {}
                for r in registros:
                    numero = normalizar_numero(r.get("numero"))
                    serie = str(r.get("serie")).strip()
                    valor = r.get("valor_float", 0.0)
                    agrupado[numero] = agrupado.get(numero, 0.0) + valor
                    if numero not in mapa_series:
                        mapa_series[numero] = serie
                return agrupado, mapa_series

            def agrupar_por_numero_dict(docs):
                agrupado = {}
                for key, meta in docs.items():
                    if isinstance(key, tuple) and len(key) >= 2:
                        numero = normalizar_numero(key[1])
                    else:
                        continue
                    valor = parse_valor(meta.get("valor"))
                    agrupado[numero] = agrupado.get(numero, 0.0) + valor
                return agrupado

            livro_agregado, mapa_series = agrupar_por_numero_lista(registros_livro)
            outros_agregado = agrupar_por_numero_dict(docs_outros)

            set_livro = set(livro_agregado.keys())
            set_outros = set(outros_agregado.keys())
            so_no_livro = set_livro - set_outros
            em_ambos = set_livro & set_outros

            saida.delete(1.0, tk.END)
            saida.insert(tk.END, f"Somente no Livro: {len(so_no_livro)}\n")
            saida.insert(tk.END, f"Em ambos: {len(em_ambos)}\n\n")

            saida.insert(tk.END, "Divergências:\n")
            for numero in sorted(so_no_livro)[:50]:
                valor = livro_agregado.get(numero, 0.0)
                serie = mapa_series.get(numero, "-")
                saida.insert(tk.END, f"Série {serie} - Número {numero} \n Valor: R$ {valor:,.2f}\n")

            saida.insert(tk.END, "\nDiferenças de valor:\n")
            total_diferencas = 0.0
            for numero in sorted(em_ambos):
                valor_livro = livro_agregado[numero]
                valor_outro = outros_agregado[numero]
                if abs(valor_livro - valor_outro) > 0.01:
                    diff = valor_livro - valor_outro
                    total_diferencas += abs(diff)
                    serie = mapa_series.get(numero, "-")
                    saida.insert(
                        tk.END,
                        f"Série {serie} - Número {numero} \n"
                        f"Livro: R$ {valor_livro:,.2f} \n"
                        f"Relatório: R$ {valor_outro:,.2f} \n"
                        f"Diferença: R$ {diff:,.2f}\n"
                    )
            saida.insert(tk.END, f"\nTotal das diferenças: R$ {total_diferencas:,.2f}\n")

            total_nfce_livro = sum(r["valor_float"] for r in registros_livro if r["fiscal"] == "NFCE")
            total_nfs_livro = sum(r["valor_float"] for r in registros_livro if r["fiscal"] == "NFS")

            cupons_agregados = agrupar_por_numero_dict(docs_cupons) 
            notas_agregadas = agrupar_por_numero_dict({**docs_notas, **docs_prazo}) 

            total_cupons_relatorio = sum(cupons_agregados.values())
            total_vista = sum(parse_valor(meta.get("valor")) for meta in docs_notas.values() if meta.get("valor"))
            total_prazo = sum(parse_valor(meta.get("valor")) for meta in docs_prazo.values() if meta.get("valor"))
            total_notas = total_vista + total_prazo

            saida.insert(tk.END, "\n📊 Comparativo por Tipo:\n")
            saida.insert(tk.END, f"- NFCE Livro: R$ {total_nfce_livro:,.2f} \n Relatório: R$ {total_cupons_relatorio:,.2f}\n")
            saida.insert(tk.END, f"- NFS Livro: R$ {total_nfs_livro:,.2f} \n Relatório: R$ {total_notas:,.2f}\n")
            saida.insert(tk.END, f"🔻 Diferença NFCE: R$ {total_nfce_livro - total_cupons_relatorio:,.2f}\n")
            saida.insert(tk.END, f"🔻 Diferença NFS: R$ {total_nfs_livro - total_notas:,.2f}\n")

            total_livro = total_nfce_livro + total_nfs_livro
            total_outros = total_cupons_relatorio + total_notas
            saida.insert(tk.END, "\n📊 Comparativo Geral:\n")
            saida.insert(tk.END, f"- Total Livro (NFCE + NFS): R$ {total_livro:,.2f}\n")
            saida.insert(tk.END, f"- Total Relatórios: R$ {total_outros:,.2f}\n")
            saida.insert(tk.END, f"- Diferença: R$ {total_livro - total_outros:,.2f}\n")

            notas_good = []
            for key, meta in docs_notas.items():                
                c = meta.get("cond")
                if isinstance(key, tuple) and c is not None and c.startswith("GOOD"):
                    serie, numero = key[:2]
                    valor_str = meta.get("valor")
                    valor = parse_valor(valor_str) if valor_str else 0.0
                    notas_good.append((str(serie), str(numero), valor))

            saida.insert(tk.END, "\n⚠️ Verificação de Notas GoodCard (à vista):\n")
            if notas_good:
                def _norm(n):
                    return re.sub(r"[^0-9]", "", str(n)).lstrip("0") or "0"

                for serie, numero, valor in sorted(notas_good, key=lambda x: (x[0], _norm(x[1]))):
                    saida.insert(tk.END, f" Série {serie} - Número {numero} - Valor: R$ {valor:,.2f}\n")
            else:
                saida.insert(tk.END, " Nenhuma nota com COND. = GOOD encontrada nos relatórios de notas à vista.\n")

        except Exception as e:
            messagebox.showerror("Erro", str(e))

    frame = tk.Frame(janela, bg="#1e1e1e")
    frame.pack(padx=10, pady=10)
    labels = ["Livro Fiscal", "Cupons", "Notas à Vista", "Notas a Prazo"]
    entries = []
    for i, label in enumerate(labels):
        tk.Label(frame, text=label, font=("Segoe UI", 10), bg="#1e1e1e", fg="#ffffff").grid(row=i, column=0, sticky="w")
        entry = tk.Entry(frame, width=50, font=("Segoe UI", 10))
        entry.grid(row=i, column=1, padx=5)
        tk.Button(frame, text="Selecionar", command=lambda e=entry: selecionar_arquivo(e),
                  font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
                  activebackground="#444444", activeforeground="#00bfff",
                  relief="flat", bd=0, padx=10, pady=5).grid(row=i, column=2)
        entries.append(entry)

    global entry_livro, entry_cupons, entry_notas, entry_prazo, saida
    entry_livro, entry_cupons, entry_notas, entry_prazo = entries
    
    botoes_frame = tk.Frame(janela, bg="#1e1e1e")
    botoes_frame.pack(pady=5)

    btn_executar = tk.Button(
        botoes_frame, text="Executar", command=executar,
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=5
    )
    btn_executar.grid(row=0, column=0, padx=5)

    btn_voltar = tk.Button(
        botoes_frame, text="Voltar", command=janela.destroy,
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=5
    )
    btn_voltar.grid(row=0, column=1, padx=5)

    btn_limpar = tk.Button(
        botoes_frame, text="Limpar Resultados", command=lambda: saida.delete("1.0", tk.END),
        font=("Segoe UI", 10), bg="#8b0000", fg="#ffffff",
        activebackground="#E10000", activeforeground="#ffffff",
        relief="flat", bd=0, padx=10, pady=5
    )
    btn_limpar.grid(row=0, column=2, padx=5)

    saida = scrolledtext.ScrolledText(janela, width=100, height=30,
                                      bg="#2e2e2e", fg="#ffffff",
                                      font=("Consolas", 10), insertbackground="#ffffff")
    saida.pack(padx=10, pady=10)

FORMATS = {
    "iso": "%m-%d",
    "br": "%d-%m",
    "compact": "%d%m",
}

def carregar_perfis():
    """
    Lê perfis do Supabase (tabela 'perfis') e normaliza para o formato:
      [{"nome": "...", "mes": "Março/2026", "lojas": [{"loja": "39"}, ...]}, ...]
    Busca as LOJAS dentro de doc.jsonb (doc['lojas']) e também aceita
    coluna 'lojas' caso exista. Não depende de 'ano'/'mes_num'.
    """
    try:
        # Pega os campos que realmente existem na sua tabela
        res = supabase.table("perfis").select("id, nome, mes, doc").execute()
        rows = res.data or []

        def _to_lojas_list(lojas_raw):
            """Converte lojas em [{'loja':'xx'}, ...] aceitando dict/list/str/int."""
            out = []
            if isinstance(lojas_raw, list):
                for item in lojas_raw:
                    if isinstance(item, dict):
                        if "loja" in item and item["loja"] is not None:
                            out.append({"loja": str(item["loja"]).strip()})
                        else:
                            for k in ("id", "codigo", "cod", "store", "filial", "loja_id"):
                                if k in item and item[k] is not None:
                                    out.append({"loja": str(item[k]).strip()})
                                    break
                            else:
                                if item:
                                    k0 = next(iter(item))
                                    out.append({"loja": str(item[k0]).strip()})
                    elif item is not None:
                        out.append({"loja": str(item).strip()})
            elif isinstance(lojas_raw, str):
                # "39,40;46|92" -> separa por vírgula / ; / |
                seps = [",", ";", "|"]
                used = next((s for s in seps if s in lojas_raw), None)
                parts = lojas_raw.split(used) if used else [lojas_raw]
                for v in parts:
                    v = v.strip()
                    if v:
                        out.append({"loja": v})
            elif lojas_raw is not None:
                out.append({"loja": str(lojas_raw).strip()})
            return out

        perfis = []
        for r in rows:
            # Base: nome e mes das colunas text (já no padrão "NomeMês/AAAA")
            nome = (r.get("nome") or "").strip() or "Sem nome"
            mes_fmt = (r.get("mes") or "").strip()

            # doc pode trazer 'lojas' (e até sobrescrever nome/mes, se desejar)
            lojas_list = []
            doc = r.get("doc")
            # Se 'doc' vier como string por algum motivo, tenta decodificar
            if isinstance(doc, str):
                try:
                    import json as _json
                    doc = _json.loads(doc)
                except Exception:
                    doc = None
            if isinstance(doc, dict):
                if not mes_fmt:
                    mes_fmt = (doc.get("mes") or "").strip()
                if nome == "Sem nome":
                    nome = (doc.get("nome") or "").strip() or nome
                lojas_from_doc = doc.get("lojas")
                if lojas_from_doc:
                    lojas_list = _to_lojas_list(lojas_from_doc)

            # fallback: se existir uma coluna 'lojas' direta (em algum ambiente)
            if not lojas_list and "lojas" in r and r["lojas"] not in (None, "", []):
                lojas_list = _to_lojas_list(r["lojas"])

            perfis.append({
                "nome": nome,
                "mes": mes_fmt,              # "Março/2026" (igual à UI)
                "lojas": lojas_list          # [{"loja":"39"}, ...]
            })

        return perfis

    except Exception as e:
        try:
            messagebox.showerror("Perfis", f"Falha ao carregar perfis do banco:\n{e}")
        except Exception:
            print("Perfis (erro Supabase):", e)
        return []

def meses_disponiveis(perfis):
    """Retorna lista ordenada de meses únicos no formato 'NomeMes/Ano'."""
    meses = {p.get("mes") for p in perfis if p.get("mes")}
    # Ordena por ano e número do mês (usando MESES_PTBR)
    return sorted(
        meses,
        key=lambda s: (int(s.split("/")[1]), MESES_PTBR.index(s.split("/")[0]) + 1)
    )

def perfis_do_mes(perfis, mes_sel):
    """Filtra perfis pelo mês selecionado."""
    return [p for p in perfis if p.get("mes") == mes_sel]

def nome_pasta_loja(loja_dict):
    """
    Formata 'loja xx' com zero à esquerda para valores numéricos curtos.
    Usa normalizar_loja_valor do controle.py.
    """
    num = normalizar_loja_valor(loja_dict.get("loja"))
    # Zero-pad somente se for estritamente numérico
    num_fmt = num.zfill(2) if num.isdigit() else num
    return f"loja {num_fmt}"

def criar_pastas_por_perfil(mes_sel, perfil_escolhido, base, formato):
    """
    Cria pastas no padrão:
      base/loja xx/<pastas dos dias do mês selecionado>
    - mes_sel: 'NomeMes/Ano' (ex.: 'Janeiro/2025')
    - perfil_escolhido: None ou string com nome do perfil; se None => todos do mês
    - base: pasta base escolhida no diálogo
    - formato: 'br' | 'iso' | 'compact'
    """
    perfis = carregar_perfis()
    if not perfis:
        raise ValueError("Nenhum perfil encontrado (perfis.json).")

    perfis_mes = perfis_do_mes(perfis, mes_sel)
    if not perfis_mes:
        raise ValueError("Não há perfis para o mês selecionado.")

    if perfil_escolhido:  # se um perfil específico foi escolhido
        perfis_mes = [p for p in perfis_mes if p.get("nome") == perfil_escolhido]
        if not perfis_mes:
            raise ValueError("Perfil selecionado não encontrado para este mês.")
    
    
    print("[DEBUG] perfis_mes_count:", len(perfis_mes))
    if perfis_mes:
        print("[DEBUG] exemplo perfis_mes[0]:", perfis_mes[0])


    # Consolida lojas evitando duplicidades por nome de loja
    lojas_unicas = []
    vistos = set()
    for p in perfis_mes:
        for l in p.get("lojas", []):
            nome = l.get("loja")
            if nome not in vistos:
                vistos.add(nome)
                lojas_unicas.append(l)

    if not lojas_unicas:
        raise ValueError("Nenhuma loja vinculada ao(s) perfil(is) selecionado(s).")

    # Extrai ano/mês a partir de 'NomeMes/Ano' usando MESES_PTBR
    mes_nome, ano_str = mes_sel.split("/")
    ano = int(ano_str)
    mes_num = MESES_PTBR.index(mes_nome) + 1
    dias_no_mes = monthrange(ano, mes_num)[1]

    if formato not in FORMATS:
        raise ValueError(f"Formato de data inválido: '{formato}'")
    padrao = FORMATS[formato]

    base_path = Path(base)
    base_path.mkdir(parents=True, exist_ok=True)

    lojas_criadas = 0
    dias_criados = 0

 
    for loja in lojas_unicas:
        pasta_loja = base_path / nome_pasta_loja(loja)

        # Cria a pasta da loja
        existia_loja = pasta_loja.exists()
        pasta_loja.mkdir(parents=True, exist_ok=True)
        if not existia_loja:
            lojas_criadas += 1

        # >>> NOVO: cria a pasta GTV no nível da loja, junto às pastas dos dias
        pasta_gtv = pasta_loja / "GTV"
        existia_gtv = pasta_gtv.exists()
        pasta_gtv.mkdir(exist_ok=True)
        # (se quiser contar quantas GTV foram criadas, dá para somar num contador aqui)

        # Cria subpastas dos dias daquele mês
        for d in range(1, dias_no_mes + 1):
            sub = date(ano, mes_num, d).strftime(padrao)
            p_sub = pasta_loja / sub
            existia_sub = p_sub.exists()
            p_sub.mkdir(exist_ok=True)
            if not existia_sub:
                dias_criados += 1


    messagebox.showinfo(
        "Concluído",
        f" Pastas de lojas criadas: {lojas_criadas}\n"
        f" Pastas de dias criadas: {dias_criados}"
    )


def criar_pastas_do_mes(ano, mes, base, formato):
    try:
        if not (1 <= mes <= 12): raise ValueError("O mês deve estar entre 1 e 12.")
        if formato not in FORMATS: raise ValueError(f"Formato inválido: '{formato}'.")
        base_path = Path(base)
        base_path.mkdir(parents=True, exist_ok=True)
        dias_no_mes = monthrange(ano, mes)[1]
        padrao = FORMATS[formato]
        criadas, existentes = 0, 0
        for dia in range(1, dias_no_mes + 1):
            nome_pasta = date(ano, mes, dia).strftime(padrao)
            caminho = base_path / nome_pasta
            ja_existia = caminho.exists()
            caminho.mkdir(exist_ok=True)
            if ja_existia: existentes += 1
            else: criadas += 1
        messagebox.showinfo("Concluído", f"✅ Pastas criadas: {criadas}\n📁 Já existentes: {existentes}")
    except Exception as e:
        messagebox.showerror("Erro", str(e))

def iniciar_pastas():
    perfis = carregar_perfis()
    meses = meses_disponiveis(perfis)

    def selecionar_pasta():
        caminho = filedialog.askdirectory()
        if caminho:
            pasta_entry.delete(0, tk.END)
            pasta_entry.insert(0, caminho)

    def atualizar_perfis_mes(*_):
        mes_sel = mes_var.get()
        nomes = [p.get("nome") for p in perfis_do_mes(perfis, mes_sel)]
        # Inserimos opção "Todos"
        valores = ["(Todos)"] + nomes
        perfil_combo["values"] = valores
        perfil_combo.set("(Todos)")

    def executar():
        try:
            mes_sel = mes_var.get().strip()
            if not mes_sel:
                messagebox.showwarning("Aviso", "Selecione o mês.")
                return

            base = pasta_entry.get().strip()
            if not base:
                messagebox.showwarning("Aviso", "Selecione a pasta base.")
                return

            formato = formato_combo.get().strip()
            perfil_escolhido = perfil_combo.get().strip()
            perfil_final = None if perfil_escolhido == "(Todos)" else perfil_escolhido

            criar_pastas_por_perfil(mes_sel, perfil_final, base, formato)
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    janela = tk.Toplevel(root)
    janela.title("Gerador de Pastas")
    janela.configure(bg="#1e1e1e")
    if icone:
        janela.iconphoto(False, icone)
    janela.transient(root)

    frame = tk.Frame(janela, bg="#1e1e1e")
    frame.pack(padx=20, pady=20, fill="x")

    tk.Label(frame, text="Mês (dos perfis):", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="w")
    mes_var = tk.StringVar(value=meses[-1] if meses else "")
    mes_combo = ttk.Combobox(frame, textvariable=mes_var, values=meses,
                             font=("Segoe UI", 10), width=18)
    mes_combo.grid(row=0, column=1, sticky="w", padx=10)

    tk.Label(frame, text="Perfil:", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="w")
    perfil_combo = ttk.Combobox(frame, values=["(Todos)"], font=("Segoe UI", 10), width=18)
    perfil_combo.grid(row=1, column=1, sticky="w", padx=10)
    perfil_combo.set("(Todos)")

    tk.Label(frame, text="Pasta base:", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=2, column=0, sticky="w")
    pasta_entry = tk.Entry(frame, font=("Segoe UI", 10), width=40)
    pasta_entry.grid(row=2, column=1, sticky="w", padx=10)
    tk.Button(frame, text="Selecionar", command=selecionar_pasta,
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=2, column=2, sticky="w", padx=5)

    tk.Label(frame, text="Formato:", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=3, column=0, sticky="w")
    formato_combo = ttk.Combobox(frame, values=list(FORMATS.keys()),
                                 font=("Segoe UI", 10), width=10)
    formato_combo.grid(row=3, column=1, sticky="w", padx=10)
    formato_combo.set("br")

    btn_frame = tk.Frame(janela, bg="#1e1e1e")
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="Criar Pastas", command=executar,
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=0, padx=5)
    tk.Button(btn_frame, text="Voltar", command=janela.destroy,
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=1, padx=5)

    # Quando o mês mudar, atualiza perfis
    mes_var.trace_add("write", atualizar_perfis_mes)
    # Inicializa perfis para o mês corrente do combo
    atualizar_perfis_mes()


def iniciar_comparador_diario():
    def selecionar_pdf(entry):
        caminho = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if caminho:
            entry.delete(0, tk.END)
            entry.insert(0, caminho)

    def selecionar_excel(entry):
        caminho = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx *.xls")])
        if caminho:
            entry.delete(0, tk.END)
            entry.insert(0, caminho)

    def voltar():
        janela.destroy()

    def executar():
        """
        Livro Fiscal x Contabilidade (comparador diário)
        - Mantém as regras existentes.
        - Regra ESPECIAL 1575: DOC no formato 'numero - xxx -' (xxx<025 -> NFCE; >=025 -> NFS)
        """

        # --- helper local: classifica NFCE/NFS pelo DOC quando a conta é 1575 ---
        def _nf_por_1575(doc_str):
            try:
                s = str(doc_str)
                m = re.search(r'(\d+)\s*-\s*(\d{3})\s*-?', s)
                if not m:
                    return None
                codigo = int(m.group(2))  # '007' -> 7; '025' -> 25
                return "NFCE" if codigo < 25 else "NFS"
            except Exception:
                return None

        try:
            caminho_pdf = entry_pdf.get().strip()
            caminho_excel = entry_excel.get().strip()
            if not caminho_pdf or not caminho_excel:
                messagebox.showwarning("Aviso", "Selecione os dois arquivos antes de executar.")
                return

            # ---------------- Livro Fiscal (PDF) ----------------
            registros_livro = extrair_livro_como_lista(caminho_pdf)
            livro_por_dia = {}
            for r in registros_livro:
                data_raw = r.get("data", "")
                try:
                    data_formatada = pd.to_datetime(data_raw, dayfirst=True).strftime('%d/%m')
                except Exception:
                    data_formatada = None
                if data_formatada:
                    tipo = "NFCE" if r["fiscal"] == "NFCE" else "NFS"
                    valor = r["valor_float"]
                    livro_por_dia.setdefault(data_formatada, {"NFCE": 0.0, "NFS": 0.0})
                    livro_por_dia[data_formatada][tipo] += valor

            # ---------------- Contabilidade (Excel) ----------------
            df = pd.read_excel(caminho_excel, header=None)
            contabilidade_por_dia = {}
            for _, row in df.iterrows():
                # coluna 9 é valor (CRÉDITO) na sua planilha atual
                valor = pd.to_numeric(row[9], errors='coerce')
                if pd.isna(valor):
                    continue

                # filtra linhas com CFOPs excluídos (usa a linha inteira como texto)
                linha_texto = " ".join(str(x) for x in row.values).upper()
                if any(cfop in linha_texto for cfop in CFOPS_EXCLUIDOS):
                    continue

                doc_raw = row[4]                     # DOC.NRO.
                texto_valor = str(row[7]).upper()    # coluna com "CONTA (xxxx)" etc.

                # extrai conta (3 ou 4 dígitos) a partir do texto da coluna de partida
                match = pd.Series(texto_valor).str.extract(r'(\d{3,4})')
                conta_num = int(match.iloc[0, 0]) if not match.isna().iloc[0, 0] else None

                # -------- Classificação do tipo (NFCE x NFS) --------
                if conta_num == 1575:
                    # regra especial baseada em "numero - xxx -" (se existir)
                    tipo_doc = _nf_por_1575(doc_raw)
                    if tipo_doc is None:
                        # fallback se não achar o padrão 'n - xxx -'
                        try:
                            doc_num = int(doc_raw)
                            tipo_doc = "NFCE" if 1 <= doc_num <= 100 else "NFS"
                        except Exception:
                            tipo_doc = "NFS"
                elif "COLIGADAS" in texto_valor or conta_num in (1022, 430):
                    tipo_doc = "NFS"
                elif conta_num == 989 or "DEPÓSITO" in texto_valor:
                    tipo_doc = "NFCE"
                else:
                    # --------- AQUI ESTAVA O BUG ---------
                    # usar conta_num (já extraída acima), não conta_partida
                    if (conta_num is not None) and (conta_num in (1253, 1179)):
                        tipo_doc = "NFS"
                    else:
                        try:
                            doc_num = int(doc_raw)
                            tipo_doc = "NFCE" if 1 <= doc_num <= 100 else "NFS"
                        except Exception:
                            tipo_doc = "NFS"

                # -------- Data (dd/mm) --------
                data_raw = str(row[0]).strip()
                if isinstance(row[0], (pd.Timestamp, datetime)):
                    data_formatada = row[0].strftime('%d/%m')
                else:
                    m = re.search(r'(\d{2}[/-]\d{2})(?:[/-](\d{4}))?', data_raw)
                    if m:
                        dia_mes = m.group(1).replace("-", "/")
                        ano = m.group(2) if m.group(2) else str(date.today().year)
                        try:
                            data_formatada = pd.to_datetime(f"{dia_mes}/{ano}", dayfirst=True).strftime('%d/%m')
                        except Exception:
                            data_formatada = None
                    else:
                        data_formatada = None

                # agrega por dia/tipo
                if data_formatada:
                    contabilidade_por_dia.setdefault(data_formatada, {"NFCE": 0.0, "NFS": 0.0})
                    contabilidade_por_dia[data_formatada][tipo_doc] += valor

            # ----- IMPRESSÃO / COMPARAÇÃO -----
            todos_os_dias = sorted(
                set(livro_por_dia.keys()) | set(contabilidade_por_dia.keys()),
                key=lambda d: datetime.strptime(d, "%d/%m")
            )

            def _fmt(v):
                try:
                    return (f"R$ {float(v):,.2f}"
                            .replace(",", "X")
                            .replace(".", ",")
                            .replace("X", "."))
                except Exception:
                    return "R$ 0,00"

            saida.delete("1.0", tk.END)
            saida.insert(tk.END, "📘 Livro × Contabilidade\n\n")

            # Blocos por dia (multi-linha)
            for dia in todos_os_dias:
                livro_nfce = livro_por_dia.get(dia, {}).get("NFCE", 0.0)
                livro_nfs  = livro_por_dia.get(dia, {}).get("NFS", 0.0)
                cont_nfce  = contabilidade_por_dia.get(dia, {}).get("NFCE", 0.0)
                cont_nfs   = contabilidade_por_dia.get(dia, {}).get("NFS", 0.0)
                saida.insert(tk.END, f"• Dia {dia}:\n")
                saida.insert(tk.END, f" Livro NFCE: {_fmt(livro_nfce)}\n")
                saida.insert(tk.END, f" Contab. NFCE: {_fmt(cont_nfce)}\n")
                saida.insert(tk.END, f" Livro NFS: {_fmt(livro_nfs)}\n")
                saida.insert(tk.END, f" Contab. NFS: {_fmt(cont_nfs)}\n\n")

                        # ── SOMATÓRIA DO PERÍODO ──────────────────────────────────
            total_livro_nfce = sum(livro_por_dia.get(d, {}).get("NFCE", 0.0) for d in todos_os_dias)
            total_livro_nfs  = sum(livro_por_dia.get(d, {}).get("NFS",  0.0) for d in todos_os_dias)
            total_cont_nfce  = sum(contabilidade_por_dia.get(d, {}).get("NFCE", 0.0) for d in todos_os_dias)
            total_cont_nfs   = sum(contabilidade_por_dia.get(d, {}).get("NFS",  0.0) for d in todos_os_dias)

            saida.insert(tk.END, "─" * 40 + "\n")
            saida.insert(tk.END, "📊 SOMATÓRIA DO PERÍODO\n\n")
            saida.insert(tk.END, f" Livro   NFCE total: {_fmt(total_livro_nfce)}\n")
            saida.insert(tk.END, f" Contab. NFCE total: {_fmt(total_cont_nfce)}\n")
            saida.insert(tk.END, f" Dif. NFCE:          {_fmt(total_livro_nfce - total_cont_nfce)}\n\n")
            saida.insert(tk.END, f" Livro   NFS  total: {_fmt(total_livro_nfs)}\n")
            saida.insert(tk.END, f" Contab. NFS  total: {_fmt(total_cont_nfs)}\n")
            saida.insert(tk.END, f" Dif. NFS:           {_fmt(total_livro_nfs - total_cont_nfs)}\n\n")
            saida.insert(tk.END, f" Livro   TOTAL:      {_fmt(total_livro_nfce + total_livro_nfs)}\n")
            saida.insert(tk.END, f" Contab. TOTAL:      {_fmt(total_cont_nfce + total_cont_nfs)}\n")
            saida.insert(tk.END, "─" * 40 + "\n\n")    

            divergencias = []
            for dia in todos_os_dias:
                livro_nfce = livro_por_dia.get(dia, {}).get("NFCE", 0.0)
                livro_nfs  = livro_por_dia.get(dia, {}).get("NFS", 0.0)
                cont_nfce  = contabilidade_por_dia.get(dia, {}).get("NFCE", 0.0)
                cont_nfs   = contabilidade_por_dia.get(dia, {}).get("NFS", 0.0)
                if abs(livro_nfce - cont_nfce) > 0.01:
                    divergencias.append((dia, "NFCE", livro_nfce, cont_nfce))
                if abs(livro_nfs - cont_nfs) > 0.01:
                    divergencias.append((dia, "NFS",  livro_nfs,  cont_nfs))

            if divergencias:
                saida.insert(tk.END, "\n🚨 Divergências encontradas:\n")
                for dia, tipo, v_livro, v_contab in divergencias:
                    diff = v_livro - v_contab
                    saida.insert(
                        tk.END,
                        f"• Dia {dia}  {tipo}  "
                        f"Livro: {_fmt(v_livro)}  "
                        f"Contab: {_fmt(v_contab)}  "
                        f"Dif: {_fmt(diff)}\n"
                    )
            else:
                saida.insert(tk.END, "\n✅ Nenhuma divergência encontrada.\n")

        except Exception as e:
            messagebox.showerror("Erro", str(e))


    janela = tk.Toplevel(root)
    janela.title("Livro Fiscal x Contabilidade")
    janela.configure(bg="#1e1e1e")
    if icone:
        janela.iconphoto(False, icone)
    janela.transient(root)

    frame = tk.Frame(janela, bg="#1e1e1e")
    frame.pack(padx=20, pady=20)

    tk.Label(frame, text="Livro Fiscal:", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="w")
    entry_pdf = tk.Entry(frame, font=("Segoe UI", 10), width=40)
    entry_pdf.grid(row=0, column=1, sticky="w", padx=10)
    tk.Button(frame, text="Selecionar", command=lambda: selecionar_pdf(entry_pdf),
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=2, sticky="w", padx=5)

    tk.Label(frame, text="Contabilidade:", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="w")
    entry_excel = tk.Entry(frame, font=("Segoe UI", 10), width=40)
    entry_excel.grid(row=1, column=1, sticky="w", padx=10)
    tk.Button(frame, text="Selecionar", command=lambda: selecionar_excel(entry_excel),
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=1, column=2, sticky="w", padx=5)

    btn_frame = tk.Frame(janela, bg="#1e1e1e")
    btn_frame.pack(pady=10)

    tk.Button(btn_frame, text="Executar", command=executar,
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=0, padx=5)

    tk.Button(btn_frame, text="Voltar", command=voltar,
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=1, padx=5)

    tk.Button(btn_frame, text="Limpar Resultados", command=lambda: saida.delete("1.0", tk.END),
              font=("Segoe UI", 10), bg="#8b0000", fg="#ffffff",
              activebackground="#E10000", activeforeground="#ffffff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=2, padx=5)

    saida = scrolledtext.ScrolledText(janela, width=100, height=30,
                                      bg="#2e2e2e", fg="#ffffff",
                                      font=("Consolas", 10), insertbackground="#ffffff")
    saida.pack(padx=10, pady=10)

def fmt_money_br(valor, na_text="R$ 0,00"):
    """Formata número no padrão BR (R$ 1.234,56)."""
    try:
        return (f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    except Exception:
        return na_text

def extrair_tabelas(pdf_path):

    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Caminho do PDF inválido: {pdf_path}")

    dados = []
    with pdfplumber.open(pdf_path) as pdf:
        for pagina in pdf.pages:
            try:
                tabelas = pagina.extract_tables() or []
            except Exception as e:
                print(f"AVISO: falha ao extrair tabela da página {pagina.page_number}: {e}")
                continue

            for tabela in tabelas:
                if not tabela:
                    continue

                if len(tabela[0]) > 3:
                    df = pd.DataFrame(tabela, columns=["DESCRIÇÃO", "X1", "ENTRADA", "X2", "X3", "X4"])
                    df = df[["DESCRIÇÃO", "ENTRADA"]]
                    df["SAÍDA"] = 0.0
                else:
                    df = pd.DataFrame(tabela[1:], columns=tabela[0])

                    cols_upper = {str(c).upper().strip(): c for c in df.columns}
                    if "SAÍDA" not in cols_upper and "SAIDA" in cols_upper:
                        df.rename(columns={cols_upper["SAIDA"]: "SAÍDA"}, inplace=True)
                    if "DESCRIÇÃO" not in cols_upper and "DESCRICAO" in cols_upper:
                        df.rename(columns={cols_upper["DESCRICAO"]: "DESCRIÇÃO"}, inplace=True)
                    if "ENTRADA" not in df.columns:
                        df["ENTRADA"] = 0.0
                    if "SAÍDA" not in df.columns:
                        df["SAÍDA"] = 0.0
                    if "DESCRIÇÃO" not in df.columns:
                        df["DESCRIÇÃO"] = ""

                df.columns = [str(c).upper().strip() for c in df.columns]
                for col in ["DESCRIÇÃO", "ENTRADA", "SAÍDA"]:
                    if col not in df.columns:
                        df[col] = 0.0 if col in ("ENTRADA", "SAÍDA") else ""
                df = df[["DESCRIÇÃO", "ENTRADA", "SAÍDA"]]
                dados.append(df)

    if not dados:
        return pd.DataFrame(columns=["DESCRIÇÃO", "ENTRADA", "SAÍDA"])
    return pd.concat(dados, ignore_index=True)


def preparar_dados(df):
    df = df.dropna(how="all").copy()
    for col in ["DESCRIÇÃO", "ENTRADA", "SAÍDA"]:
        if col not in df.columns:
            df[col] = 0.0 if col in ("ENTRADA", "SAÍDA") else ""
    for col in ["ENTRADA", "SAÍDA"]:
        df[col] = (
            df[col].astype(str)
                 .str.replace(".", "", regex=False)
                 .str.replace(",", ".", regex=False)
        )
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["DESCRIÇÃO"] = df["DESCRIÇÃO"].astype(str).str.upper().str.strip()
    return df

def calcular_vendas(df):
    cupom = df[df["DESCRIÇÃO"].str.contains("CUPOM FISCAL", na=False)]
    nota  = df[df["DESCRIÇÃO"].str.contains("NOTA FISCAL", na=False)]
    resultados = {}

    dinheiro_cupom_base = cupom[cupom["DESCRIÇÃO"].str.contains("DINHEIRO", na=False)]["SAÍDA"].sum()
    dinheiro_cupom_dev  = cupom[cupom["DESCRIÇÃO"].str.contains("DEVOLUCAO ACERTO DE ESTOQUE", na=False)]["SAÍDA"].sum()
    dinheiro_cupom_suc  = cupom[
        cupom["DESCRIÇÃO"].str.contains("SUCATA SMARTINS", na=False)
        | cupom["DESCRIÇÃO"].str.contains("SUCATA PMZ", na=False)
    ]["SAÍDA"].sum()
    total_cupom_din = dinheiro_cupom_base + dinheiro_cupom_dev + dinheiro_cupom_suc

    total_nota_din = nota[nota["DESCRIÇÃO"].str.contains("DINHEIRO", na=False)]["SAÍDA"].sum()

    resultados["DINHEIRO"] = {
        "Cupom": float(total_cupom_din),
        "Nota":  float(total_nota_din),
        "Total": float(total_cupom_din + total_nota_din),
    }

    for forma in ["CARTAO CREDITO", "CARTAO DEBITO", "DEPOSITO", "DEVCAR"]:
        total_cupom = cupom[cupom["DESCRIÇÃO"].str.contains(forma, na=False)]["SAÍDA"].sum()
        total_nota  = nota [nota ["DESCRIÇÃO"].str.contains(forma, na=False)]["SAÍDA"].sum()
        resultados[forma] = {"Cupom": float(total_cupom), "Nota": float(total_nota), "Total": float(total_cupom + total_nota)}

    cup = cupom[cupom["DESCRIÇÃO"].str.contains("PIX MAQUINETA", na=False)]["SAÍDA"].sum()
    not_ = nota [nota ["DESCRIÇÃO"].str.contains("PIX MAQUINETA", na=False)]["SAÍDA"].sum()
    resultados["PIX MAQUINETA"] = {"Cupom": float(cup), "Nota": float(not_), "Total": float(cup + not_)}

    cup = cupom[cupom["DESCRIÇÃO"].str.contains("PIX", na=False) & ~cupom["DESCRIÇÃO"].str.contains("MAQUINETA", na=False)]["SAÍDA"].sum()
    not_ = nota [nota ["DESCRIÇÃO"].str.contains("PIX", na=False) & ~nota ["DESCRIÇÃO"].str.contains("MAQUINETA", na=False)]["SAÍDA"].sum()
    resultados["PIX"] = {"Cupom": float(cup), "Nota": float(not_), "Total": float(cup + not_)}

    cartao_cupom = resultados["CARTAO CREDITO"]["Cupom"] + resultados["CARTAO DEBITO"]["Cupom"] + resultados["DEVCAR"]["Cupom"]
    cartao_nota  = resultados["CARTAO CREDITO"]["Nota"]  + resultados["CARTAO DEBITO"]["Nota"]  + resultados["DEVCAR"]["Nota"]
    resultados["CARTÃO"] = {"Cupom": float(cartao_cupom), "Nota": float(cartao_nota), "Total": float(cartao_cupom + cartao_nota)}

    total_cupom = float(cupom["SAÍDA"].sum())
    total_nota  = float(nota ["SAÍDA"].sum())
    total_geral = float(total_cupom + total_nota)
    return resultados, total_cupom, total_nota, total_geral

def calcular_recebimentos_detalhados(df):
    resultados = {"ANTECIPADOS": {}, "DUPLICATAS": {}, "ABATIMENTO": 0.0, "DEPÓSITO": 0.0}

    df = df.copy()
    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"].astype(str).map(unidecode).str.upper().str.strip().apply(lambda x: re.sub(r"\s+", " ", x))
    )
    df["SAÍDA"] = pd.to_numeric(df.get("SAÍDA", 0.0), errors="coerce").fillna(0.0)

    antecipados = df[df["DESCRIÇÃO"].str.contains("RECEB. ANTECIP|ANTECIPADO", na=False)]
    resultados["ANTECIPADOS"]["DINHEIRO"]      = float(antecipados[antecipados["DESCRIÇÃO"].str.contains("DINHEIRO", na=False)]["SAÍDA"].sum())
    resultados["ANTECIPADOS"]["DEPOSITO"]      = float(antecipados[antecipados["DESCRIÇÃO"].str.contains("DEPOSITO", na=False)]["SAÍDA"].sum())
    resultados["ANTECIPADOS"]["CARTAO"]        = float(antecipados[antecipados["DESCRIÇÃO"].str.contains("CARTAO|DEVCAR", na=False)]["SAÍDA"].sum())
    resultados["ANTECIPADOS"]["PIX"]           = float(antecipados[antecipados["DESCRIÇÃO"].str.contains(r"\bPIX\b", regex=True, na=False) & ~antecipados["DESCRIÇÃO"].str.contains("MAQUINETA", na=False)]["SAÍDA"].sum())
    resultados["ANTECIPADOS"]["PIX MAQUINETA"] = float(antecipados[antecipados["DESCRIÇÃO"].str.contains("PIX MAQUINETA", na=False)]["SAÍDA"].sum())

    duplicatas = df[df["DESCRIÇÃO"].str.contains("RECEB. DUP", na=False)]
    resultados["DUPLICATAS"]["DINHEIRO"]       = float(duplicatas[duplicatas["DESCRIÇÃO"].str.contains("DINHEIRO", na=False)]["SAÍDA"].sum())
    resultados["DUPLICATAS"]["PIX"]            = float(duplicatas[duplicatas["DESCRIÇÃO"].str.contains(r"\bPIX\b", regex=True, na=False) & ~duplicatas["DESCRIÇÃO"].str.contains("MAQUINETA", na=False)]["SAÍDA"].sum())
    resultados["DUPLICATAS"]["PIX MAQUINETA"]  = float(duplicatas[duplicatas["DESCRIÇÃO"].str.contains("PIX MAQUINETA", na=False)]["SAÍDA"].sum())
    resultados["DUPLICATAS"]["CARTAO"]         = float(duplicatas[duplicatas["DESCRIÇÃO"].str.contains("CARTAO", na=False)]["SAÍDA"].sum())

    resultados["ABATIMENTO"] = float(df[df["DESCRIÇÃO"].str.contains("RECEB. ABATIMENTO ANTECIPADO|ABATIMENTO", na=False)]["SAÍDA"].sum())
    resultados["DEPÓSITO"]   = float(df[df["DESCRIÇÃO"].str.contains("RECEBIMENTO EM DEPOSITO", na=False)]["SAÍDA"].sum())

    total_geral = sum(resultados["ANTECIPADOS"].values()) + sum(resultados["DUPLICATAS"].values()) + resultados["ABATIMENTO"] + resultados["DEPÓSITO"]
    return resultados, float(total_geral)

def calcular_saidas_diversas(df):    
    desc_raw = df["DESCRIÇÃO"].astype(str)
    desc = desc_raw.str.upper().str.strip()
    desc_noacc = desc_raw.map(unidecode).str.upper().str.strip()
    saida = df["SAÍDA"]
    sucata_smartins = saida[desc.str.contains("SUCATA SMARTINS", na=False)].sum()
    sucata_pmz      = saida[desc.str.contains("SUCATA PMZ", na=False)].sum()
    total_sucata    = sucata_smartins + sucata_pmz

    resgate_vale = saida[desc.str.contains("RESGATE DE VALE", na=False)].sum()
    devol_comp_consumo = saida[
        desc.str.contains("DEVOLUCAO DE COMPRA", na=False)
        | desc.str.contains("DEVOLUCAO DE CONSUMO", na=False)
        | desc.str.contains("DEVOLUCAO DE COMPRA / CONSUMO", na=False)
    ].sum()
    receb_spm_cartao = saida[desc.str.contains("RECEBIMENTOS SPM CARTAO", na=False)].sum()
    receb_spm_dh_pix = saida[desc.str.contains("RECEBIMENTOS SPM DH/PIX", na=False)].sum()
    reembolso_fin_manaus = saida[desc.str.contains("REEMBOLSO FINANCEIRO MANAUS", na=False)].sum()
    sucata_pgto_fin = saida[desc.str.contains("SUCATA BATERIA-PGTO FINANCEIRO", na=False)
    ].sum()
    devolucao_acerto_estoque = saida[desc.str.contains("DEVOLUCAO ACERTO DE ESTOQUE", na=False)].sum()   
    pneu_oleo_usado = saida[
        (desc.str.contains("VENDA DE PNEU/OLEO - USADO", na=False)) |
        (desc.str.contains("VENDA DE PNEU/ÓLEO - USADO", na=False)) |
        (desc_noacc.str.contains("VENDA DE PNEU/OLEO - USADO", na=False))
    ].sum()

    resultados = {
        "SUCATA BATERIA-PGTO FINANCEIRO": float(sucata_pgto_fin),
        "SUCATA SMARTINS": float(sucata_smartins),
        "SUCATA PMZ": float(sucata_pmz),
        "TOTAL SUCATA": float(total_sucata),
        "RESGATE DE VALE": float(resgate_vale),
        "DEVOLUÇÃO COMPRA/CONSUMO": float(devol_comp_consumo),
        "RECEBIMENTOS SPM CARTAO": float(receb_spm_cartao),
        "RECEBIMENTOS SPM DH/PIX": float(receb_spm_dh_pix),
        "REEMBOLSO FINANCEIRO MANAUS": float(reembolso_fin_manaus),
        "DEVOLUCAO ACERTO DE ESTOQUE": float(devolucao_acerto_estoque),
        "VENDA DE PNEU/ÓLEO - USADO": float(pneu_oleo_usado),
    }

    total_geral = (
        resultados["TOTAL SUCATA"] + resultados["RESGATE DE VALE"] + resultados["DEVOLUÇÃO COMPRA/CONSUMO"]
        + resultados["RECEBIMENTOS SPM CARTAO"] + resultados["RECEBIMENTOS SPM DH/PIX"]
        + resultados["REEMBOLSO FINANCEIRO MANAUS"] + resultados["SUCATA BATERIA-PGTO FINANCEIRO"] + resultados["DEVOLUCAO ACERTO DE ESTOQUE"] + resultados["VENDA DE PNEU/ÓLEO - USADO"]
    )
    return resultados, float(total_geral)

from unidecode import unidecode


def extrair_dev_cartao_canc_portal_texto(pdf_path):
    """
    Procura no TEXTO do PDF a linha do total:
      DEV. CARTAO CRED CANC PORTAL 373,00
    Tolerante a: pontos, variação de espaços, quebra de linha e 'CRED'/'CREDITO'.
    Retorna '373,00' (string) ou None.
    """
    # 1) Padrões mais tolerantes
    padroes = [
        # tudo na mesma linha (como no seu print)
        re.compile(
            r"DEV\.?\s*CARTAO\s*CRED(?:ITO)?\.?\s*CANC\s*PORTAL\s*([0-9\.\,]+)",
            re.IGNORECASE
        ),
        # quebrado entre linhas (ex.: '... CRED' \n 'CANC PORTAL 373,00')
        re.compile(
            r"DEV\.?\s*CARTAO\s*CRED(?:ITO)?\.?\s*CANC\s*PORTAL[\s\n\r]*([0-9\.\,]+)",
            re.IGNORECASE
        ),
        # ainda mais frouxo: aceita qualquer coisa entre as palavras‑chave
        re.compile(
            r"DEV\.?\s*CARTAO.*?CRED(?:ITO)?\.?.*?CANC.*?PORTAL\s*([0-9\.\,]+)",
            re.IGNORECASE | re.DOTALL
        ),
    ]

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                raw = page.extract_text() or ""
                # normalização leve: colapsa múltiplos espaços; NÃO remove quebras ainda
                texto = re.sub(r"[ \t]+", " ", raw)

                # 2) tenta direto no texto normalizado
                for rgx in padroes:
                    m = rgx.search(texto)
                    if m:
                        return m.group(1)

                # 3) fallback linha a linha (sem normalizar pontuação)
                for linha in raw.splitlines():
                    up = linha.upper()
                    if "DEV" in up and "CARTAO" in up and "CANC" in up and "PORTAL" in up:
                        for rgx in padroes:
                            m = rgx.search(linha)
                            if m:
                                return m.group(1)
    except Exception:
        pass
    return None

def extrair_valores_prazo_texto(pdf_path):
    """
    Extrai do texto do PDF (fora das tabelas) os valores:
      - VENDAS A PRAZO COLIGADA
      - VENDAS A PRAZO
      - DEVOLUCAO A PRAZO
      - DEVOLUCOES DO CLIENTE A PRAZO

    Retorna dict com floats.
    """
    import pdfplumber
    resultados = {
        "vendas_prazo_coligada": 0.0,
        "vendas_prazo": 0.0,
        "devolucao_prazo": 0.0,
        "devolucoes_cliente_prazo": 0.0,
    }

    # Expressões mais tolerantes (permitem espaços, maiúsculas/minúsculas, acentos removidos)
    padroes = {
        "vendas_prazo_coligada": re.compile(
            r"VENDAS?\s+A\s+PRAZO\s+COLIGADA\s+([0-9\.,]+)", re.IGNORECASE
        ),
        "vendas_prazo": re.compile(
            r"VENDAS?\s+A\s+PRAZO\s+([0-9\.,]+)", re.IGNORECASE
        ),
        "devolucao_prazo": re.compile(
            r"DEVOLUCAO\s+A\s+PRAZO\s+([0-9\.,]+)", re.IGNORECASE
        ),
        "devolucoes_cliente_prazo": re.compile(
            r"DEVOLUCOES?\s+DO\s+CLIENTE\s+A\s+PRAZO\s+([0-9\.,]+)", re.IGNORECASE
        ),
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            texto_completo = ""
            for page in pdf.pages:
                texto_completo += "\n" + (page.extract_text() or "")

            # Extrair os valores
            for chave, rgx in padroes.items():
                m = rgx.search(texto_completo)
                if m:
                    valor_str = m.group(1)
                    valor_num = parse_brl_to_float(valor_str)
                    resultados[chave] = valor_num

    except Exception as e:
        print("Erro ao extrair valores a prazo:", e)

    return resultados

def parse_brl_to_float(s):
    if not s:
        return 0.0
    s = str(s).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0



def calcular_devolucoes_e_sucata_entradas_ordenado(df):
    """
    Consolida 'Devoluções', 'Sucata', 'Despesas' e 'Outros' a partir do DataFrame
    já normalizado (preparar_dados). Mantém a ordem de apresentação
    e inclui 'DEV CARTAO CRED CANC PORTAL' logo abaixo de
    'DEVOLUCAO ACERTO DE ESTOQUE'.
    """
    # Colunas base
    desc_raw = df["DESCRIÇÃO"].astype(str)

    # Normalizações para buscas robustas
    desc_up    = (desc_raw.str.upper().str.strip()
                  .str.replace(r"\s+", " ", regex=True))
    desc_noacc = (desc_raw.map(unidecode).str.upper().str.strip()
                  .str.replace(r"\s+", " ", regex=True))

    # Considere que o valor pode cair tanto em ENTRADA quanto em SAÍDA
    entrada = df.get("ENTRADA", 0.0).fillna(0.0)
    saida   = df.get("SAÍDA",   0.0).fillna(0.0)
    valores = entrada + saida

    # ---------------- DEVOLUÇÕES (ordem controlada) ----------------
    devolucoes = {}

    devolucoes["DEVOLUCOES CARTAO CREDITO"] = float(
        entrada[desc_up.str.contains("DEVOLUCOES CARTAO CREDITO", na=False)].sum()
    )

    devolucoes["DEVOLUCOES A VISTA"] = float(
        entrada[desc_up.str.contains("DEVOLUCOES A VISTA", na=False)].sum()
    )

    devolucoes["DEVOLUCOES DO CLIENTE A VISTA"] = float(
        entrada[desc_up.str.contains("DEVOLUCOES DO CLIENTE A VISTA", na=False)].sum()
    )

    devolucoes["DEVOLUCOES DO CLIENTE CARTAO"] = float(
        entrada[desc_up.str.contains("DEVOLUCOES DO CLIENTE CARTAO", na=False)].sum()
    )

    # Chave de referência
    val_dev_acerto = float(
        entrada[desc_up.str.contains("DEVOLUCAO ACERTO DE ESTOQUE", na=False)].sum()
    )
    devolucoes["DEVOLUCAO ACERTO DE ESTOQUE"] = val_dev_acerto

    # >>> NOVO: DEV CARTAO CRED CANC PORTAL (logo abaixo do item acima)
    pad_portal = (
        desc_up.str.contains(r"\bDEV\.?\s*CARTAO\s*CRED\.?\s*CANC\s*PORTAL\b", regex=True, na=False) |
        desc_noacc.str.contains(r"\bDEV\.?\s*CARTAO\s*CRED\.?\s*CANC\s*PORTAL\b", regex=True, na=False)
    )
    val_portal_df = float((entrada + saida)[pad_portal].sum())

    # Valor inicial assumindo o que vier do DF
    val_portal_final = val_portal_df
    devolucoes["DEV CARTAO CRED CANC PORTAL"] = val_portal_final


    # ---------------- SUCATA ----------------
    sucata = {}
    sucata["SUCATA DE BATERIA"] = float(
        entrada[desc_up.str.contains("SUCATA DE BATERIA", na=False)].sum()
    )
    sucata["SUCATA OUTROS"] = float(
        entrada[
            desc_up.str.contains("SUCATA", na=False) &
            ~desc_up.str.contains("SUCATA SMARTINS|SUCATA PMZ|SUCATA DE BATERIA", na=False)
        ].sum()
    )

    # ---------------- DESPESAS ----------------
    despesas = {}
    despesas["FRETE - DESPESA"] = float(
        entrada[desc_up.str.contains("FRETE - DESPESA", na=False)].sum()
    )
    despesas["DESPESAS"] = float(
        entrada[desc_up.str.contains(r"\bDESPESAS\b", regex=True, na=False)].sum()
    )

    # ---------------- OUTROS ----------------
    outros = {}
    outros["REEMBOLSO"] = float(
        entrada[desc_up.str.contains("REEMBOLSO", na=False)].sum()
    )
    outros["PAGAMENTO FOLHA FUNCIONARIOS"] = float(
        entrada[desc_up.str.contains("PAGAMENTO FOLHA FUNCIONARIOS", na=False)].sum()
    )
    outros["VALES"] = float(
        entrada[desc_up.str.contains("VALES", na=False)].sum()
    )
    outros["REMESSA DINHEIRO FINAL"] = float(
        entrada[desc_up.str.contains("REMESSA DINHEIRO FINAL", na=False)].sum()
    )
    outros["RECEB. ABATIMENTO ANTECIPADO"] = float(
        entrada[desc_up.str.contains("RECEB. ABATIMENTO ANTECIPADO", na=False)].sum()
    )

    # ---------------- Consolidação na ordem desejada ----------------
    resultados = {}
    resultados.update(devolucoes)
    resultados.update(sucata)
    resultados.update(despesas)
    resultados.update(outros)

    total = float(sum(resultados.values()))
    return resultados, total


def calcular_remessas(df, resultados_vendas, resultados_receb, resultados_saidas):

    desc_raw = df["DESCRIÇÃO"].astype(str)
    desc      = desc_raw.str.upper().str.strip()
    desc_noacc= desc_raw.map(unidecode).str.upper().str.strip()
    entrada   = df["ENTRADA"]

    resultados = {}
    remessa_pix = entrada[desc.str.contains(r"REMESSA DE PIX", na=False) & ~desc.str.contains("MAQUINETA", na=False)].sum()
    saida_pix = resultados_vendas.get("PIX", {}).get("Total", 0.0) + resultados_receb.get("DUPLICATAS", {}).get("PIX", 0.0)
    resultados["PIX"] = {"Remessa": float(remessa_pix), "Saídas": float(saida_pix), "Diferença": float(remessa_pix - saida_pix)}

    remessa_pixmaq = entrada[desc.str.contains("REMESSA DE PIX MAQUINETA", na=False)].sum()
    saida_pixmaq = (
        resultados_vendas.get("PIX MAQUINETA", {}).get("Total", 0.0)
        + resultados_receb.get("ANTECIPADOS", {}).get("PIX", 0.0)
        + resultados_receb.get("DUPLICATAS", {}).get("PIX MAQUINETA", 0.0)
    )
    resultados["PIX MAQUINETA"] = {"Remessa": float(remessa_pixmaq), "Saídas": float(saida_pixmaq), "Diferença": float(remessa_pixmaq - saida_pixmaq)}

    remessa_cartao_base = entrada[desc.str.contains("REMESSA CARTAO", na=False) | desc.str.contains("REMESSA GOOD CARD", na=False)].sum()
    devolucao_cartao_credito = entrada[desc.str.contains("DEVOLUCOES CARTAO CREDITO", na=False)].sum()
    devolucao_cliente_cartao = entrada[desc.str.contains("DEVOLUCOES DO CLIENTE CARTAO", na=False)].sum()
    remessa_cartao_total = remessa_cartao_base + devolucao_cartao_credito + devolucao_cliente_cartao

    saida_cartao = (
        resultados_vendas.get("CARTÃO", {}).get("Total", 0.0)
        + resultados_receb.get("ANTECIPADOS", {}).get("CARTAO", 0.0)
        + resultados_receb.get("DUPLICATAS", {}).get("CARTAO", 0.0)
        + resultados_saidas.get("RECEBIMENTOS SPM CARTAO", 0.0)
    )
    resultados["CARTÃO"] = {"Remessa": float(remessa_cartao_total), "Saídas": float(saida_cartao), "Diferença": float(remessa_cartao_total - saida_cartao)}

    remessa_dinheiro = (
        entrada[desc.str.contains("REMESSA DE DINHEIRO", na=False)].sum()
        + entrada[desc.str.contains("DEVOLUCOES A VISTA", na=False)].sum()
        + entrada[desc.str.contains("DEVOLUCOES DO CLIENTE A VISTA", na=False)].sum()
        + entrada[desc.str.contains("DEVOLUCAO ACERTO DE ESTOQUE", na=False)].sum()
        + entrada[desc.str.contains("SUCATA DE BATERIA", na=False)].sum()
        + entrada[desc.str.contains("FRETE - DESPESA", na=False)].sum()
        + entrada[desc.str.contains("DESPESAS", na=False)].sum()
        + entrada[desc.str.contains("REEMBOLSO FINANCEIRO MANAUS", na=False)].sum()
        + entrada[desc.str.contains("REEMBOLSO", na=False)].sum()
        + entrada[desc.str.contains("PAGAMENTO FOLHA FUNCIONARIOS", na=False)].sum()
        + entrada[desc.str.contains("VALES", na=False)].sum()
        + entrada[desc.str.contains("REMESSA DINHEIRO FINAL", na=False)].sum()
        + entrada[desc.str.contains("RECEB. ABATIMENTO ANTECIPADO", na=False)].sum()     
    )
    saida_dinheiro = (
        resultados_vendas.get("DINHEIRO", {}).get("Total", 0.0)
        + resultados_receb.get("ANTECIPADOS", {}).get("DINHEIRO", 0.0)
        + resultados_receb.get("DUPLICATAS", {}).get("DINHEIRO", 0.0)
        + resultados_saidas.get("TOTAL SUCATA", 0.0)
        + resultados_saidas.get("RECEBIMENTOS SPM DH/PIX", 0.0)
        + resultados_saidas.get("REEMBOLSO FINANCEIRO MANAUS", 0.0)
        + resultados_saidas.get("SUCATA BATERIA-PGTO FINANCEIRO", 0.0)
        + resultados_saidas.get("DEVOLUCAO ACERTO DE ESTOQUE", 0.0)
        + float(resultados_receb.get("ABATIMENTO", 0.0))
        + resultados_saidas.get("VENDA DE PNEU/ÓLEO - USADO", 0.0)
        + resultados_saidas.get("RESGATE DE VALE", 0.0)
    )
    resultados["DINHEIRO"] = {"Remessa": float(remessa_dinheiro), "Saídas": float(saida_dinheiro), "Diferença": float(remessa_dinheiro - saida_dinheiro)}

    remessa_deposito = entrada[desc.str.contains("REMESSA DE DEPOSITO", na=False)].sum()
    saida_deposito = (
        resultados_vendas.get("DEPOSITO", {}).get("Total", 0.0)
        + resultados_receb.get("ANTECIPADOS", {}).get("DEPOSITO", 0.0)
        + resultados_receb.get("DUPLICATAS", {}).get("DEPOSITO", 0.0)
        + resultados_receb.get("DEPÓSITO", 0.0)
    )
    resultados["DEPÓSITO"] = {"Remessa": float(remessa_deposito), "Saídas": float(saida_deposito), "Diferença": float(remessa_deposito - saida_deposito)}

    dif_pixmaq  = resultados.get("PIX MAQUINETA", {}).get("Diferença", 0.0)
    dif_dep     = resultados.get("DEPÓSITO", {}).get("Diferença", 0.0)
    dif_din     = resultados.get("DINHEIRO", {}).get("Diferença", 0.0)
    total_consolidado = float(dif_pixmaq + dif_dep + dif_din)
    resultados["CONSOLIDADO (PIX MAQ + DEPÓSITO + DINHEIRO)"] = {"Remessa": 0.0, "Saídas": 0.0, "Diferença": total_consolidado}

    total_final_remessas = (
        resultados.get("PIX", {}).get("Remessa", 0.0)
        + resultados.get("PIX MAQUINETA", {}).get("Remessa", 0.0)
        + resultados.get("CARTÃO", {}).get("Remessa", 0.0)
        + resultados.get("DINHEIRO", {}).get("Remessa", 0.0)
        + resultados.get("DEPÓSITO", {}).get("Remessa", 0.0)
    )
    resultados["TOTAL FINAL REMESSAS"] = {"Remessa": float(total_final_remessas), "Saídas": 0.0, "Diferença": 0.0}
    return resultados

def _to_number_safe(col):
    # Igual ao da sua referência (robusto p/ string, BR e numérico)
    from pandas.api.types import is_numeric_dtype
    if is_numeric_dtype(col):
        return pd.to_numeric(col, errors='coerce')
    s = (col.astype(str)
            .str.strip()
            .str.replace(r'[^\d,.\-]', '', regex=True))
    has_comma = s.str.contains(',', regex=False)
    has_dot = s.str.contains('.', regex=False)
    mask_both = has_comma & has_dot
    s.loc[mask_both] = (s.loc[mask_both]
                            .str.replace('.', '', regex=False)
                            .str.replace(',', '.', regex=False))
    mask_only_comma = has_comma & ~has_dot
    s.loc[mask_only_comma] = s.loc[mask_only_comma].str.replace(',', '.', regex=False)
    return pd.to_numeric(s, errors='coerce')


def calcular_dinheiro_remetido_core(caminho, ini_str='', fim_str=''):
    import pandas as pd
    from pandas.api.types import is_numeric_dtype

    df = pd.read_excel(caminho, header=None)

    COL_DATA          = 0
    COL_HISTORICO     = 5
    COL_CONTA_PARTIDA = 7
    COL_DEBITO        = 8
    COL_CREDITO       = 9
    COL_E             = 2
    COL_G             = 6

    def to_number_safe(col):
        if is_numeric_dtype(col):
            return pd.to_numeric(col, errors='coerce')
        s = (col.astype(str)
                .str.strip()
                .str.replace(r'[^\d,.\-]', '', regex=True))
        has_comma = s.str.contains(',', regex=False)
        has_dot   = s.str.contains('.', regex=False)
        mask_both = has_comma & has_dot
        s.loc[mask_both] = (s.loc[mask_both]
                            .str.replace('.', '', regex=False)
                            .str.replace(',', '.', regex=False))
        mask_only_comma = has_comma & ~has_dot
        s.loc[mask_only_comma] = s.loc[mask_only_comma].str.replace(',', '.', regex=False)
        return pd.to_numeric(s, errors='coerce')

    deb   = to_number_safe(df[COL_DEBITO])
    cred  = to_number_safe(df[COL_CREDITO])
    col_E = to_number_safe(df[COL_E])
    col_G = to_number_safe(df[COL_G])

    linha_texto = (
        df.apply(lambda s: " ".join("" if pd.isna(v) else str(v) for v in s), axis=1)
        .str.upper()
        .str.strip()
    )
    historico_col = df[COL_HISTORICO].astype(str).str.upper().str.strip()

    # ── 1. Captura rodapés ──────────────────────────────────────────────────
    mask_total_anterior = linha_texto.str.contains(r'\bTOTAL\s+ANTERIOR\b',     regex=True, na=False)
    mask_saldo_final    = linha_texto.str.contains(r'\bTOTAL\s+SALDO\s+FINAL\b', regex=True, na=False)
    eh_dev_total        = linha_texto.str.contains(r'\bDEV\.?\s*TOTAL\b',         regex=True, na=False)

    total_anterior_val    = float(col_E.where(mask_total_anterior).sum()) if mask_total_anterior.any() else None
    total_saldo_final_val = float(col_G.where(mask_saldo_final).sum())    if mask_saldo_final.any()    else None

    padrao_rodape = (
        r'(?:^\s)TOTAL\s+ANTERIOR\b'
        r'|(?:^\s)TOTAL\s+SALDO\s+FINAL\b'
        r'|(?:^\s)TOTAL\s+DEB\s*/\s*CRED\b'
        r'|^\s*TOTAL\s'
    )
    eh_rodape = linha_texto.str.contains(padrao_rodape, regex=True, na=False) & ~eh_dev_total

    # ── 2. Identifica linhas "DINHEIRO REMETIDO" ───────────────────────────
    mask_dr = historico_col.str.contains(r'DINHEIRO\s+REMETIDO', regex=True, na=False)

    # ── 3. Exclui rodapés E linhas DR do cálculo ───────────────────────────
    mask_valor = ((deb.notna() & (deb != 0)) | (cred.notna() & (cred != 0)))
    mask_valid = mask_valor & ~eh_rodape & ~mask_dr

    deb_calc  = deb.where(mask_valid, other=0.0).fillna(0.0)
    cred_calc = cred.where(mask_valid, other=0.0).fillna(0.0)

    # ── 4. Filtro de datas ──────────────────────────────────────────────────
    datas_raw = df[COL_DATA]
    datas = (pd.to_datetime(datas_raw, errors='coerce', dayfirst=True)
             if not pd.api.types.is_datetime64_any_dtype(datas_raw)
             else pd.to_datetime(datas_raw))

    data_ini = pd.to_datetime(ini_str, dayfirst=True, errors='coerce') if ini_str else None
    data_fim = pd.to_datetime(fim_str, dayfirst=True, errors='coerce') if fim_str else None

    mask_data = datas.notna()
    if data_ini is not None:
        mask_data &= datas >= data_ini
    if data_fim is not None:
        mask_data &= datas <= data_fim

    deb_filtrado  = deb_calc.where(mask_data, other=0.0)
    cred_filtrado = cred_calc.where(mask_data, other=0.0)

    df_calc = pd.DataFrame({
        'data': datas.dt.date,
        'deb':  deb_filtrado,
        'cred': cred_filtrado,
    })
    df_calc = df_calc[df_calc['data'].notna() & ((df_calc['deb'] != 0) | (df_calc['cred'] != 0))]

    por_dia = (
        df_calc
        .groupby('data')
        .apply(lambda x: float(x['deb'].sum() - x['cred'].sum()))
        .reset_index(name='remetido')
    )
    total_periodo = float(por_dia['remetido'].sum()) if not por_dia.empty else 0.0

    # ── 5. Sucata ───────────────────────────────────────────────────────────
    mask_sucata  = historico_col.str.contains(r'SUCATA', regex=True, na=False) & ~eh_rodape
    sucata_deb   = deb.where(mask_sucata & mask_data, other=0.0).fillna(0.0)
    sucata_cred  = cred.where(mask_sucata & mask_data, other=0.0).fillna(0.0)
    sucata_total = float(sucata_deb.sum() - sucata_cred.sum())

    # ── 6. Captura linhas "DINHEIRO REMETIDO" (no período) ─────────────────
    mask_dr_periodo = mask_dr & mask_data
    linhas_dr = []

    if mask_dr_periodo.any():
        for idx in df[mask_dr_periodo].index:
            row = df.loc[idx]
            linhas_dr.append({
                'data':          pd.to_datetime(datas[idx]).strftime('%d/%m/%Y') if pd.notna(datas[idx]) else '',
                'sequencia':     row[1],
                'lote':          row[2],
                'voucher':       row[3],
                'doc_nro':       row[4],
                'conta_partida': row[7],
                'debito':        float(deb[idx])  if pd.notna(deb[idx])  else 0.0,
                'credito':       float(cred[idx]) if pd.notna(cred[idx]) else 0.0,
            })

        valor_lancado_dr = sum(l['debito'] - l['credito'] for l in linhas_dr)
        TOLERANCIA = 0.01
        if abs(abs(valor_lancado_dr) - abs(total_periodo)) <= TOLERANCIA:
            status_dr      = 'correto'
            valor_ajustado = None
        else:
            status_dr      = 'divergente'
            valor_ajustado = total_periodo
    else:
        valor_lancado_dr = None
        valor_ajustado   = total_periodo
        status_dr        = 'sem_lancamento'

    return (por_dia, total_periodo, total_anterior_val, total_saldo_final_val,
            sucata_total, valor_lancado_dr, valor_ajustado, status_dr, linhas_dr)

def abrir_analise_memorando():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
    import pandas as pd
    import re
    from unidecode import unidecode
    from datetime import datetime, date

    # ============================
    # UI raiz
    # ============================
    janela = tk.Toplevel(root)
    janela.title("Conferência de Caixa")
    janela.configure(bg="#1e1e1e")
    if icone:
        janela.iconphoto(False, icone)
    janela.transient(root)

    def ao_fechar():
        janela.destroy()
    janela.protocol("WM_DELETE_WINDOW", ao_fechar)

    
    style = ttk.Style()
    style.theme_use("default")

    # Cor de fundo da barra de abas
    style.configure("TNotebook", background="#1e1e1e", borderwidth=0)

    # Cor das abas (normais)
    style.configure("TNotebook.Tab", background="#1e1e1e", foreground="white")

    # Cor da aba selecionada
    style.map(
        "TNotebook.Tab",
        background=[("selected", "#2e2e2e")],
        foreground=[("selected", "white")]
    )


    # ============================
    # TOPO: campos + Excel opcional da contabilidade
    # ============================
    topo = tk.Frame(janela, bg="#1e1e1e")
    topo.pack(padx=20, pady=12, fill="x")

    # Memorando (PDF)
    tk.Label(topo, text="Memorando Geral:", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="w")
    entry_pdf = tk.Entry(topo, font=("Segoe UI", 10))
    entry_pdf.grid(row=0, column=1, sticky="we", padx=8)
    topo.grid_columnconfigure(1, weight=1)

    def selecionar_pdf():
        caminho = filedialog.askopenfilename(
            title="Selecione o memorando PDF",
            filetypes=[("PDF files", "*.pdf")]
        )
        if caminho:
            entry_pdf.delete(0, tk.END)
            entry_pdf.insert(0, caminho)
            # Autopreenche loja/data
            try:
                loja_auto, data_auto = extrair_cabecalho_memorando(caminho)
                if loja_auto:
                    entry_loja.delete(0, tk.END)
                    entry_loja.insert(0, loja_auto)
                if data_auto:
                    entry_data.delete(0, tk.END)
                    entry_data.insert(0, data_auto)
            except Exception as e:
                print("WARN: autopreenchimento falhou:", e)

    tk.Button(
        topo, text="Selecionar...", command=selecionar_pdf,
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=5
    ).grid(row=0, column=2, padx=5)

    # Loja / Data
    tk.Label(topo, text="Loja:", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="w", pady=(6, 0))
    entry_loja = tk.Entry(topo, font=("Segoe UI", 10), width=12)
    entry_loja.grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))

    tk.Label(topo, text="Data (dd/mm/aaaa):", font=("Segoe UI", 10),
             bg="#1e1e1e", fg="#ffffff").grid(row=1, column=2, sticky="e", pady=(6, 0))
    entry_data = tk.Entry(topo, font=("Segoe UI", 10), width=14)
    entry_data.grid(row=1, column=3, sticky="w", padx=8, pady=(6, 0))

    # ============================
    # Notebook com 3 abas
    # ============================
    nb = ttk.Notebook(janela)
    nb.pack(fill="both", expand=True, padx=10, pady=(6, 10))

    # Aba 1: Memorando
    aba_memo = tk.Frame(nb, bg="#1e1e1e")
    nb.add(aba_memo, text="Memorando")

    memo_btns = tk.Frame(aba_memo, bg="#1e1e1e")
    memo_btns.pack(pady=6)

    memo_saida = scrolledtext.ScrolledText(
        aba_memo, width=110, height=30,
        bg="#2e2e2e", fg="#ffffff",
        font=("Consolas", 10), insertbackground="#ffffff"
    )
    memo_saida.pack(padx=8, pady=8, fill="both", expand=True)
    memo_saida.pack(padx=8, pady=8, fill="both", expand=True)
    memo_saida.tag_configure("rem_ok",  foreground="#00cc66")   # verde: diferença > 1,50
    memo_saida.tag_configure("rem_err", foreground="#ff4444")
    # Aba 2: Contabilidade
    aba_conf = tk.Frame(nb, bg="#1e1e1e")
    nb.add(aba_conf, text="Contabilidade")

    conf_btns = tk.Frame(aba_conf, bg="#1e1e1e")
    conf_btns.pack(pady=6)

    # === Seletor do Excel (dentro da aba Contabilidade) ===
    conf_top = tk.Frame(aba_conf, bg="#1e1e1e")
    conf_top.pack(fill="x", padx=10, pady=(4, 0))

    tk.Label(conf_top, text="Razão Modelo I - Intervalo de Contas:", font=("Segoe UI", 10),
            bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="w")

    entry_contab = tk.Entry(conf_top, font=("Segoe UI", 10))
    entry_contab.grid(row=0, column=1, sticky="we", padx=8)
    conf_top.grid_columnconfigure(1, weight=1)

    def selecionar_excel_conf():
        caminho = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx *.xls")])
        if caminho:
            entry_contab.delete(0, tk.END)
            entry_contab.insert(0, caminho)

    tk.Button(conf_top, text="Selecionar...", command=selecionar_excel_conf,
            font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
            activebackground="#444444", activeforeground="#00bfff",
            relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=2, padx=5)

    conf_saida = scrolledtext.ScrolledText(
        aba_conf, width=110, height=30,
        bg="#2e2e2e", fg="#ffffff",
        font=("Consolas", 10), insertbackground="#ffffff"
    )
    conf_saida.pack(padx=8, pady=8, fill="both", expand=True)
    conf_saida.tag_configure("div_red", background="", foreground="#ff6666")
    # =========================================================
    # Aba 3: Dinheiro Remetido (somente Excel, sem datas)
    # =========================================================
    aba_rem = tk.Frame(nb, bg="#1e1e1e")
    nb.add(aba_rem, text="Dinheiro Remetido")

    rem_top = tk.Frame(aba_rem, bg="#1e1e1e")
    rem_top.pack(fill="x", padx=10, pady=8)

    tk.Label(rem_top, text="Razão Modelo I - Conta 6:", font=("Segoe UI", 10),
            bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="w")

    entry_rem_arquivo = tk.Entry(rem_top, font=("Segoe UI", 10), width=48)
    entry_rem_arquivo.grid(row=0, column=1, padx=8, sticky="we")
    rem_top.grid_columnconfigure(1, weight=1)

    def selecionar_rem_excel():
        caminho = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx *.xls")])
        if caminho:
            entry_rem_arquivo.delete(0, tk.END)
            entry_rem_arquivo.insert(0, caminho)

    tk.Button(rem_top, text="Selecionar...", command=selecionar_rem_excel,
            font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
            relief="flat", padx=10, pady=5).grid(row=0, column=2, padx=5)

    rem_btns = tk.Frame(aba_rem, bg="#1e1e1e")
    rem_btns.pack(pady=6)

    rem_saida = scrolledtext.ScrolledText(
        aba_rem, width=110, height=26, bg="#2e2e2e", fg="#ffffff",
        font=("Consolas", 10), insertbackground="#ffffff"
    )
    rem_saida.pack(padx=10, pady=8, fill="both", expand=True)
    rem_saida.tag_configure("divergente", foreground="#ff4444")
    rem_saida.tag_configure("correto", foreground="#00cc66")
    rem_saida.tag_configure("divergente_sup", foreground="#00ff88", background="#1a3d2b")


    def _fmt_brl(v):
        try:
            return (f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        except Exception:
            return "R$ 0,00"

    def _executar_dinheiro_remetido():
        try:
            caminho = entry_rem_arquivo.get().strip()
            if not caminho:
                messagebox.showwarning("Aviso", "Selecione o arquivo Excel antes de executar.")
                return

            por_dia, total_periodo, total_anterior_val, total_saldo_final_val, \
            sucata_total, valor_lancado_dr, valor_ajustado, status_dr, linhas_dr = \
                calcular_dinheiro_remetido_core(caminho, "", "")

            rem_saida.delete("1.0", tk.END)

            # 1) Rodapés capturados
            if total_anterior_val is not None:
                rem_saida.insert(tk.END, f"◊ TOTAL ANTERIOR: {_fmt_brl(total_anterior_val)}\n")
            if total_saldo_final_val is not None:
                rem_saida.insert(tk.END, f"◊ TOTAL SALDO FINAL: {_fmt_brl(total_saldo_final_val)}\n")

            rem_saida.insert(tk.END, f"◊ PAGAMENTO DE SUCATA (no período): {_fmt_brl(sucata_total)}\n")
            rem_saida.insert(tk.END, "\n")

            # ── Pré-calcula quais datas da parte superior têm divergência ──
            # (compara remetido por dia vs. lançado na parte inferior)
            TOLERANCIA = 0.01
            datas_divergentes_sup = set()
            if status_dr != 'sem_lancamento' and not por_dia.empty:
                lancado_por_data = {}
                for l in linhas_dr:
                    valor_l = l['debito'] if l['debito'] != 0 else l['credito']
                    lancado_por_data[l['data']] = lancado_por_data.get(l['data'], 0.0) + float(valor_l)
                for _, r in por_dia.iterrows():
                    d_fmt = pd.to_datetime(r['data']).strftime('%d/%m/%Y')
                    esperado = float(r['remetido'])
                    lancado = lancado_por_data.get(d_fmt)
                    if lancado is None or abs(abs(lancado) - abs(esperado)) > TOLERANCIA:
                        datas_divergentes_sup.add(d_fmt)

            # 2) Remetido por dia
            rem_saida.insert(tk.END, "Remetido por dia (Débito – Crédito):\n")
            if por_dia.empty:
                rem_saida.insert(tk.END, " - (sem lançamentos no período)\n")
            else:
                for _, r in por_dia.iterrows():
                    d = pd.to_datetime(r['data']).strftime('%d/%m/%Y')
                    v = float(r['remetido'])
                    linha_sup = f" - {d}: {_fmt_brl(v)}\n"
                    if d in datas_divergentes_sup:
                        rem_saida.insert(tk.END, linha_sup, "divergente_sup")
                    else:
                        rem_saida.insert(tk.END, linha_sup)

            rem_saida.insert(tk.END, "\n")
            rem_saida.insert(tk.END, f"◊ Total remetido no período: {_fmt_brl(total_periodo)}\n")
            rem_saida.insert(tk.END, "\n")

            # 3) Status do lançamento DINHEIRO REMETIDO
            rem_saida.insert(tk.END, "─" * 55 + "\n")

            if status_dr == 'sem_lancamento':
                rem_saida.insert(tk.END,
                    "⚠ Nenhum lançamento 'DINHEIRO REMETIDO' encontrado.\n"
                    f"  👉 Valor a lançar: {_fmt_brl(valor_ajustado)}\n"
                )
            else:
                rem_saida.insert(tk.END, "◊ DINHEIRO REMETIDO lançado:\n")

                # Monta dicionário: data -> valor esperado (por_dia)
                esperado_por_data = {}
                for _, r in por_dia.iterrows():
                    d_fmt = pd.to_datetime(r['data']).strftime('%d/%m/%Y')
                    esperado_por_data[d_fmt] = float(r['remetido'])

                for l in linhas_dr:
                    sinal = "DEB" if l['debito'] != 0 else "CRED"
                    valor = l['debito'] if l['debito'] != 0 else l['credito']
                    valor_fmt = _fmt_brl(valor)
                    linha_txt = (
                        f"  {l['data']}  SEQ {l['sequencia']}  LOTE {l['lote']}  "
                        f"VOUCHER {l['voucher']}  C.PARTIDA {l['conta_partida']}  "
                        f"{sinal}: {valor_fmt}\n"
                    )
                    esperado = esperado_por_data.get(l['data'])
                    if esperado is not None and abs(abs(valor) - abs(esperado)) <= TOLERANCIA:
                        tag_linha = "correto"
                    else:
                        tag_linha = "divergente"

                    prefixo = linha_txt[:linha_txt.rfind(valor_fmt)]
                    rem_saida.insert(tk.END, prefixo)
                    rem_saida.insert(tk.END, valor_fmt + "\n", tag_linha)

                tag_total = "correto" if status_dr == 'correto' else "divergente"
                rem_saida.insert(tk.END, "\n  Total lançado: ")
                rem_saida.insert(tk.END, f"{_fmt_brl(valor_lancado_dr)}\n", tag_total)

                if status_dr == 'correto':
                    rem_saida.insert(tk.END, "  ✔ Valor confere com o calculado.\n", "correto")
                else:
                    rem_saida.insert(tk.END,
                        f"\n  ✘ Valor correto (ajustado): {_fmt_brl(valor_ajustado)}\n"
                        f"  Diferença: {_fmt_brl(abs(abs(valor_lancado_dr) - abs(valor_ajustado)))}\n"
                    )

        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao calcular Dinheiro Remetido:\n{e}")

    tk.Button(rem_btns, text="Calcular", command=_executar_dinheiro_remetido,
            font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
            relief="flat", padx=10, pady=6).grid(row=0, column=0, padx=5)

    tk.Button(rem_btns, text="Limpar", command=lambda: rem_saida.delete("1.0", tk.END),
            font=("Segoe UI", 10), bg="#8b0000", fg="#ffffff",
            relief="flat", padx=10, pady=6).grid(row=0, column=1, padx=5)

    # =========================================================
    # Aba 4: Relatórios de Notas (à vista / a prazo)
    # =========================================================
    aba_rel_notas = tk.Frame(nb, bg="#1e1e1e")
    nb.add(aba_rel_notas, text="Relatórios de Notas")

    rel_top = tk.Frame(aba_rel_notas, bg="#1e1e1e")
    rel_top.pack(fill="x", padx=10, pady=8)

    # Seletor: Notas à Vista (PDF)
    tk.Label(rel_top, text="Notas à Vista (PDF):", font=("Segoe UI", 10),
            bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="w", pady=2)
    entry_notas_vista = tk.Entry(rel_top, font=("Segoe UI", 10))
    entry_notas_vista.grid(row=0, column=1, sticky="we", padx=8, pady=2)
    rel_top.grid_columnconfigure(1, weight=1)

    def selecionar_pdf_notas(entry_widget):
        caminho = filedialog.askopenfilename(
            title="Selecione o PDF do relatório",
            filetypes=[("PDF files", "*.pdf")]
        )
        if caminho:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, caminho)

    tk.Button(
        rel_top, text="Selecionar...", command=lambda: selecionar_pdf_notas(entry_notas_vista),
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=5
    ).grid(row=0, column=2, padx=5, pady=2)

    # Seletor: Notas a Prazo (PDF)
    tk.Label(rel_top, text="Notas a Prazo (PDF):", font=("Segoe UI", 10),
            bg="#1e1e1e", fg="#ffffff").grid(row=1, column=0, sticky="w", pady=2)
    entry_notas_prazo = tk.Entry(rel_top, font=("Segoe UI", 10))
    entry_notas_prazo.grid(row=1, column=1, sticky="we", padx=8, pady=2)
    tk.Button(
        rel_top, text="Selecionar...", command=lambda: selecionar_pdf_notas(entry_notas_prazo),
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=5
    ).grid(row=1, column=2, padx=5, pady=2)

    # Botões de ação
    rel_btns = tk.Frame(aba_rel_notas, bg="#1e1e1e")
    rel_btns.pack(pady=(0, 6))

    notas_saida = scrolledtext.ScrolledText(
        aba_rel_notas, width=110, height=26, bg="#2e2e2e", fg="#ffffff",
        font=("Consolas", 10), insertbackground="#ffffff"
    )
    notas_saida.pack(padx=10, pady=8, fill="both", expand=True)

    def executar_rel_notas():
        try:
            caminho_vista = entry_notas_vista.get().strip()
            caminho_prazo = entry_notas_prazo.get().strip()

            if not caminho_vista and not caminho_prazo:
                messagebox.showwarning("Aviso", "Selecione pelo menos um PDF (à vista ou a prazo).")
                return

            # --- Leitura usando a MESMA lógica do 'Livro x Relatórios' ---
            docs_vista = extrair_notas(caminho_vista) if caminho_vista else {}
            docs_prazo = extrair_prazo(caminho_prazo) if caminho_prazo else {}

            # Totais
            total_vista = sum(parse_valor(meta.get("valor")) for meta in docs_vista.values())
            total_prazo = sum(parse_valor(meta.get("valor")) for meta in docs_prazo.values())
            total_geral = total_vista + total_prazo

            # Contagens
            qtd_vista = len(docs_vista)
            qtd_prazo = len(docs_prazo)

            # GoodCard (à vista) – cond inicia com 'GOOD'
            notas_good = []
            for key, meta in (docs_vista or {}).items():
                cond = (meta.get("cond") or "")
                if isinstance(key, tuple) and cond and cond.upper().startswith("GOOD"):
                    serie, numero = key[:2]
                    valor = parse_valor(meta.get("valor"))
                    notas_good.append((str(serie), str(numero), float(valor)))

            # Resumo por dia (à vista) – se 'data' vier no relatório
            por_dia = {}
            for key, meta in (docs_vista or {}).items():
                data = meta.get("data")
                if not data:
                    continue
                # normaliza para dd/mm
                try:
                    d = pd.to_datetime(str(data), dayfirst=True).strftime('%d/%m')
                except Exception:
                    continue
                por_dia[d] = por_dia.get(d, 0.0) + parse_valor(meta.get("valor"))

            # ---- Saída ----
            notas_saida.delete("1.0", tk.END)
            notas_saida.insert(tk.END, "=== RESUMO DO RELATÓRIO DE NOTAS ===\n\n")
            notas_saida.insert(tk.END, f"Total Notas à Vista : {_fmt(total_vista)}\n")
            notas_saida.insert(tk.END, f"Total Notas a Prazo: {_fmt(total_prazo)}\n")
            notas_saida.insert(tk.END, f"Total Geral        : {_fmt(total_geral)}\n\n")

            notas_saida.insert(tk.END, f"Qtd. de Notas à Vista : {qtd_vista}\n")
            notas_saida.insert(tk.END, f"Qtd. de Notas a Prazo : {qtd_prazo}\n\n")

            notas_saida.insert(tk.END, "=== Notas GoodCard (à vista) ===\n")
            if notas_good:
                for (serie, numero, valor) in sorted(notas_good, key=lambda x: (x[0], int(re.sub(r'\\D','', x[1]) or 0))):
                    notas_saida.insert(tk.END, f" Série {serie} - Número {numero} - Valor: {_fmt(valor)}\n")
            else:
                notas_saida.insert(tk.END, " Nenhuma nota com COND.=GOOD encontrada.\n")
            notas_saida.insert(tk.END, "\n")

            if por_dia:
                notas_saida.insert(tk.END, "=== Totais por Dia (Notas à Vista) ===\n")
                for dia in sorted(por_dia.keys(), key=lambda d: datetime.strptime(d, "%d/%m")):
                    notas_saida.insert(tk.END, f" Dia {dia}: {_fmt(por_dia[dia])}\n")
                notas_saida.insert(tk.END, "\n")

        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao resumir Relatórios de Notas:\n{e}")

    tk.Button(
        rel_btns, text="Analisar", command=executar_rel_notas,
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=6
    ).grid(row=0, column=0, padx=5)

    tk.Button(
        rel_btns, text="Limpar", command=lambda: notas_saida.delete("1.0", tk.END),
        font=("Segoe UI", 10), bg="#8b0000", fg="#ffffff",
        activebackground="#E10000", activeforeground="#ffffff",
        relief="flat", bd=0, padx=10, pady=6
    ).grid(row=0, column=1, padx=5)

    # ============================
    # Estado compartilhado (aba 1)
    # ============================
    memo_state = {
        "ok": False,
        "total_cupom": 0.0,     # NFCE (Cupom)
        "total_nota": 0.0,      # NFS (Nota)
        "total_geral_vendas": 0.0,
        "vendas_por_forma": {}, # Cupom/Nota/Total por forma
        "devolucoes_memo": {},
        "devol_memo_dinheiro": 0.0,
        "devol_memo_cartao": 0.0,
        "recebimentos": {},     # ANTECIPADOS / DUPLICATAS / DEPÓSITO
        "coligadas_memo": 0.0,
    }

    # ============================
    # Utilitário de formato
    # ============================
    def _fmt(v):
        try:
            return (f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        except Exception:
            return "R$ 0,00"

    # ============================
    # Aba 1 — Executar Memorando (mantém sua lógica)
    # ============================
    def executar_memorando(
        _extrair_tabelas=extrair_tabelas,
        _preparar_dados=preparar_dados,
        _calcular_vendas=calcular_vendas,
        _calcular_recebimentos_detalhados=calcular_recebimentos_detalhados,
        _calcular_saidas_diversas=calcular_saidas_diversas,
        _calcular_devolucoes=calcular_devolucoes_e_sucata_entradas_ordenado,
        _calcular_remessas=calcular_remessas,
    ):
        pdf_path = entry_pdf.get().strip()
        if not pdf_path:
            messagebox.showwarning("Aviso", "Selecione o arquivo PDF do memorando.")
            return

        # Loja/Data
        loja_txt = entry_loja.get().strip()
        data_txt = entry_data.get().strip()
        if (not loja_txt or not data_txt) and pdf_path:
            try:
                loja_auto, data_auto = extrair_cabecalho_memorando(pdf_path)
                if not loja_txt and loja_auto: loja_txt = loja_auto
                if not data_txt and data_auto: data_txt = data_auto
            except Exception as _e:
                print("WARN header:", _e)
        if not loja_txt or not data_txt:
            messagebox.showwarning("Campos", "Informe Loja e Data (dd/mm/aaaa).")
            return

        try:
            df_raw = _extrair_tabelas(pdf_path)
            df = _preparar_dados(df_raw)
            
            # --- [NOVO] Remessa de Dinheiro -> Supabase (mesma lógica do backup) ---
            try:
                # 1) Loja/Data a partir dos campos (fallback: cabeçalho do PDF)
                loja_txt = entry_loja.get().strip()
                data_txt = entry_data.get().strip()
                if (not loja_txt or not data_txt) and pdf_path:
                    try:
                        loja_auto, data_auto = extrair_cabecalho_memorando(pdf_path)
                        if not loja_txt and loja_auto:
                            loja_txt = loja_auto
                        if not data_txt and data_auto:
                            data_txt = data_auto
                    except Exception as _e:
                        print("WARN header (fallback loja/data):", _e)

                # Se ainda faltou algo, não interrompe o fluxo; só evita o upsert
                if loja_txt and data_txt:
                    # 2) Valor da remessa de dinheiro: soma ENTRADA onde descrição contém "REMESSA DE DINHEIRO"
                    desc = df["DESCRIÇÃO"].astype(str).str.upper().str.strip()
                    entrada = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0.0)
                    valor_remessa_dinheiro = float(
                        entrada[desc.str.contains("REMESSA DE DINHEIRO", na=False)].sum()
                    )

                    # 3) Upsert no Supabase (public.memorandos), PK: (data, loja)
                    try:
                        data_iso = _to_iso(data_txt)  # 'YYYY-MM-DD' (igual ao backup)
                        payload = {
                            "data": data_iso,
                            "loja": str(loja_txt).strip(),
                            "valor_remessa_dinheiro": int(round(valor_remessa_dinheiro)),
                            "fonte": "prisma",
                        }
                        (
                            supabase
                            .schema("public")
                            .table("memorandos")
                            .upsert(payload, on_conflict="data,loja")
                            .execute()
                        )
                        print("[MEMO] Upsert OK:", payload)
                    except Exception as e:
                        # Não bloqueia a análise; só loga o alerta
                        print("[WARN] Falha no upsert do memorando:", e)
            except Exception as e:
                print("[WARN] Remessa Dinheiro (pré-cálculo):", e)
            # --- [FIM NOVO] ---


            vendas, total_cupom, total_nota, total_geral_vendas = _calcular_vendas(df)
            receb, total_geral_receb = _calcular_recebimentos_detalhados(df)
            saidas, total_geral_saidas = _calcular_saidas_diversas(df)
            dev_sucata, _ = _calcular_devolucoes(df)
            remessas = _calcular_remessas(df, vendas, receb, saidas)
            
            valores_prazo = extrair_valores_prazo_texto(pdf_path)

            vendas_prazo_coligada = valores_prazo["vendas_prazo_coligada"]
            vendas_prazo          = valores_prazo["vendas_prazo"]
            devolucao_prazo       = valores_prazo["devolucao_prazo"]
            devolucoes_cliente_prazo = valores_prazo["devolucoes_cliente_prazo"]


            # DEV CARTAO CRED CANC PORTAL a partir do texto
            valor_portal_str = extrair_dev_cartao_canc_portal_texto(pdf_path)
            valor_portal_num = parse_brl_to_float(valor_portal_str) if valor_portal_str else 0.0
            if valor_portal_num > 0:
                dev_sucata["DEV CARTAO CRED CANC PORTAL"] = valor_portal_num

        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao processar o PDF:\n{e}")
            return

        # Impressão
        memo_saida.delete("1.0", tk.END)
        memo_saida.insert(tk.END, "=== RELATÓRIO DE VENDAS ===\n")
        ordem = ["DINHEIRO", "CARTAO CREDITO", "CARTAO DEBITO", "DEVCAR", "CARTÃO",
                 "PIX MAQUINETA", "PIX", "DEPOSITO"]
        for forma in ordem:
            if forma in vendas:
                v = vendas[forma]
                memo_saida.insert(
                    tk.END,
                    f"{forma}: Cupom = {_fmt(v.get('Cupom',0.0))} "
                    f"Nota = {_fmt(v.get('Nota',0.0))} "
                    f"Total = {_fmt(v.get('Total',0.0))}\n"
                )
        memo_saida.insert(tk.END, f"\nTotal Cupom Fiscal: {_fmt(total_cupom)}\n")
        memo_saida.insert(tk.END, f"Total Nota Fiscal: {_fmt(total_nota)}\n")
        memo_saida.insert(tk.END, f"Total Geral: {_fmt(total_geral_vendas)}\n\n")

        memo_saida.insert(tk.END, "=== RELATÓRIO DETALHADO DE RECEBIMENTOS ===\n")
        for k, val in receb.get("ANTECIPADOS", {}).items():
            memo_saida.insert(tk.END, f"Antecipado {k}: {_fmt(val)}\n")
        for k, val in receb.get("DUPLICATAS", {}).items():
            memo_saida.insert(tk.END, f"Duplicata {k}: {_fmt(val)}\n")
        memo_saida.insert(tk.END, f"Abatimento: {_fmt(receb.get('ABATIMENTO', 0.0))}\n")
        memo_saida.insert(tk.END, f"Depósito: {_fmt(receb.get('DEPÓSITO', 0.0))}\n\n")

        memo_saida.insert(tk.END, "=== RELATÓRIO DE SAÍDAS DIVERSAS ===\n")
        for k, val in saidas.items():
            memo_saida.insert(tk.END, f"{k}: {_fmt(val)}\n")
        memo_saida.insert(tk.END, "\n")

        total_consolidado = total_geral_vendas + total_geral_receb + total_geral_saidas
        memo_saida.insert(tk.END, "=== TOTAL CONSOLIDADO ===\n")
        memo_saida.insert(tk.END, f"Total Geral de Vendas: {_fmt(total_geral_vendas)}\n")
        memo_saida.insert(tk.END, f"Total Geral de Recebimentos: {_fmt(total_geral_receb)}\n")
        memo_saida.insert(tk.END, f"Total Geral de Saídas Diversas: {_fmt(total_geral_saidas)}\n")
        memo_saida.insert(tk.END, f"\nTOTAL FINAL (somatório): {_fmt(total_consolidado)}\n\n")

        memo_saida.insert(tk.END, "=== DEVOLUÇÕES, SUCATA E DESPESAS===\n\n")
        for k, val in dev_sucata.items():
            memo_saida.insert(tk.END, f"{k}: {_fmt(val)}\n")
        memo_saida.insert(tk.END, "\n")

        
        memo_saida.insert(tk.END, "\n=== VENDAS / DEVOLUÇÕES A PRAZO\n\n")
        memo_saida.insert(tk.END, f"Vendas a Prazo Coligada: { _fmt(vendas_prazo_coligada) }\n")
        memo_saida.insert(tk.END, f"Vendas a Prazo:           { _fmt(vendas_prazo) }\n")
        memo_saida.insert(tk.END, f"Devolução a Prazo:        { _fmt(devolucao_prazo) }\n")
        memo_saida.insert(tk.END, f"Devoluções Cliente Prazo: { _fmt(devolucoes_cliente_prazo) }\n\n")


        memo_saida.insert(tk.END, "=== ANÁLISE DE REMESSAS ===\n\n")
        for k, vals in remessas.items():
            dif_val = float(vals.get('Diferença', 0.0))
            if dif_val > 1.50:
                tag_rem = "rem_ok"
            elif dif_val < -1.50:
                tag_rem = "rem_err"
            else:
                tag_rem = ""
            linha_rem = (
                f"{k}: Remessa = {_fmt(vals.get('Remessa',0.0))} "
                f"Saídas = {_fmt(vals.get('Saídas',0.0))} "
                f"Diferença = {_fmt(dif_val)}\n"
            )
            memo_saida.insert(tk.END, linha_rem, tag_rem)

        # ====== SALVAR NO ESTADO PARA A CONCILIAÇÃO ======
        memo_state["ok"] = True
        memo_state["total_cupom"] = float(total_cupom)
        memo_state["total_nota"] = float(total_nota)
        memo_state["total_geral_vendas"] = float(total_geral_vendas)
        memo_state["vendas_por_forma"] = vendas

        def _get_memo_val(chave): 
            return float(dev_sucata.get(chave, 0.0))

        memo_state["devolucoes_memo"] = dev_sucata.copy()
        memo_state["devol_memo_dinheiro"] = (
            _get_memo_val("DEVOLUCOES A VISTA") +
            _get_memo_val("DEVOLUCOES DO CLIENTE A VISTA") +
            _get_memo_val("DEVOLUCAO ACERTO DE ESTOQUE")
        )
        memo_state["devol_memo_cartao"] = (
            _get_memo_val("DEVOLUCOES CARTAO CREDITO") +
            _get_memo_val("DEVOLUCOES DO CLIENTE CARTAO") +
            _get_memo_val("DEV CARTAO CRED CANC PORTAL")
        )

        memo_state["recebimentos"] = receb
        soma_antecip = sum(float(v or 0.0) for v in (receb.get("ANTECIPADOS", {}) or {}).values())
        soma_dupl   = sum(float(v or 0.0) for v in (receb.get("DUPLICATAS", {}) or {}).values())
        soma_dep_rcb= float(receb.get("DEPÓSITO", 0.0) or 0.0)
        memo_state["coligadas_memo"] = float(soma_antecip + soma_dupl + soma_dep_rcb)

        memo_state["vendas_prazo_coligada"] = vendas_prazo_coligada
        memo_state["vendas_prazo"] = vendas_prazo
        memo_state["devolucao_prazo"] = devolucao_prazo
        memo_state["devolucoes_cliente_prazo"] = devolucoes_cliente_prazo


    # Botões da Aba 1
    tk.Button(
        memo_btns, text="Executar", command=executar_memorando,
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=5
    ).grid(row=0, column=0, padx=5)
    tk.Button(
        memo_btns, text="Limpar", command=lambda: memo_saida.delete("1.0", tk.END),
        font=("Segoe UI", 10), bg="#8b0000", fg="#ffffff",
        activebackground="#E10000", activeforeground="#ffffff",
        relief="flat", bd=0, padx=10, pady=5
    ).grid(row=0, column=1, padx=5)
    tk.Button(
        memo_btns, text="Voltar", command=ao_fechar,
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=5
    ).grid(row=0, column=2, padx=5)

    # ============================
    # Conciliação — Helpers
    # ============================

    # Extrai conta de partida (ROBUSTO)
    def _extrair_conta_partida_texto(texto_col7) -> int | None:
        s = "" if texto_col7 is None else str(texto_col7)
        m = re.search(r'^\s*\d+\s*-\s*(\d{1,4})\b', s)
        if m:
            try: return int(m.group(1))
            except: pass
        m = re.search(r'\((\d{1,4})\)', s)
        if m:
            try: return int(m.group(1))
            except: pass
        m = re.search(r'(?:CONTA|PARTIDA)\D*(\d{1,4})', s, flags=re.I)
        if m:
            try: return int(m.group(1))
            except: pass
        todos = re.findall(r'(\d{1,4})', s)
        if todos:
            try: return int(todos[-1])
            except: pass
        return None

    # === NOVO: devoluções por contabilidade (bloco 446, DÉBITO), só Dinheiro/Cartão
    def _sumarizar_devolucoes_contab_det(caminho_excel: str):
        res = {"dinheiro": 0.0, "cartao": 0.0}
        if not caminho_excel:
            return res
        try:
            df = pd.read_excel(caminho_excel, header=None, engine='xlrd')
            COL_HIST, COL_CONTA, COL_DEBITO = 5, 7, 8
            formas_pagamento = { 6: "Dinheiro", 2157: "Cartão", 1253: "Cartão", 1179: "Cartão" }
            bloco_devol = False
            for _, row in df.iterrows():
                # ✅ Verifica SOMENTE a coluna de conta (col 7), não o histórico
                # Isso evita falso positivo com NFs como "44680" que contêm "446"
                col_conta_txt = ("" if pd.isna(row[COL_CONTA]) else str(row[COL_CONTA])).upper()

                # Detecta cabeçalho real do bloco 446
                if re.search(r'\b446\b', col_conta_txt) and "DEVOLU" in col_conta_txt:
                    bloco_devol = True
                    continue

                # Fecha o bloco ao encontrar linha de total
                if bloco_devol and ("TOTAL DEB" in col_conta_txt or "TOTAL CRED" in col_conta_txt):
                    bloco_devol = False
                    continue

                # Detecta início de outro bloco (ex: 417, 418, etc.) → fecha 446
                if bloco_devol and re.search(r'\b(41[0-9]|42[0-9]|43[0-9]|44[0-9]|45[0-9])\b', col_conta_txt):
                    if re.search(r'\b(417|418|419|420|421|422|430)\b', col_conta_txt):
                        bloco_devol = False
                        continue

                if not bloco_devol:
                    continue

                valor = pd.to_numeric(row[COL_DEBITO], errors="coerce")
                if pd.isna(valor) or float(valor) == 0.0:
                    continue
                conta_partida = _extrair_conta_partida_texto(row[COL_CONTA])
                forma = formas_pagamento.get(conta_partida, None)
                if forma == "Dinheiro":
                    res["dinheiro"] += float(valor)
                elif forma == "Cartão":
                    res["cartao"] += float(valor)
            return res
        except Exception as e:
            print("Erro devolução contab:", e)
            return res

    # === NOVO: cálculo de Notas (à vista / a prazo) a partir do Excel
    #           usando a MESMA LÓGICA da conciliação (DOC<=100 Cupom; DOC>100 Nota).
    def calcular_notas_excel_prisma(caminho_excel: str):
        if not caminho_excel:
            return 0.0, 0.0, 0.0

        df = pd.read_excel(caminho_excel, header=None, engine='xlrd')
        COL_DOC, COL_HIST, COL_CONTA, COL_CRED = 4, 5, 7, 9
        CONTAS_VISTA, CONTAS_PRAZO, CONTA_DEVOL = {417,419,421,422}, {418,420,430}, 446

        def extrair_num_bloco(texto):
            if not isinstance(texto, str):
                texto = "" if pd.isna(texto) else str(texto)
            t = texto.upper()
            for padrao in [r'\((\d{3,4})\)', r'CONTA\s*(\d{3,4})', r'\b(\d{3,4})\s*-\s*']:
                m = re.search(padrao, t)
                if m:
                    try: return int(m.group(1))
                    except: pass
            return None

        def classificar_cupom_nota(doc_raw, conta_partida=None):
            # >>> FORÇAR 1179/1253 COMO NOTA FISCAL <<<
            if conta_partida in (1253, 1179):
                return "Nota Fiscal"
            # regra numérica padrão
            toks = re.findall(r'\d+', str(doc_raw))
            if toks:
                try:
                    num = int(toks[-1])
                    return "Cupom Fiscal" if 1 <= num <= 100 else "Nota Fiscal"
                except:
                    pass
            return None

        bloco_atual, conta_bloco_atual = None, None
        tot_vista, tot_prazo = 0.0, 0.0

        for _, row in df.iterrows():
            texto_bloco = (("" if pd.isna(row[COL_CONTA]) else str(row[COL_CONTA])) + " " +
                           ("" if pd.isna(row[COL_HIST]) else str(row[COL_HIST]))).upper()
            if any(x in texto_bloco for x in ["VENDA", "DEVOLU", "CONTA "]):
                nb = extrair_num_bloco(texto_bloco)
                if nb in (CONTAS_VISTA | CONTAS_PRAZO | {CONTA_DEVOL}):
                    bloco_atual, conta_bloco_atual = texto_bloco.strip(), nb
                    continue
            if conta_bloco_atual is None:
                continue
            cp_txt = "" if pd.isna(row[COL_CONTA]) else str(row[COL_CONTA]).upper()
            if "TOTAL DEB" in cp_txt or "TOTAL CRED" in cp_txt:
                continue
            if conta_bloco_atual == CONTA_DEVOL:
                continue

            valor = pd.to_numeric(row[COL_CRED], errors="coerce")
            if pd.isna(valor) or float(valor) == 0.0:
                continue
            valor = float(valor)

            conta_partida = _extrair_conta_partida_texto(row[COL_CONTA])  # sua helper já existente
            tipo_doc = classificar_cupom_nota(row[COL_DOC], conta_partida)

            if tipo_doc != "Nota Fiscal":
                continue

            if conta_bloco_atual in CONTAS_VISTA:
                tot_vista += valor
            elif conta_bloco_atual in CONTAS_PRAZO:
                tot_prazo += valor

        return tot_vista, tot_prazo, tot_vista + tot_prazo


    def calcular_prazo_contab(caminho_excel: str):
        """
        Lê o Excel de contabilidade e consolida valores 'a prazo' para comparação com o Memorando:
        - vendas_prazo_coligada (CRED)  -> contas de 'prazo' com conta-partida = 1022 (Coligadas)
        - vendas_prazo (CRED)           -> contas de 'prazo' com conta-partida = 1698 (Venda a Prazo)
        - devolucao_prazo (DEB)         -> bloco 446 (Devolução), conta-partida = 1698
        - devolucoes_cliente_prazo (DEB)-> bloco 446 (Devolução), conta-partida = 1698 e descrição contém 'DEVOLUCOES DO CLIENTE'

        Usa a mesma lógica de blocos do app:
        - CONTAS_VISTA = {417, 419, 421, 422}
        - CONTAS_PRAZO = {418, 420, 430}
        - CONTA_DEVOL  = 446

        Retorna dict com floats.
        """
        resultados = {
            "vendas_prazo_coligada": 0.0,
            "vendas_prazo": 0.0,
            "devolucao_prazo": 0.0,
            "devolucoes_cliente_prazo": 0.0,
        }
        if not caminho_excel:
            return resultados

        try:
            # Usa o mesmo padrão de leitura já utilizado em outras funções
            try:
                df = pd.read_excel(caminho_excel, header=None, engine='xlrd')
            except Exception:
                df = pd.read_excel(caminho_excel, header=None)

            # Colunas padrão do seu layout
            COL_DOC, COL_HIST, COL_CONTA, COL_DEB, COL_CRED = 4, 5, 7, 8, 9

            # Conjuntos de contas por bloco (conforme sua lógica)
            CONTAS_VISTA = {417, 419, 421, 422}
            CONTAS_PRAZO = {418, 420, 430}
            CONTA_DEVOL  = 446

            # Contas/formas (mesma convenção da aplicação)
            # 1698: "Venda a Prazo"; 1022: "Coligadas"
            CONTA_PARTIDA_PRAZO = 1698
            CONTA_PARTIDA_COLIG = 1022

            # ---- Helpers internos (reuso dos seus padrões) ----
            def _extrair_conta_partida_texto(texto_col7) -> int | None:
                s = "" if texto_col7 is None else str(texto_col7)
                m = re.search(r'^\s*\d+\s*-\s*(\d{1,4})\b', s)
                if m:
                    try: return int(m.group(1))
                    except: pass
                m = re.search(r'\((\d{1,4})\)', s)
                if m:
                    try: return int(m.group(1))
                    except: pass
                m = re.search(r'(?:CONTA|PARTIDA)\D*(\d{1,4})', s, flags=re.I)
                if m:
                    try: return int(m.group(1))
                    except: pass
                todos = re.findall(r'(\d{1,4})', s)
                if todos:
                    try: return int(todos[-1])
                    except: pass
                return None

            def _extrair_num_bloco(texto):
                t = "" if pd.isna(texto) else str(texto)
                u = t.upper()
                for padrao in [r'\((\d{3,4})\)', r'CONTA\s*(\d{3,4})', r'\b(\d{3,4})\s*-\s*']:
                    m = re.search(padrao, u)
                    if m:
                        try: return int(m.group(1))
                        except: pass
                return None

            # ---- Varredura seguindo a sua lógica de blocos ----
            bloco_atual = None  # número da conta do bloco (ex.: 418, 420, 430, 446)
            for _, row in df.iterrows():
                # Detecta início/troca de bloco pelo cabeçalho da linha (col 7 + col 5)
                texto_bloco = (
                    ("" if pd.isna(row[COL_CONTA]) else str(row[COL_CONTA])) + " " +
                    ("" if pd.isna(row[COL_HIST]) else str(row[COL_HIST]))
                ).upper()

                if any(x in texto_bloco for x in ["VENDA", "DEVOLU", "CONTA "]):
                    nb = _extrair_num_bloco(texto_bloco)
                    if nb in (CONTAS_VISTA | CONTAS_PRAZO | {CONTA_DEVOL}):
                        bloco_atual = nb
                        continue

                if bloco_atual is None:
                    continue

                # Ignora somatórios
                cp_txt = "" if pd.isna(row[COL_CONTA]) else str(row[COL_CONTA]).upper()
                if "TOTAL DEB" in cp_txt or "TOTAL CRED" in cp_txt:
                    continue

                # Valores
                valor_deb = pd.to_numeric(row[COL_DEB], errors="coerce")
                valor_cred = pd.to_numeric(row[COL_CRED], errors="coerce")
                if (pd.isna(valor_deb) or float(valor_deb) == 0.0) and (pd.isna(valor_cred) or float(valor_cred) == 0.0):
                    continue

                conta_partida = _extrair_conta_partida_texto(row[COL_CONTA])
                desc_hist = "" if pd.isna(row[COL_HIST]) else str(row[COL_HIST]).upper()

                # ---- Regras ----
                # 1) VENDAS A PRAZO COLIGADA: blocos de PRAZO (418/420/430), somar CRED, conta-partida = 1022
                if bloco_atual in CONTAS_PRAZO and not pd.isna(valor_cred) and float(valor_cred) != 0.0:
                    if conta_partida == CONTA_PARTIDA_COLIG:
                        resultados["vendas_prazo_coligada"] += float(valor_cred)

                    # 2) VENDAS A PRAZO: blocos de PRAZO (418/420/430), somar CRED, conta-partida = 1698
                    if conta_partida == CONTA_PARTIDA_PRAZO:
                        resultados["vendas_prazo"] += float(valor_cred)

                # 3) DEVOLUCAO A PRAZO: bloco 446, somar DEB, conta-partida = 1698
                if bloco_atual == CONTA_DEVOL and not pd.isna(valor_deb) and float(valor_deb) != 0.0:
                    if conta_partida == CONTA_PARTIDA_PRAZO:
                        if "DEV." in desc_hist:
                            resultados["devolucao_prazo"] += float(valor_deb)

                        # 4) DEVOLUCOES DO CLIENTE A PRAZO: mesmo critério + descrição contendo 'DEVOLUCOES DO CLIENTE'
                        if "VENDAS" in desc_hist:
                            resultados["devolucoes_cliente_prazo"] += float(valor_deb)

            return resultados

        except Exception as e:
            print("Erro em calcular_prazo_contab:", e)
            return resultados
  

    # Conciliação — sumarizador básico (mantido; mas vamos filtrar formas na exibição)
    def _sumarizar_contab_por_forma_e_tipo_basico(caminho_excel: str):

        por_forma_total, por_forma_tipo = {}, {}
        primeira_linha = {}
        linhas_por_forma_tipo = {}

        if not caminho_excel:
            return por_forma_total, por_forma_tipo, primeira_linha, linhas_por_forma_tipo

        try:
            try:
                df = pd.read_excel(caminho_excel, header=None, engine="xlrd")
            except Exception:
                df = pd.read_excel(caminho_excel, header=None)

            COL_DOC  = 4
            COL_HIST = 5
            COL_CONTA = 7
            COL_CRED = 9
            COL_SEQ = 1

            formas_pagamento = {
                6: "Dinheiro",
                11: "Pix QrCode",
                2074: "Pix Maquineta",
                1698: "Venda a Prazo",
                1022: "Coligadas",
                2157: "Cartão",
                1253: "GoodCard",
                1179: "Cartão Link",
                989: "Depósito",
                1677: "Nota Fiscal Depósito",
                1575: "Transitórias",
            }

            alias_forma = {
                "Nota Fiscal Depósito": "Depósito",
                "GoodCard": "Cartão",
                "Cartão Link": "Cartão",
            }

            bloco_atual = None

            for _, row in df.iterrows():

                # ===== DETECTA BLOCO =====
                texto_bloco = (
                    ("" if pd.isna(row[COL_CONTA]) else str(row[COL_CONTA])) + " " +
                    ("" if pd.isna(row[COL_HIST]) else str(row[COL_HIST]))
                ).upper()

                m = re.search(r'\b(4\d{2})\s*-', texto_bloco)
                if m:
                    bloco_atual = int(m.group(1))

                # ===== VALOR =====
                valor = pd.to_numeric(row[COL_CRED], errors="coerce")
                if pd.isna(valor) or float(valor) == 0.0:
                    continue

                linha_txt = " ".join(str(x) for x in row.values).upper()
                if any(cfop in linha_txt for cfop in CFOPS_EXCLUIDOS):
                    continue

                doc_raw   = row[COL_DOC]
                conta_txt = row[COL_CONTA]
                conta_num = _extrair_conta_partida_texto(conta_txt)

                # ===== TIPO =====
                def _nf_por_1575(doc_str):
                    try:
                        s = str(doc_str)
                        m2 = re.search(r'(\d+)\s*-\s*(\d{3})\s*-?', s)
                        if not m2:
                            return None
                        codigo = int(m2.group(2))
                        return "NFCE" if codigo < 25 else "NFS"
                    except:
                        return None

                texto_val = unidecode(str(conta_txt) if conta_txt is not None else "").upper()

                if conta_num == 1575:
                    tipo = _nf_por_1575(doc_raw) or "NFS"
                elif "COLIGADAS" in linha_txt or (conta_num in (1022, 430)):
                    tipo = "NFS"
                elif conta_num == 989 or "DEPOSITO" in texto_val:
                    tipo = "NFCE"
                else:
                    toks = re.findall(r'\d+', str(doc_raw))
                    if toks:
                        try:
                            nro = int(toks[-1])
                            tipo = "NFCE" if 1 <= nro <= 100 else "NFS"
                        except:
                            tipo = "NFS"
                    else:
                        tipo = "NFS"

                if conta_num == 1677:
                    tipo = "NFS"

                # ===== FORMA NORMALIZADA (AGORA CERTO PRA MATCH) =====
                forma_base = formas_pagamento.get(conta_num, "Outros")
                forma_norm = alias_forma.get(forma_base, forma_base)

                # 🔥 NORMALIZA DEFINITIVO (evita erro de Cartão)
                if forma_norm in ("GoodCard", "Cartão Link"):
                    forma_norm = "Cartão"

                # ===== ACUMULA =====
                por_forma_total[forma_norm] = por_forma_total.get(forma_norm, 0.0) + float(valor)
                por_forma_tipo.setdefault(forma_norm, {"NFCE": 0.0, "NFS": 0.0})
                por_forma_tipo[forma_norm][tipo] += float(valor)

                # ===== GUARDA TODAS AS LINHAS (AGORA FUNCIONA 100%) =====
                linhas_por_forma_tipo \
                    .setdefault(forma_norm, {}) \
                    .setdefault(tipo, []) \
                    .append({
                        "valor": float(valor),
                        "seq": str(row[COL_SEQ]).strip() if not pd.isna(row[COL_SEQ]) else "—"
                    })

                # ===== PRIMEIRA LINHA (419 / 422) =====
                if bloco_atual in (419, 422):
                    if forma_norm not in primeira_linha:
                        seq = str(row[COL_SEQ]).strip() if not pd.isna(row[COL_SEQ]) else "—"
                        primeira_linha[forma_norm] = (seq, float(valor))

            return por_forma_total, por_forma_tipo, primeira_linha, linhas_por_forma_tipo

        except Exception as e:
            try:
                messagebox.showerror("Erro", f"Falha ao ler a contabilidade (básico):\n{e}")
            except:
                print("Falha ao ler a contabilidade (básico):", e)
            return {}, {}, {}, {}

    # ============================
    # Conciliação — Execução
    # ============================

    def executar_conciliacao():
        def _ins_div(texto, c_val, m_val, tolerancia=0.01):
            """Insere linha no conf_saida; se houver divergência, pinta de vermelho."""
            tag = "div_red" if abs(c_val - m_val) > tolerancia else ""
            conf_saida.insert(tk.END, texto, tag)        
        conf_saida.delete("1.0", tk.END)

        if not memo_state["ok"]:
            conf_saida.insert(tk.END, "⚠️ Rode primeiro a aba 'Memorando' para gerar a base de comparação.\n\n")
        
        caminho_excel = entry_contab.get().strip()
        if not caminho_excel:
            conf_saida.insert(tk.END, "ℹ️ Nenhum Excel de contabilidade selecionado. Usando 0,00 para a contabilidade.\n\n")

        # ====================================================
        # (A) Por FORMA/TIPO – leitura básica
        # ====================================================
        por_forma_total_b, por_forma_tipo_b, primeira_linha_b, linhas_por_forma_tipo = _sumarizar_contab_por_forma_e_tipo_basico(caminho_excel)

        # --- NFCE ---
        FORMAS_EXCLUIDAS = {"GoodCard", "Coligadas", "Cartão Link"}
        c_nfce = 0.0
        for forma, tipos in por_forma_tipo_b.items():
            if forma in FORMAS_EXCLUIDAS:
                continue
            c_nfce += float(tipos.get("NFCE", 0.0))

        # --- NFS CONTABILIDADE (corrigido) ---
        n_vista, n_prazo, n_total = calcular_notas_excel_prisma(caminho_excel)
        c_nfs = n_total

        # --- MEMORANDO ---
        m_nfce = float(memo_state["total_cupom"])
        m_nfs = float(memo_state["total_nota"]) + float(memo_state.get("vendas_prazo", 0.0))

        # ====================================================
        # IMPRIME CABEÇALHO NFCE / NFS
        # ====================================================
        conf_saida.insert(tk.END, "==== Contabilidade x Memorando ====\n\n")
        conf_saida.insert(tk.END, f"Cupons - Contabillidade: {_fmt(c_nfce)}\n")
        conf_saida.insert(tk.END, f"Cupons - Memorando: {_fmt(m_nfce)}\n")
        conf_saida.insert(tk.END, f"Variação - Cupons: {_fmt(c_nfce - m_nfce)}\n\n")

        conf_saida.insert(tk.END, f"Notas Fiscais - Contabilidade: {_fmt(c_nfs)}\n")
        conf_saida.insert(tk.END, f"Notas Fiscais - Memorando: {_fmt(m_nfs)}\n")
        conf_saida.insert(tk.END, f"Variação - Notas Fiscais: {_fmt(c_nfs - m_nfs)}\n\n")

        total_c = c_nfce + c_nfs
        total_m = m_nfce + m_nfs

        conf_saida.insert(tk.END, "------------------------------------------------\n")
        conf_saida.insert(tk.END, f"Total - Contabilidade: { _fmt(total_c) }\n")
        conf_saida.insert(tk.END, f"Total - Memorando: { _fmt(total_m) }\n")
        conf_saida.insert(tk.END, f"Variação - Total: { _fmt(total_c - total_m) }\n\n")

        # ====================================================
        # (B) POR FORMA DE PAGAMENTO
        # ====================================================
        conf_saida.insert(tk.END, "\n==== Conciliação por Forma de Pagamento ====\n\n")

        vendas_memo = memo_state.get("vendas_por_forma", {}) or {}

        mapa_formas = [
            ("Dinheiro", "DINHEIRO"),
            ("Pix QrCode", "PIX"),
            ("Pix Maquineta", "PIX MAQUINETA"),
            ("Depósito", "DEPOSITO"),
            ("Cartão", "CARTÃO"),
        ]

        EXCLUIR_FORMAS = {"Transitórias", "Reembolso Manaus"}

        def _memo_total(forma_key_memo):
            d = vendas_memo.get(forma_key_memo, {})
            return float(d.get("Total", 0.0))

        for contab_key, memo_key in mapa_formas:
            if contab_key in EXCLUIR_FORMAS:
                continue
            v_cont = float(por_forma_total_b.get(contab_key, 0.0))
            v_memo = _memo_total(memo_key)

            conf_saida.insert(tk.END, f"{contab_key:>22} (Contab): {_fmt(v_cont)}\n")
            conf_saida.insert(tk.END, f"{memo_key:>22} (Memorando): {_fmt(v_memo)}\n")
            
            conf_saida.insert(tk.END, f"{('Δ ' + contab_key):>22}: {_fmt(v_cont - v_memo)}\n\n")

        # ====================================================
        # (C) POR TIPO (Cupom / Nota)
        # ====================================================
        conf_saida.insert(tk.END, "\n---- Conciliação por Tipo de Pagamento ----\n\n")

        EXCLUIR_POR_TIPO = {
            "Transitórias", "Reembolso Manaus", "Outros",
            "Coligadas", "GoodCard", "Cartão Link"
        }

        for contab_key, memo_key in mapa_formas:
            if contab_key in EXCLUIR_POR_TIPO:
                continue
            
            tipos = por_forma_tipo_b.get(contab_key, {"NFCE": 0.0, "NFS": 0.0})

            c_nfce_f = float(tipos.get("NFCE", 0.0))
            c_nfs_f  = float(tipos.get("NFS", 0.0))

            m_cupom = float((vendas_memo.get(memo_key, {}) or {}).get("Cupom", 0.0))
            m_nota  = float((vendas_memo.get(memo_key, {}) or {}).get("Nota", 0.0))

            if any([c_nfce_f, c_nfs_f, m_cupom, m_nota]):
                delta_nfce = c_nfce_f - m_cupom
                delta_nfs  = c_nfs_f  - m_nota
            
                conf_saida.insert(tk.END, f"[{contab_key}]\n")
                conf_saida.insert(tk.END, f" NFCE (Contab): {_fmt(c_nfce_f)}   | Cupom (Memo): {_fmt(m_cupom)}   | Δ: {_fmt(delta_nfce)}\n")

                if abs(delta_nfce) > 0.01 and contab_key in primeira_linha_b:

                    seq, val_seq = primeira_linha_b[contab_key]
                    valor_ajustado = val_seq - delta_nfce

                    conf_saida.insert(
                        tk.END,
                        f" ↳ 1ª linha (NFCE) — Sequência: {seq} | "
                        f"Valor: {_fmt(val_seq)} | Ajustado: {_fmt(valor_ajustado)}\n",
                        "div_red"
                    )

                conf_saida.insert(tk.END, f" NFS (Contab):  {_fmt(c_nfs_f)}   | Nota (Memo):  {_fmt(m_nota)}    | Δ: {_fmt(delta_nfs)}\n")
                # 🔥 BUSCA AUTOMÁTICA DA LINHA (QUANDO SOBRA NA CONTABILIDADE)
                match = None

                if delta_nfs > 0:
                    for tipo_busca in ("NFS", "NFCE"):  # busca nos dois tipos
                        for item in linhas_por_forma_tipo.get(contab_key, {}).get(tipo_busca, []):
                            if abs(item["valor"] - delta_nfs) < 0.01:
                                match = item
                                break
                        if match:
                            break

                # ✅ EXIBE SE ENCONTRAR
                if match:
                    conf_saida.insert(
                        tk.END,
                        f" ✔ Linha correspondente encontrada — Sequência: {match['seq']} | Valor: {_fmt(match['valor'])}\n"
                    )
                

                conf_saida.insert(tk.END, "\n")
        # ====================================================
        # (D) DEVOLUÇÕES — DETALHADO (446 / DÉBITO)
        # ====================================================
        conf_saida.insert(tk.END, "\n==== Devoluções à Vista ====\n\n")

        devol_contab = {"dinheiro": 0.0, "cartao": 0.0}
        try:
            devol_contab = _sumarizar_devolucoes_contab_det(caminho_excel)
        except:
            pass

        c_dev_din = float(devol_contab.get("dinheiro", 0.0))
        c_dev_car = float(devol_contab.get("cartao", 0.0))

        m_dev_din = float(memo_state.get("devol_memo_dinheiro", 0.0))
        m_dev_car = float(memo_state.get("devol_memo_cartao", 0.0))

        delta_dev_din = c_dev_din - m_dev_din
        delta_dev_car = c_dev_car - m_dev_car

        conf_saida.insert(tk.END, f"Devolução (Dinheiro) — Contab: {_fmt(c_dev_din)}\n")
        conf_saida.insert(tk.END, f"Devolução (Dinheiro) — Memo  : {_fmt(m_dev_din)}\n")
        conf_saida.insert(tk.END, f"Δ Dinheiro (Devolução): {_fmt(delta_dev_din)}\n\n")

        conf_saida.insert(tk.END, f"Devolução (Cartão) — Contab: {_fmt(c_dev_car)}\n")
        conf_saida.insert(tk.END, f"Devolução (Cartão) — Memo  : {_fmt(m_dev_car)}\n")
        conf_saida.insert(tk.END, f"Δ Cartão (Devolução): {_fmt(delta_dev_car)}\n")

        # 🔥 ALERTA POP-UP
        if abs(delta_dev_din) > 0.01 or abs(delta_dev_car) > 0.01:
            messagebox.showwarning(
                "Alerta de Divergência",
                "⚠️ Foi identificada diferença nas devoluções.\n\n"
                f"Dinheiro: {_fmt(delta_dev_din)}\n"
                f"Cartão: {_fmt(delta_dev_car)}"
            )

        # ====================================================
        # (E)  NOVA ETAPA — "A PRAZO"
        # ====================================================
        conf_saida.insert(tk.END, "\n==== Devoluções e Vendas a Prazo ====\n\n")

        contab_prazo = calcular_prazo_contab(caminho_excel)

        c_vpc = float(contab_prazo.get("vendas_prazo_coligada", 0.0))
        c_vp  = float(contab_prazo.get("vendas_prazo", 0.0))
        c_dp  = float(contab_prazo.get("devolucao_prazo", 0.0))
        c_dcp = float(contab_prazo.get("devolucoes_cliente_prazo", 0.0))

        m_vpc = float(memo_state.get("vendas_prazo_coligada", 0.0))
        m_vp  = float(memo_state.get("vendas_prazo", 0.0))
        m_dp  = float(memo_state.get("devolucao_prazo", 0.0))
        m_dcp = float(memo_state.get("devolucoes_cliente_prazo", 0.0))

        linhas = [
            ("Vendas a Prazo Coligada", c_vpc, m_vpc),
            ("Vendas a Prazo",         c_vp,  m_vp),
            ("Devolução a Prazo",      c_dp,  m_dp),
            ("Devoluções Cliente Prazo", c_dcp, m_dcp),
        ]


        tem_diferenca_prazo = False
        mensagem_alerta = ""

        for rot, c_val, m_val in linhas:
            delta = c_val - m_val

            conf_saida.insert(tk.END, f"{rot} — Contab: {_fmt(c_val)}\n")
            conf_saida.insert(tk.END, f"{rot} — Memo  : {_fmt(m_val)}\n")
            conf_saida.insert(tk.END, f"Δ {rot}: {_fmt(delta)}\n\n",
                              "div_red" if abs(delta) > 0.01 else "")

            # 🔥 Só alerta para devoluções específicas
            if rot in ("Devolução a Prazo", "Devoluções Cliente Prazo"):
                if abs(delta) > 0.01:
                    tem_diferenca_prazo = True
                    mensagem_alerta += f"{rot}: {_fmt(delta)}\n"

        # 🔥 ALERTA APENAS PARA DEVOLUÇÕES A PRAZO
        if tem_diferenca_prazo:
            messagebox.showwarning(
                "Alerta de Divergência - Devolução a Prazo",
                "⚠️ Diferença identificada nas devoluções a prazo.\n\n"
                + mensagem_alerta
            )



    # Botões da Aba 2
    tk.Button(
        conf_btns, text="Conciliar", command=executar_conciliacao,
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=6
    ).grid(row=0, column=0, padx=5)
    tk.Button(
        conf_btns, text="Limpar", command=lambda: conf_saida.delete("1.0", tk.END),
        font=("Segoe UI", 10), bg="#8b0000", fg="#ffffff",
        activebackground="#E10000", activeforeground="#ffffff",
        relief="flat", bd=0, padx=10, pady=6
    ).grid(row=0, column=1, padx=5)

def iniciar_calculo_dinheiro_remetido():
    def selecionar_arquivo():
        caminho = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx *.xls")])
        if caminho:
            entry_arquivo.delete(0, tk.END)
            entry_arquivo.insert(0, caminho)            
            
    def executar():
        try:
            caminho = entry_arquivo.get()
            if not caminho:
                messagebox.showwarning("Aviso", "Selecione o arquivo antes de executar.")
                return

            df = pd.read_excel(caminho, header=None)
            COL_DATA = 0
            COL_HISTORICO = 5
            COL_CONTA_PARTIDA = 7
            COL_DEBITO = 8
            COL_CREDITO = 9
            COL_E = 2        
            COL_G = 6       

            from pandas.api.types import is_numeric_dtype
            def to_number_safe(col):
                if is_numeric_dtype(col):
                    return pd.to_numeric(col, errors='coerce')
                s = (col.astype(str)
                        .str.strip()
                        .str.replace(r'[^\d,.\-]', '', regex=True))
                has_comma = s.str.contains(',', regex=False)
                has_dot = s.str.contains('.', regex=False)
                mask_both = has_comma & has_dot
                s.loc[mask_both] = (s.loc[mask_both]
                                    .str.replace('.', '', regex=False)
                                    .str.replace(',', '.', regex=False))
                mask_only_comma = has_comma & ~has_dot
                s.loc[mask_only_comma] = s.loc[mask_only_comma].str.replace(',', '.', regex=False)
                return pd.to_numeric(s, errors='coerce')

            deb   = to_number_safe(df[COL_DEBITO])
            cred  = to_number_safe(df[COL_CREDITO])
            col_E = to_number_safe(df[COL_E]) 
            col_G = to_number_safe(df[COL_G])  


            linha_texto = (
                df.apply(lambda s: " ".join("" if pd.isna(v) else str(v) for v in s), axis=1)
                .str.upper()
                .str.strip()
            )


            mask_total_anterior = linha_texto.str.contains(r'\bTOTAL\s+ANTERIOR\b', regex=True, na=False)
            mask_saldo_final   = linha_texto.str.contains(r'\bTOTAL\s+SALDO\s+FINAL\b', regex=True, na=False)
            eh_dev_total       = linha_texto.str.contains(r'\bDEV\.?\s*TOTAL\b', regex=True, na=False)

            deb  = to_number_safe(df[COL_DEBITO])
            cred = to_number_safe(df[COL_CREDITO])
            col_G = to_number_safe(df[6])  

            if mask_total_anterior.any():
                total_anterior_val = float(col_E.where(mask_total_anterior).sum())
            else:
                total_anterior_val = None

            if mask_saldo_final.any():
                total_saldo_final_val = float(col_G.where(mask_saldo_final).sum())
            else:
                total_saldo_final_val = None

            padrao_rodape = (
                r'(?:^\s)TOTAL\s+ANTERIOR\b'
                r'|(?:^\s)TOTAL\s+SALDO\s+FINAL\b'
                r'|(?:^\s)TOTAL\s+DEB\s*/\s*CRED\b'
                r'|^\s*TOTAL\s'
            )
            eh_rodape = linha_texto.str.contains(padrao_rodape, regex=True, na=False) & ~eh_dev_total

            mask_valor = ((deb.notna() & (deb != 0)) | (cred.notna() & (cred != 0)))
            mask_valid = mask_valor & ~eh_rodape

            deb = deb.where(mask_valid, other=0.0).fillna(0.0)
            cred = cred.where(mask_valid, other=0.0).fillna(0.0)

            datas_raw = df[COL_DATA]
            if not pd.api.types.is_datetime64_any_dtype(datas_raw):
                datas = pd.to_datetime(datas_raw, errors='coerce', dayfirst=True)
            else:
                datas = pd.to_datetime(datas_raw)

            ini_str = entry_data_ini.get().strip() if 'entry_data_ini' in locals() or 'entry_data_ini' in globals() else ''
            fim_str = entry_data_fim.get().strip() if 'entry_data_fim' in locals() or 'entry_data_fim' in globals() else ''
            data_ini = pd.to_datetime(ini_str, dayfirst=True, errors='coerce') if ini_str else None
            data_fim = pd.to_datetime(fim_str, dayfirst=True, errors='coerce') if fim_str else None

            if ini_str and pd.isna(data_ini):
                messagebox.showwarning("Aviso", "Data inicial inválida. Use dd/mm/aaaa.")
                return
            if fim_str and pd.isna(data_fim):
                messagebox.showwarning("Aviso", "Data final inválida. Use dd/mm/aaaa.")
                return

            mask_data = datas.notna()
            if data_ini is not None:
                mask_data &= datas >= data_ini
            if data_fim is not None:
                mask_data &= datas <= data_fim

            deb_filtrado = deb.where(mask_data, other=0.0)
            cred_filtrado = cred.where(mask_data, other=0.0)

            df_calc = pd.DataFrame({
                'data': datas.dt.date,
                'deb': deb_filtrado,
                'cred': cred_filtrado
            })
            df_calc = df_calc[df_calc['data'].notna() & ((df_calc['deb'] != 0) | (df_calc['cred'] != 0))]

            por_dia = (
                df_calc
                .groupby('data')
                .apply(lambda x: float(x['deb'].sum() - x['cred'].sum()))
                .reset_index(name='remetido')
            )
            total_periodo = float(por_dia['remetido'].sum()) if not por_dia.empty else 0.0

            # --- SAÍDA ---
            
            saida.delete(1.0, tk.END)

            # >>> SE VOCÊ DECIDIR REUTILIZAR O NÚCLEO (recomendado):
            # from ... use a função já pronta, com período vindo dos campos (se existirem)
            por_dia, total_periodo, total_anterior_val, total_saldo_final_val, sucata_total = \
                calcular_dinheiro_remetido_core(caminho, ini_str, fim_str)

            # Cabeçalho do período
            if data_ini or data_fim:
                i_txt = data_ini.strftime('%d/%m/%Y') if data_ini is not None else 'início'
                f_txt = data_fim.strftime('%d/%m/%Y') if data_fim is not None else 'fim'
                saida.insert(tk.END, f"📅 Período: {i_txt} → {f_txt}\n\n")

            # Mostrar rodapés capturados, se encontrados
            def fmt_brl(v):
                return (f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

            if total_anterior_val is not None or total_saldo_final_val is not None:
                if total_anterior_val is not None:
                    saida.insert(tk.END, f"🔹 TOTAL ANTERIOR: R$ {fmt_brl(total_anterior_val)}\n")
                else:
                    saida.insert(tk.END, "🔹 TOTAL ANTERIOR: (não encontrado)\n")
                if total_saldo_final_val is not None:
                    saida.insert(tk.END, f"🔹 TOTAL SALDO FINAL: R$ {fmt_brl(total_saldo_final_val)}\n")
                else:
                    saida.insert(tk.END, "🔹 TOTAL SALDO FINAL: (não encontrado)\n")
                saida.insert(tk.END, "\n")

            saida.insert(tk.END, f"🔹 PAGAMENTO DE SUCATA (no período): R$ {fmt_brl(sucata_total)}\n\n")

            # Lista por dia
            if por_dia.empty:
                saida.insert(tk.END, "Nenhum lançamento no período informado.\n")
            else:
                saida.insert(tk.END, "Remetido por dia (Débito − Crédito):\n")
                for _, row in por_dia.sort_values('data').iterrows():
                    d = pd.to_datetime(row['data']).strftime('%d/%m/%Y')
                    v = row['remetido']
                    saida.insert(tk.END, f" - {d}: R$ {fmt_brl(v)}\n")
                saida.insert(tk.END, "\n")

            # Total do período
            saida.insert(tk.END, f"💸 Total remetido no período: R$ {fmt_brl(total_periodo)}\n")

        except Exception as e:
            messagebox.showerror("Erro", str(e))


    janela = tk.Toplevel(root)
    janela.title("Dinheiro Remetido")
    janela.configure(bg="#1e1e1e")
    if icone:
        janela.iconphoto(False, icone)
    janela.transient(root)

    frame = tk.Frame(janela, bg="#1e1e1e")
    frame.pack(padx=20, pady=20)

    tk.Label(frame, text="Razão Modelo I - Conta 6:", font=("Segoe UI", 10), bg="#1e1e1e", fg="#ffffff").grid(row=0, column=0, sticky="w")
    entry_arquivo = tk.Entry(frame, font=("Segoe UI", 10), width=40)
    entry_arquivo.grid(row=0, column=1, sticky="w", padx=10)
    tk.Button(frame, text="Selecionar", command=selecionar_arquivo,
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=2, sticky="w", padx=5)

    btn_frame = tk.Frame(janela, bg="#1e1e1e")
    btn_frame.pack(pady=10)

    tk.Button(btn_frame, text="Executar", command=executar,
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=0, padx=5)

    tk.Button(btn_frame, text="Voltar", command=janela.destroy,
              font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
              activebackground="#444444", activeforeground="#00bfff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=1, padx=5)
   
    tk.Button(btn_frame, text="Limpar Resultados", command=lambda: saida.delete("1.0", tk.END),
              font=("Segoe UI", 10), bg="#8b0000", fg="#ffffff",
              activebackground="#E10000", activeforeground="#ffffff",
              relief="flat", bd=0, padx=10, pady=5).grid(row=0, column=2, padx=5)


    saida = scrolledtext.ScrolledText(janela, width=100, height=20,
                                      bg="#2e2e2e", fg="#ffffff",
                                      font=("Consolas", 10), insertbackground="#ffffff")
    saida.pack(padx=10, pady=10)


def abrir_controle_lojas():
    """
    Abre o módulo Controle de Lojas no content-area do PRISMA,
    injetando também o usuário logado (current_user).
    """

    global menu_content_area, _show_main_menu_func

    # Limpa o content-area
    _clear_menu_content()

    # Callback de retorno ao menu principal
    def voltar_menu():
        try:
            if _show_main_menu_func:
                _show_main_menu_func()
        except Exception:
            pass

    # IMPORT LOCAL para evitar ciclo de importação
    from controle import ControleLojas

    # Instancia o módulo ControleLojas com:
    #   master = menu_content_area
    #   voltar_menu = callback
    #   current_user = usuário logado (do prisma.py)
    frame = ControleLojas(menu_content_area, voltar_menu, current_user)

    frame.pack(fill="both", expand=True)



# Se em algum lugar do seu código você ainda usa voltar_menu_principal(),
# mantenha uma versão fina que só chama a função registrada:
def voltar_menu_principal():
    global _show_main_menu_func
    if callable(_show_main_menu_func):
        _show_main_menu_func()


def iniciar_conciliacao_cartoes():
    ConciliacaoApp(master=root)
    janela.title("Conciliação de Cartões")
    janela.configure(bg="#1e1e1e")
    try:
        ConciliacaoApp()  # abre a aplicação do conciliador
    except Exception as e:
        messagebox.showerror("Erro", f"Falha ao iniciar o conciliador:\n{e}")


def iniciar_conciliacao_pix_qrcode():
    """
    Novo módulo: Conciliador Pix QrCode.
    Por enquanto um placeholder com janela dedicada para evoluir (importações, parsing, etc.).
    """
    janela = tk.Toplevel(root)
    janela.title("Conciliador Pix QrCode")
    janela.configure(bg="#1e1e1e")
    if icone:
        janela.iconphoto(False, icone)
    janela.transient(root)

    # Mensagem inicial / TODO
    lbl = tk.Label(
        janela,
        text="Módulo de conciliação Pix QrCode (em desenvolvimento).\n"
             "Aqui você poderá carregar extratos/relatórios Pix e executar a conciliação.",
        font=("Segoe UI", 11),
        bg="#1e1e1e",
        fg="#ffffff",
        justify="left"
    )
    lbl.pack(padx=16, pady=16, anchor="w")

    # Botões básicos
    btn_frame = tk.Frame(janela, bg="#1e1e1e")
    btn_frame.pack(pady=10)

    tk.Button(
        btn_frame, text="Fechar", command=janela.destroy,
        font=("Segoe UI", 10), bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0, padx=10, pady=5
    ).grid(row=0, column=0, padx=5)


def abrir_submenu_livro_fiscal():
    global menu_content_area, submenu_conc_frame
    _clear_menu_content()  # limpa a área central
    submenu_conc_frame = tk.Frame(menu_content_area, bg="#1e1e1e")
    submenu_conc_frame.pack(fill="both", expand=True, padx=20, pady=20)

    # helper para criar botões estilo principal
    def mk_btn(parent, text, cmd, danger=False):
        bg = "#8b0000" if danger else "#2e2e2e"
        fg = "#ffffff"
        b = tk.Button(
            parent, text=text, command=cmd,
            font=("Segoe UI", 11), bg=bg, fg=fg,
            activebackground="#444444", activeforeground="#00bfff",
            relief="flat", bd=0, padx=16, pady=10, cursor="hand2"
        )
        # hover
        def on_enter(e): b.configure(bg="#3a3a3a" if not danger else "#a10000")
        def on_leave(e): b.configure(bg=bg)
        b.bind("<Enter>", on_enter); b.bind("<Leave>", on_leave)
        return b

    titulo = tk.Label(
        submenu_conc_frame, text="Livro Fiscal",
        bg="#1e1e1e", fg="#ffffff", font=("Segoe UI", 12, "bold")
    )
    titulo.pack(pady=(0, 10))

    botoes = tk.Frame(submenu_conc_frame, bg="#1e1e1e")
    botoes.pack()

    mk_btn(botoes, "Livro Fiscal × Relatórios", iniciar_conciliacao).pack(fill="x", pady=6)
    mk_btn(botoes, "Livro Fiscal × Contabilidade", iniciar_comparador_diario).pack(fill="x", pady=6)

    # voltar ao menu principal
    mk_btn(botoes, "Voltar", construir_menu_principal, danger=True).pack(fill="x", pady=(18, 0))


def abrir_submenu_analise():
    """Submenu "Análise" no content area, com atalhos para telas do Controle de Lojas."""
    global menu_content_area, current_user
    if not menu_content_area or not menu_content_area.winfo_exists():
        return

    _clear_menu_content()
    frame = tk.Frame(menu_content_area, bg="#1e1e1e")
    frame.pack(fill="both", expand=True, padx=24, pady=10)

    titulo = tk.Label(frame, text="Análise", font=("Segoe UI", 14, "bold"),
                      bg="#1e1e1e", fg="#ffffff")
    titulo.pack(pady=(10, 14))

    def aplicar_brilho(btn):
        btn.bind("<Enter>", lambda e: btn.config(bg="#00bfff", fg="#000000"))
        btn.bind("<Leave>", lambda e: btn.config(bg="#2e2e2e", fg="#ffffff"))

    def make_btn(texto, cmd):
        b = tk.Button(frame, text=texto, command=cmd,
                      font=("Segoe UI", 12), bg="#2e2e2e", fg="#ffffff",
                      activebackground="#444444", activeforeground="#00bfff",
                      relief="flat", bd=0, padx=10, pady=8)
        b.pack(fill="x", pady=8)
        aplicar_brilho(b)
        return b

    # Instancia Controlador "silencioso" para abrir diretamente as telas
    def _with_controle(call):
        try:
            from controle import ControleLojas
            dummy = ControleLojas(root, None, current_user)
            call(dummy)  # abre Toplevels específicos
        except Exception as e:
            try:
                messagebox.showerror("Análise", str(e))
            except Exception:
                print("[Análise] Erro:", e)

    make_btn("Análise de Vales", lambda: _with_controle(lambda ctl: ctl.abrir_analise_vales()))

    from controle import abrir_analise_de_lojas
    make_btn("Análise de Lojas", lambda: abrir_analise_de_lojas(root)).pack(pady=6)
    make_btn("Resultado Mensal", lambda: _with_controle(lambda ctl: ctl.abrir_resultado_mensal()))
    make_btn("Avaliação de Lojas", lambda: _with_controle(lambda ctl: ctl.abrir_avaliacao_lojas()))

    tk.Button(frame, text="Voltar", command=construir_menu_principal,
              font=("Segoe UI", 12), bg="#8b0000", fg="#ffffff",
              activebackground="#E10000", activeforeground="#ffffff",
              relief="flat", bd=0, padx=10, pady=8).pack(fill="x", pady=(16, 4))

def abrir_submenu_conciliacao():
    """Submenu de conciliação dentro do content area (logo permanece no header)."""
    global menu_content_area
    if not menu_content_area or not menu_content_area.winfo_exists():
        return

    _clear_menu_content()
    frame = tk.Frame(menu_content_area, bg="#1e1e1e")
    frame.pack(fill="both", expand=True, padx=24, pady=10)

    titulo = tk.Label(frame, text="Conciliação", font=("Segoe UI", 14, "bold"),
                      bg="#1e1e1e", fg="#ffffff")
    titulo.pack(pady=(10, 14))

    def aplicar_brilho(btn):
        btn.bind("<Enter>", lambda e: btn.config(bg="#00bfff", fg="#000000"))
        btn.bind("<Leave>", lambda e: btn.config(bg="#2e2e2e", fg="#ffffff"))

    def make_btn(texto, cmd):
        b = tk.Button(frame, text=texto, command=cmd,
                      font=("Segoe UI", 12), bg="#2e2e2e", fg="#ffffff",
                      activebackground="#444444", activeforeground="#00bfff",
                      relief="flat", bd=0, padx=10, pady=8)
        b.pack(fill="x", pady=8)
        aplicar_brilho(b)
        return b

    # Botões do submenu (abrem Toplevels existentes)
    make_btn("Conciliador de Cartões", lambda: ConciliacaoApp(master=root))
    make_btn("Conciliador Pix QrCode", lambda: ConciliacaoPixApp(master=root))
    make_btn("Conciliador Pix Maquineta", lambda: ConciliacaoPixMaquinetaApp(master=root))

    # Voltar ao menu principal (só troca o content area)
    btn_voltar = tk.Button(frame, text="Voltar",
                           command=construir_menu_principal,
                           font=("Segoe UI", 12),
                           bg="#8b0000", fg="#ffffff",
                           activebackground="#E10000", activeforeground="#ffffff",
                           relief="flat", bd=0, padx=10, pady=8)
    btn_voltar.pack(fill="x", pady=(16, 4))

def voltar_menu_principal():
    global menu_principal_frame, controle_frame, pix_frame
    # esconde controle de lojas se estiver aberto
    if controle_frame:
        controle_frame.pack_forget()
        controle_frame = None
    # esconde o frame Pix se estiver aberto
    if pix_frame:
        pix_frame.pack_forget()
        pix_frame = None
    # volta a mostrar o menu
    if menu_principal_frame:
        menu_principal_frame.pack(fill="both", expand=True)


# ==== PERFIL (quadro no canto e painel lateral) ====

perfil_btn_ref = None       # referência ao botão/quadro
perfil_panel_ref = None     # referência ao painel lateral aberto (se houver)

def _fechar_painel_perfil():
    global perfil_panel_ref
    try:
        if perfil_panel_ref and perfil_panel_ref.winfo_exists():
            perfil_panel_ref.destroy()
    except Exception:
        pass
    perfil_panel_ref = None


def _abrir_painel_perfil(master):
    """
    Abre o painel lateral de perfil (avatar, nome, opções de usuário).
    Agora inclui o botão 'Voltar ao Menu Principal'.
    """
    global perfil_panel_ref, current_user, _show_main_menu_func

    # Se já estiver aberto, só dá foco
    if perfil_panel_ref and perfil_panel_ref.winfo_exists():
        perfil_panel_ref.lift()
        return

    PAINEL_W = 280
    PAINEL_BG = "#232323"
    TXT = "#ffffff"

    painel = tk.Toplevel(master)
    painel.overrideredirect(True)
    painel.configure(bg=PAINEL_BG)
    painel.attributes("-topmost", True)
    perfil_panel_ref = painel

    master.update_idletasks()
    x = master.winfo_rootx() + master.winfo_width() - PAINEL_W
    y = master.winfo_rooty()
    h = master.winfo_height()
    painel.geometry(f"{PAINEL_W}x{h}+{x}+{y}")

    # ---------- Cabeçalho ----------
    header = tk.Frame(painel, bg=PAINEL_BG)
    header.pack(fill="x", padx=12, pady=(12, 4))

    usuario = (current_user or {}).get("nome", "Usuário")
    role = (current_user or {}).get("role", "-")

    tk.Label(header, text="Perfil",
             font=("Segoe UI", 12, "bold"),
             bg=PAINEL_BG, fg=TXT).pack(anchor="w")

    tk.Label(header, text=f"{usuario} • {role}",
             font=("Segoe UI", 10),
             bg=PAINEL_BG, fg="#cccccc").pack(anchor="w")

    # ---------- Corpo ----------
    corpo = tk.Frame(painel, bg=PAINEL_BG)
    corpo.pack(fill="both", expand=True, padx=12, pady=8)

    # Função de criação de botões
    def make_btn(texto, cmd, danger=False):
        b = tk.Button(
            corpo, text=texto, command=cmd,
            font=("Segoe UI", 10),
            bg=("#2e2e2e" if not danger else "#8b0000"),
            fg="#ffffff",
            activebackground=("#444444" if not danger else "#E10000"),
            activeforeground=("#00bfff" if not danger else "#ffffff"),
            relief="flat", bd=0, padx=12, pady=8
        )
        b.pack(fill="x", pady=5)
        return b

    # Fecha painel
    def fechar():
        try:
            painel.destroy()
        except:
            pass

    # ---------- Botões ----------
    is_admin = bool(current_user and current_user.get("role") == "admin")

    # Gerenciar usuários (admin)
    if is_admin:
        make_btn("Gerenciar Usuários (Admin)",
                 lambda: (fechar(), gerenciar_usuarios(master)))

    # Editar Perfil
    make_btn("Editar Perfil",
             lambda: (fechar(), _show_profile_menu()))

    # Voltar ao Menu Principal  ← AQUI ESTÁ O NOVO BOTÃO
    def voltar_menu():
        fechar()
        if callable(_show_main_menu_func):
            _show_main_menu_func()

    make_btn("Voltar ao Menu Principal", voltar_menu)

    # Sair
    def sair():
        fechar()
        try:
            master.destroy()
        except:
            sys.exit(0)

    make_btn("Sair", sair, danger=True)

    # ---------- Botão X ----------
    topo = tk.Frame(painel, bg=PAINEL_BG)
    topo.place(relx=1.0, rely=0.0, x=-4, y=4, anchor="ne")

    btn_close = tk.Label(topo, text="✕", bg=PAINEL_BG, fg="#aaaaaa",
                         font=("Segoe UI", 11), cursor="hand2")
    btn_close.pack()
    btn_close.bind("<Button-1>", lambda e: fechar())

    # Fecha painel ao clicar fora (opcional)
    def _on_click_global(event):
        # Verifica se clique foi fora do painel
        if not painel.winfo_exists():
            return
        px, py = painel.winfo_rootx(), painel.winfo_rooty()
        pw, ph = painel.winfo_width(), painel.winfo_height()
        if not (px <= event.x_root <= px + pw and py <= event.y_root <= py + ph):
            _fechar_painel_perfil()
            master.unbind("<Button-1>", handler_id)
    handler_id = master.bind("<Button-1>", _on_click_global, add="+")

def trocar_usuario():
    global current_user
    u = login_modal(root)
    if u:
        current_user = u
        messagebox.showinfo("Sessão", f"Agora: {u.get('nome')} ({u.get('role')})")
        construir_menu_principal()  # <— isto já força recarregar o avatar
    else:
        messagebox.showwarning("Aviso", "Login cancelado.")

def construir_menu_principal():
    """
    Monta a janela principal com:
      - HEADER fixo no topo: logo central (pequeno) e bloco de perfil no canto direito.
      - Balanceamento dinâmico: o espaçador esquerdo replica a largura do perfil (direita),
        mantendo o logo exatamente no centro geométrico.
      - CONTENT AREA abaixo do header, onde trocamos as telas (menu principal, menu do usuário etc.).
      - Hover/brilho em todos os botões.
      - Ponteiro global _show_main_menu_func para permitir "Voltar" sem tela preta.
    """
    import tkinter as tk
    from PIL import Image, ImageTk

    global menu_principal_frame, menu_content_area, _show_main_menu_func
    global current_user, logo, LOGO_PATH, root

    # =================== HELPERS ===================
    def _apply_hover(btn: tk.Widget, base_bg="#2e2e2e", base_fg="#ffffff"):
        def on_enter(_): btn.config(bg="#00bfff", fg="#000000")
        def on_leave(_): btn.config(bg=base_bg, fg=base_fg)
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)

    def _clear_content():
        if not menu_content_area or not menu_content_area.winfo_exists():
            return
        for w in menu_content_area.winfo_children():
            try:
                w.destroy()
            except Exception:
                try:
                    w.pack_forget()
                except Exception:
                    pass

    def _make_btn(parent, text, cmd, danger=False):
        if danger:
            bg, abg, afg = "#8b0000", "#E10000", "#ffffff"
        else:
            bg, abg, afg = "#2e2e2e", "#444444", "#00bfff"
        b = tk.Button(
            parent, text=text, command=cmd,
            font=("Segoe UI", 12),
            bg=bg, fg="#ffffff",
            activebackground=abg, activeforeground=afg,
            relief="flat", bd=0, padx=10, pady=8
        )
        b.pack(fill="x", pady=8)
        if danger:
            b.bind("<Enter>", lambda e: b.config(bg=abg))
            b.bind("<Leave>", lambda e: b.config(bg=bg))
        else:
            _apply_hover(b, base_bg=bg, base_fg="#ffffff")
        return b

    # ============ (RE)CRIA O CONTAINER RAIZ ============
    if menu_principal_frame and menu_principal_frame.winfo_exists():
        try:
            menu_principal_frame.destroy()
        except Exception:
            menu_principal_frame.pack_forget()

    menu_principal_frame = tk.Frame(root, bg="#1e1e1e")
    menu_principal_frame.pack(fill="both", expand=True)

    # =================== HEADER (TOPO) ===================
    header = tk.Frame(menu_principal_frame, bg="#1e1e1e")
    header.pack(side="top", fill="x", padx=12, pady=(6, 4))

    # Grade 3 colunas: [espaçador E] [LOGO] [espaçador D]
    header.grid_columnconfigure(0, weight=1)  # left spacer
    header.grid_columnconfigure(1, weight=0)  # center (logo)
    header.grid_columnconfigure(2, weight=1)  # right spacer

    # Células
    left_spacer   = tk.Frame(header, bg="#1e1e1e")
    center_holder = tk.Frame(header, bg="#1e1e1e")
    right_spacer  = tk.Frame(header, bg="#1e1e1e")

    left_spacer.grid(  row=0, column=0, sticky="nsew")
    center_holder.grid(row=0, column=1, sticky="n")
    right_spacer.grid( row=0, column=2, sticky="nsew")

    # ---------- BLOCO DE PERFIL (canto superior direito) ----------
    # LIMPE qualquer resquício anterior para não duplicar
    try:
        for w in right_spacer.winfo_children():
            w.destroy()
    except Exception:
        pass

    header_right = tk.Frame(right_spacer, bg="#1e1e1e")
    header_right.pack(side="right", anchor="ne", padx=(0, 8), pady=(0, 4))

    nome_curto = (current_user or {}).get("nome", "Usuário")
    lbl_user = tk.Label(header_right, text=nome_curto,
                        font=("Segoe UI", 9, "bold"),
                        bg="#1e1e1e", fg="#cccccc")
    lbl_user.pack(anchor="ne")

    perfil_btn = tk.Button(
        header_right,
        text="◼",  # troque por image=perfil_photo quando quiser
        bg="#2e2e2e", fg="#ffffff",
        activebackground="#444444", activeforeground="#00bfff",
        relief="flat", bd=0,
        
        highlightthickness=0, cursor="hand2",
        padx=0, pady=0                  # evita margens internas que “encolhem” a área útil

    )
    perfil_btn.pack(anchor="ne", pady=(2, 0))
    _apply_hover(perfil_btn)
    
    # --- Avatar no botão de perfil (canto superior direito) ---
    try:
        foto_path = (current_user or {}).get("foto")
        perfil_photo = _load_user_avatar(foto_path, size=(80, 80))
        if perfil_photo:
            # Aplica imagem e remove texto
            perfil_btn.config(image=perfil_photo, text="")
            # Evita garbage collector
            perfil_btn.image = perfil_photo
        else:
            # Fallback visual: mantém o quadradinho com texto
            perfil_btn.config(text="◼", image="", width=3, height=1)
    except Exception:
        # Qualquer falha → volta para o fallback textual
        perfil_btn.config(text="◼", image="", width=3, height=1)

    # ---------- LOGO CENTRAL ----------
    LOGO_HEADER_SIZE = (130, 130)  # ajuste fino; menor ajuda a caber todos os botões
    try:
        try:
            im = Image.open(LOGO_PATH)
            resample = getattr(Image, "LANCZOS", Image.BICUBIC)
            im = im.resize(LOGO_HEADER_SIZE, resample)
            logo_header = ImageTk.PhotoImage(im)
        except Exception:
            logo_header = logo  # fallback: usa o que já estiver carregado

        if logo_header:
            lbl_logo = tk.Label(center_holder, image=logo_header, bg="#1e1e1e")
            lbl_logo.image = logo_header  # evita GC
            lbl_logo.pack()
        else:
            tk.Label(center_holder, text="PRISMA",
                     font=("Segoe UI", 14, "bold"),
                     bg="#1e1e1e", fg="#ffffff").pack()
    except Exception:
        tk.Label(center_holder, text="PRISMA",
                 font=("Segoe UI", 14, "bold"),
                 bg="#1e1e1e", fg="#ffffff").pack()

    # ---------- BALANCEAMENTO DINÂMICO ----------
    def _balance_header(_evt=None):
        try:
            header_right.update_idletasks()
            w_right = header_right.winfo_reqwidth()
            header.grid_columnconfigure(0, minsize=w_right)  # espelha à esquerda
            header.grid_columnconfigure(2, minsize=0)
        except Exception:
            pass

    # ... depois de aplicar o avatar:
    header.after(0, _balance_header)


    # =================== CONTENT AREA ===================
    global menu_content_area
    if menu_content_area and menu_content_area.winfo_exists():
        try:
            menu_content_area.destroy()
        except Exception:
            pass
    menu_content_area = tk.Frame(menu_principal_frame, bg="#1e1e1e")
    menu_content_area.pack(side="top", fill="both", expand=True, padx=24, pady=(2, 10))


    # =================== TELAS (CORPO) ===================
    def _show_main_menu():
        _clear_content()
        f = tk.Frame(menu_content_area, bg="#1e1e1e")
        f.pack(fill="both", expand=True)

        _make_btn(f, "Livro Fiscal", abrir_submenu_livro_fiscal)
        #_make_btn(f, "Contabilidade Detalhada", iniciar_analise_contabil)
        _make_btn(f, "Conferência de Caixa", abrir_analise_memorando)
        #_make_btn(f, "Dinheiro Remetido", iniciar_calculo_dinheiro_remetido)
        _make_btn(f, "Conciliação", abrir_submenu_conciliacao)
        _make_btn(f, "Controle de Lojas", abrir_controle_lojas)
        _make_btn(f, "Análise", abrir_submenu_analise) 
        _make_btn(f, "Gerador de Pastas", iniciar_pastas)

    def _show_profile_menu():
        """
        Menu de perfil dentro do content area.
        Mostra: Editar Perfil, Gerenciar Usuários (admin), Voltar ao Menu Principal, Sair.
        """
        _clear_content()

        f = tk.Frame(menu_content_area, bg="#1e1e1e")
        f.pack(fill="both", expand=True, padx=30, pady=30)

        is_admin = bool(current_user and current_user.get("role") == "admin")

        # ---- Editar Perfil ----
        def _abrir_editar_perfil():
            try:
                editar_perfil_modal(root)
            except Exception as e:
                messagebox.showerror("Erro", f"Falha ao abrir edição de perfil:\n{e}")

        _make_btn(f, "Editar Perfil", _abrir_editar_perfil)
        

        # ---- Gerenciar Usuários (apenas admin) ----
        if is_admin:
            _make_btn(f, "Gerenciar Usuários (Admin)", lambda: gerenciar_usuarios(root))

        # ---- Voltar ao Menu Principal ----
        _make_btn(f, "Voltar ao Menu Principal", _show_main_menu)

        # ---- Sair ----
        _make_btn(f, "Sair", lambda: root.destroy(), danger=True)


    # registra ponteiro global para que outras telas consigam "voltar"
    _show_main_menu_func = _show_main_menu

    # mostra o menu principal inicialmente
    _show_main_menu()

    # clique no avatar -> abre o menu de perfil do content area
    perfil_btn.config(command=_show_profile_menu)

def main():
    global root
    root = tk.Tk()
    root.title("PRISMA")
    root.configure(bg="#1e1e1e")
    root.geometry("420x820")

    # Ícone (ignorar falha)
    try:
        icone_img = Image.open(ICON_PATH)
        icone = ImageTk.PhotoImage(icone_img)
        root.iconphoto(False, icone)
    except Exception as e:
        print("⚠ Ícone não encontrado:", e)

    # Esconde a janela principal até terminar o login
    root.withdraw()

    # === LOGIN OBRIGATÓRIO ===
    user = exigir_login(root)
    if not user:
        root.destroy()
        return

    # Reexibe a janela principal após login OK
    root.deiconify()

    # Monta o menu principal (permite que só apareça depois do login)
    construir_menu_principal()

    root.mainloop()

if __name__ == "__main__":
    main()