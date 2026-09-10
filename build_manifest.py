# build_manifest.py
import hashlib, json, os, sys, shutil
from pathlib import Path
from datetime import datetime

# === CONFIGURÁVEIS ===
BUILD_DIR   = Path("dist/Prisma")        # saída do PyInstaller (onedir)
EXTRA_DIR   = Path("extra_files")        # .env, updater_config.json, etc.
NEW_VERSION = "4.0.8.6"                  # versão a publicar
GITHUB_USER = "pliskin15"
GITHUB_REPO = "Prisma"
GIT_BRANCH  = "updates"

BASE_URL         = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GIT_BRANCH}/updates/files"
OUTPUT_MANIFEST  = Path("updates/latest/version.json")
OUTPUT_FILES_DIR = Path("updates/files")  # onde os arquivos serão copiados para o git

EXCLUDE_NAMES = {"version.json", "launcher.exe", "updater_config.json", "version.txt"}

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
    files = {}
    if not base_dir.exists():
        print(f"  [aviso] pasta não encontrada: {base_dir}")
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

def copy_files_to_output(base_dir: Path, out_dir: Path):
    """Copia todos os arquivos de base_dir para out_dir mantendo a estrutura."""
    if not base_dir.exists():
        return
    for root, _, filenames in os.walk(base_dir):
        for name in filenames:
            if name in EXCLUDE_NAMES:
                continue
            src = Path(root) / name
            rel = src.relative_to(base_dir)
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  copiado: {rel}")

def main():
    if not BUILD_DIR.exists():
        print(f"ERRO: BUILD_DIR não existe: {BUILD_DIR.resolve()}")
        print("Rode o PyInstaller antes.")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"Build: {BUILD_DIR.resolve()}")
    print(f"Versão: {NEW_VERSION}")
    print(f"{'='*50}\n")

    # 1. Limpa e recria a pasta updates/files
    if OUTPUT_FILES_DIR.exists():
        shutil.rmtree(OUTPUT_FILES_DIR)
    OUTPUT_FILES_DIR.mkdir(parents=True)

    # 2. Copia arquivos do build e extras para updates/files/
    print("Copiando arquivos do build...")
    copy_files_to_output(BUILD_DIR, OUTPUT_FILES_DIR)
    print("Copiando arquivos extras...")
    copy_files_to_output(EXTRA_DIR, OUTPUT_FILES_DIR)

    # 3. Coleta hashes de dentro de updates/files/ (fonte da verdade)
    print("\nCalculando hashes...")
    files = collect_files(OUTPUT_FILES_DIR)

    # 4. Gera manifesto
    manifest = {
        "version": NEW_VERSION,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "base_url": BASE_URL,
        "files": files
    }

    OUTPUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"\nManifesto gerado: {OUTPUT_MANIFEST.resolve()}")
    print(f"Total de arquivos: {len(files)}")
    print("\nPróximos passos:")
    print("  git add updates/")
    print("  git commit -m \"release v{NEW_VERSION}\"")
    print(f"  git push origin master:updates --force")

if __name__ == "__main__":
    main()