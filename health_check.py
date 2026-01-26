
from supabase_config import supabase

def health_check():
    # tenta ler 1 registro da tabela 'usuarios'
    res = supabase.table("usuarios").select("username").limit(1).execute()
    print("OK — Conectou no Supabase. Exemplo de resposta:", res.data)

if __name__ == "__main__":
    health_check()
