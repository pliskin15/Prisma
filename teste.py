
from supabase_config import supabase

res = supabase.table("lojas").select("*").execute()
print("Lojas:", res.data)
