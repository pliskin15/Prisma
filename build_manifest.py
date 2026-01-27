# build_manifest.py
import hashlib, json, os, sys
from pathlib import Path
from datetime import datetime

# build_manifest.py — trechos importantes para você revisar
BUILD_DIR   = Path("dist/Prisma")        # pasta gerada pelo PyInstaller do app
NEW_VERSION = "1.0.0"                    # defina a versão que está lançando
GITHUB_USER = "pliskin15"                 # seu usuário GitHub
GITHUB_REPO = "prisma"                   # nome do repositório
GIT_BRANCH  = "updates"                  # branch onde vamos publicar
BASE_URL    = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GIT_BRANCH}/updates/files"
OUTPUT_MANIFEST = Path("updates/latest/version.json")

def sha256_file(p: Path, chunk=1024*1024) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b: break
            h.update(b)
    return h.hexdigest()

def main():
    if not BUILD_DIR.exists():
        print(f"ERRO: BUILD_DIR não existe: {BUILD_DIR}")
        sys.exit(1)

    files = {}
    for root, _, filenames in os.walk(BUILD_DIR):
        for name in filenames:
            fp = Path(root) / name
            rel = fp.relative_to(BUILD_DIR).as_posix()  # caminho relativo deployável
            size = fp.stat().st_size
            digest = sha256_file(fp)
            files[rel] = {"sha256": digest, "size": size}

    manifest = {
        "version": NEW_VERSION,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "base_url": BASE_URL,
        "files": files
    }

    OUTPUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifesto gerado em: {OUTPUT_MANIFEST.resolve()}")
    print(f"Arquivos previstos: {len(files)}")

if __name__ == "__main__":
    main()