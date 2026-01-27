
import hashlib, json, os, sys, threading, time, subprocess
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.parse import quote
# ---------- Utilidades ----------
def app_dir() -> Path:
    # Diretório de instalação (pasta onde está o launcher.exe)
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent

def sha256_file(p: Path, chunk=1024*1024) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b: break
            h.update(b)
    return h.hexdigest()

def atomic_replace(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)

def read_text_if_exists(p: Path, default=""):
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return default

# ---------- Carregar config ----------
DEFAULT_CONFIG = {
    "manifest_url": "https://raw.githubusercontent.com/<usuario>/<repo>/updates/updates/latest/version.json",
    "app_executable": "SeuApp.exe",   # nome do seu exe
    "exclude": ["launcher.exe", "updater_config.json", "version.txt"],  # nunca atualizar
    "auth_token": ""                  # se repo for privado (PAT)
}

CONFIG_PATH = app_dir() / "updater_config.json"
if CONFIG_PATH.exists():
    try:
        CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for k, v in DEFAULT_CONFIG.items():
            CONFIG.setdefault(k, v)
    except Exception:
        CONFIG = DEFAULT_CONFIG.copy()
else:
    CONFIG = DEFAULT_CONFIG.copy()

MANIFEST_URL = CONFIG["manifest_url"]
APP_EXE      = CONFIG["app_executable"]
EXCLUDE      = set(CONFIG["exclude"])
AUTH_TOKEN   = CONFIG.get("auth_token", "")

# ---------- UI ----------
class UpdaterUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Atualizador")
        self.geometry("520x260")
        self.resizable(False, False)

        self.lbl = ttk.Label(self, text="Verificando atualizações...", anchor="w")
        self.lbl.pack(fill="x", padx=12, pady=(12, 6))

        self.pb_total = ttk.Progressbar(self, orient="horizontal", mode="determinate", length=480)
        self.pb_total.pack(padx=12, pady=6)
        self.lbl_total = ttk.Label(self, text="Total: 0%")
        self.lbl_total.pack(anchor="w", padx=12)

        self.pb_file = ttk.Progressbar(self, orient="horizontal", mode="determinate", length=480)
        self.pb_file.pack(padx=12, pady=6)
        self.lbl_file = ttk.Label(self, text="Arquivo: aguardando...")
        self.lbl_file.pack(anchor="w", padx=12)

        self.log = tk.Text(self, height=6, state="disabled")
        self.log.pack(fill="both", expand=True, padx=12, pady=6)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.thread = threading.Thread(target=self.run_update, daemon=True)
        self.thread.start()

    def on_close(self):
        # Evitar fechamento durante update
        if self.thread.is_alive():
            messagebox.showinfo("Aguarde", "Atualização em andamento. Por favor, aguarde.")
        else:
            self.destroy()

    def set_status(self, text):
        self.lbl.config(text=text)
        self._log(text)

    def _log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")
        self.update_idletasks()

    def set_total_progress(self, current, total):
        self.pb_total["maximum"] = max(total, 1)
        self.pb_total["value"] = current
        pct = 0 if total == 0 else int((current/total)*100)
        self.lbl_total.config(text=f"Total: {pct}%")
        self.update_idletasks()

    def set_file_progress(self, current, total, name=""):
        self.pb_file["maximum"] = max(total, 1)
        self.pb_file["value"] = current
        pct = 0 if total == 0 else int((current/total)*100)
        base = Path(name).name if name else ""
        self.lbl_file.config(text=f"Arquivo: {base} ({pct}%)")
        self.update_idletasks()

    # ----------- Rede -----------
    def _http_get_json(self, url):
        req = Request(url, headers={"User-Agent": "Updater"})
        if AUTH_TOKEN:
            req.add_header("Authorization", f"token {AUTH_TOKEN}")
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _http_stream_to_file(self, url, out_path: Path, expected_size: int = 0):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmpdl")
        req = Request(url, headers={"User-Agent": "Updater"})
        if AUTH_TOKEN:
            req.add_header("Authorization", f"token {AUTH_TOKEN}")
        with urlopen(req, timeout=60) as resp, tmp.open("wb") as f:
            total = 0
            chunk = 1024 * 256
            while True:
                b = resp.read(chunk)
                if not b:
                    break
                f.write(b)
                total += len(b)
                self.set_file_progress(total, expected_size, out_path.name)
        return tmp

    # ----------- Núcleo -----------
    def run_update(self):
        root = app_dir()
        local_version_file = root / "version.txt"
        local_version = read_text_if_exists(local_version_file, "0.0.0")

        try:
            self.set_status("Baixando manifesto...")
            manifest = self._http_get_json(MANIFEST_URL)
        except (URLError, HTTPError) as e:
            self.set_status("Falha ao obter manifesto. Abrindo app atual...")
            self.launch_app()
            return
        except Exception as e:
            self._log(f"Erro: {e}")
            self.launch_app()
            return

        remote_version = manifest.get("version", "0.0.0")
        base_url = manifest["base_url"]
        files = manifest.get("files", {})

        # Descobrir o que precisa atualizar
        to_update = []
        total_bytes = 0
        for rel, meta in files.items():
            if Path(rel).name in EXCLUDE:
                continue
            target = root / rel
            need = True
            if target.exists():
                try:
                    local_hash = sha256_file(target)
                    if local_hash == meta["sha256"]:
                        need = False
                except Exception:
                    need = True
            if need:
                to_update.append((rel, meta))
                total_bytes += int(meta.get("size", 0))

        if not to_update and remote_version == local_version:
            self.set_status("Nenhuma atualização disponível.")
            self.launch_app()
            return

        if not to_update and remote_version != local_version:
            # Apenas versão mudou, sem arquivos (raro, mas ok)
            local_version_file.write_text(remote_version, encoding="utf-8")
            self.set_status("Versão atualizada.")
            self.launch_app()
            return

        # Download dos arquivos necessários
        self.set_status(f"Atualizando de v{local_version} → v{remote_version}...")
        self.set_total_progress(0, total_bytes)

        downloaded_bytes = 0
        for rel, meta in to_update:
            url = f"{base_url}/{quote(rel.replace('\\', '/'))}"
            dst = root / rel
            size = int(meta.get("size", 0))
            self._log(f"Baixando: {rel} ({size} bytes)")
            try:
                tmp = self._http_stream_to_file(url, dst, size)
                # Verifica hash antes de aplicar
                if sha256_file(tmp) != meta["sha256"]:
                    self._log(f"Checksum inválido para {rel}. Abortando.")
                    tmp.unlink(missing_ok=True)
                    messagebox.showerror("Erro", f"Falha na verificação de integridade: {rel}")
                    self.launch_app()
                    return
                dst.parent.mkdir(parents=True, exist_ok=True)
                atomic_replace(tmp, dst)
            except Exception as e:
                self._log(f"Falha ao atualizar {rel}: {e}")
                messagebox.showerror("Erro", f"Falha ao atualizar {rel}.\n{e}")
                self.launch_app()
                return
            downloaded_bytes += size
            self.set_total_progress(downloaded_bytes, total_bytes)
            self.set_file_progress(0, 1, rel)  # reset barra do arquivo

        # Atualiza versão local
        try:
            local_version_file.write_text(remote_version, encoding="utf-8")
        except Exception as e:
            self._log(f"Não foi possível gravar version.txt: {e}")

        self.set_status("Atualização concluída.")
        time.sleep(0.5)
        self.launch_app()

    def launch_app(self):
        # Executa o app e fecha o launcher
        try:
            exe_path = app_dir() / APP_EXE
            subprocess.Popen([str(exe_path)], cwd=str(app_dir()))
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível iniciar o aplicativo.\n{e}")
        finally:
            self.destroy()

if __name__ == "__main__":
    # Estilo nativo
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    UpdaterUI().mainloop()