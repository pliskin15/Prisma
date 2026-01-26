
# migrar_json_supabase.py
import json, os
from datetime import datetime
from supabase_config import supabase

ARQUIVO_USUARIOS = "usuarios.json"
ARQUIVO_LOJAS = "lojas.json"
ARQUIVO_PERFIS = "perfis.json"
ARQUIVO_VALES = "analise_vales.json"

def _carregar_json(caminho):
    if os.path.exists(caminho):
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def migrar_usuarios():
    data = _carregar_json(ARQUIVO_USUARIOS) or []
    if isinstance(data, dict):
        data = list(data.values())
    rows = []
    for u in data:
        rows.append({
            "username": (u.get("username") or "").lower(),
            "nome": u.get("nome") or u.get("username"),
            "email": (u.get("email") or "").lower() or None,
            "role": (u.get("role") or "auditor"),
            "salt": u.get("salt"),
            "hash": u.get("hash"),
            "foto": u.get("foto"),
            "ativo": u.get("ativo", True),
            "created_at": u.get("created_at") or datetime.now().isoformat(timespec="seconds")
        })
    if rows:
        supabase.table("usuarios").upsert(rows, on_conflict="username").execute()
    print(f"Usuarios migrados: {len(rows)}")

def migrar_lojas():
    data = _carregar_json(ARQUIVO_LOJAS) or []
    rows = []
    for d in data:
        rows.append({
            "loja": d.get("loja", "Sem Nome"),
            "cnpj": d.get("cnpj") or "",
            "regional": d.get("regional") or "OUTROS"
        })
    if rows:
        supabase.table("lojas").insert(rows).execute()
    print(f"Lojas migradas: {len(rows)}")

def migrar_perfis():
    data = _carregar_json(ARQUIVO_PERFIS) or []
    if not isinstance(data, list):
        print("Nenhum perfil para migrar.")
        return
    count = 0
    for p in data:
        # Garante campos essenciais no doc
        doc = dict(p)
        doc.setdefault("planilha", [])
        doc.setdefault("observacoes", [])
        doc.setdefault("vouchers", [])
        doc.setdefault("avaliacoes", [])
        doc.setdefault("usuario_dono", (p.get("usuario_dono") or "").lower() or None)

        ins = supabase.table("perfis").upsert({
            "nome": p.get("nome") or "",
            "mes": p.get("mes") or "",
            "usuario_dono": (p.get("usuario_dono") or "").lower() or None,
            "doc": doc
        }, on_conflict="nome,mes,usuario_dono").execute()
        count += 1
    print(f"Perfis migrados: {count}")

def migrar_vales():
    data = _carregar_json(ARQUIVO_VALES) or {}
    supabase.table("analise_vales_doc").upsert({
        "id": "singleton",
        "doc": data
    }).execute()
    print("Análise de vales migrada (documento único).")

if __name__ == "__main__":
    migrar_usuarios()
    migrar_lojas()
    migrar_perfis()
    migrar_vales()
    print("Migração concluída.")
