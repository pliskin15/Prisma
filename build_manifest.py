# build_manifest.py (refatorado)
import hashlib, json, os, sys
from pathlib import Path
from datetime import datetime

# === CONFIGURÁVEIS ===
BUILD_DIR = Path("dist/Prisma")           # saída do PyInstaller (onedir)
EXTRA_DIR = Path("extra_files")           # arquivos adicionais: .env, version.txt, updater_config.json etc.
NEW_VERSION = "4.0.7.38" \
""                     # versão a publicar
GITHUB_USER = "pliskin15"
GITHUB_REPO = "Prisma"
GIT_BRANCH  = "updates"                   # branch onde você publica os arquivos de release

# O base_url deve apontar para a pasta ONDE OS ARQUIVOS FINAIS ficam (não o manifesto)
# Ex.: https://raw.githubusercontent.com/<user>/<repo>/<branch>/updates/files
BASE_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GIT_BRANCH}/updates/files"

# Saída do manifesto (version.json) que o launcher vai ler:
OUTPUT_MANIFEST = Path("updates/latest/version.json")

# Quais arquivos pular (apenas pelo nome base)
EXCLUDE_NAMES = {
    "version.json",  # nunca incluir o próprio manifesto
    # acrescente algo se necessário
}

# === FUNÇÕES ===
def sha256_file(p: Path, chunk=1024*1024) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def collect_files(base_dir: Path) -> dict:
    """
    Varre base_dir e retorna dict {rel_path_posix: {"size": int, "sha256": str}}
    com caminhos relativos à própria base_dir.
    """
    files = {}
    if not base_dir.exists():
        return files
    for root, _, filenames in os.walk(base_dir):
        for name in filenames:
            if name in EXCLUDE_NAMES:
                continue
            fp = Path(root) / name
            rel = fp.relative_to(base_dir).as_posix()
            files[rel] = {
                "size": fp.stat().st_size,
                "sha256": sha256_file(fp)
            }
    return files

def main():
    # Garantir que BUILD_DIR exista (o EXTRA_DIR é opcional)
    if not BUILD_DIR.exists():
        print(f"ERRO: BUILD_DIR não existe: {BUILD_DIR}")
        sys.exit(1)

    # Coleta dos arquivos do build e dos extras
    build_files = collect_files(BUILD_DIR)     # ex.: SeuApp.exe, conciliador.py, etc.
    extra_files = collect_files(EXTRA_DIR)     # ex.: .env, version.txt, updater_config.json

    # Mescla: se houver colisão de nomes relaivos, "extra_files" sobrescreve (intencional)
    files = {}
    files.update(build_files)
    files.update(extra_files)

    # Monta manifesto
    manifest = {
        "version": NEW_VERSION,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "base_url": BASE_URL,
        "files": files
    }

    # Garante a pasta do manifesto e grava
    OUTPUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Manifesto gerado em: {OUTPUT_MANIFEST.resolve()}")
    print(f"Arquivos previstos: {len(files)}")
    # Dica de publicação:
    print("\nPublique os ARQUIVOS (build + extras) em: updates/files/")
    print("E publique este manifest em: updates/latest/version.json")

if __name__ == "__main__":
    main()