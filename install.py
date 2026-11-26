"""Install Nib for the current user, from a release or an extracted folder."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen

VERSION = "1.1.1"
DIGEST = "be2768b6c7f1c0e0d277fee2860d49b8a8392a3027a4d8ff92073f666e084e88"
OLD = {
    "31b14e9fa5d21f315b5002992d0646a5873bab97b017a6c706b94e3dbb7273cb",
    "5a2953cac6dfad067de4f6877252888c82bc0f6b47faf7d89e202f90f0977475",
    "ea3b1d94936be2e9594e076cbe42eef32d3202aea7e171fcc16c074fa2dd5ec3",
}


def write(path, data, mode):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
    try:
        temp.write_bytes(data)
        temp.chmod(mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def add_path(folder):
    if os.name == "nt":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                value, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                value, kind = "", winreg.REG_EXPAND_SZ
            if str(folder).casefold() not in [p.casefold() for p in value.split(";")]:
                winreg.SetValueEx(key, "Path", 0, kind, os.pathsep.join(filter(None, (value, str(folder)))))
        import ctypes
        ctypes.windll.user32.SendMessageTimeoutW(65535, 26, 0, "Environment", 2, 1000, None)
    else:
        shell = Path(os.environ.get("SHELL", "sh")).name
        if str(folder) in os.get_exec_path():
            return
        if shell == "fish":
            subprocess.run(["fish", "-c", 'fish_add_path "$HOME/.local/bin"'], check=True)
            return
        files = [".zshrc"] if shell == "zsh" else [".profile"]
        if shell == "bash":
            files.append(".bashrc")
            files.extend(name for name in (".bash_profile", ".bash_login") if (Path.home()/name).exists())
        line = 'export PATH="$HOME/.local/bin:$PATH"'
        for name in files:
            profile = Path.home()/name
            if not profile.exists() or line not in profile.read_text(errors="replace").splitlines():
                with profile.open("a") as out:
                    out.write("\n" + line + "\n")


def main():
    if sys.version_info < (3, 8):
        sys.exit("Install Python 3.8 or newer first.")
    windows = os.name == "nt"
    folder = Path(os.environ["LOCALAPPDATA"])/"Nib" if windows else Path.home()/".local/bin"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder/"nib"
    marker = folder/".nib-installed"
    if dest.exists():
        old = hashlib.sha256(dest.read_bytes()).hexdigest()
        if old not in OLD | {DIGEST} and not marker.is_file():
            sys.exit(f"Another command exists at {dest}. Installation stopped.")
    adjacent = Path(__file__).resolve().with_name("nib")
    data = adjacent.read_bytes() if adjacent.is_file() else b""
    if hashlib.sha256(data).hexdigest() != DIGEST:
        url = f"https://github.com/Ka1bOne/nib/releases/download/v{VERSION}/nib"
        with urlopen(url, timeout=20) as response:
            data = response.read(1024 * 1024)
    if hashlib.sha256(data).hexdigest() != DIGEST:
        sys.exit("Release download failed checksum verification.")
    python = sys.executable
    if windows:
        subprocess.run([python, "-m", "venv", str(folder/"runtime")], check=True)
        python = str(folder/"runtime/Scripts/python.exe")
        subprocess.run([python, "-m", "pip", "install", "--only-binary=:all:", "windows-curses"], check=True)
    elif not shutil.which("python3"):
        sys.exit("Nib requires python3 on PATH.")
    subprocess.run([python if windows else "python3", "-c",
                    'import curses; assert hasattr(curses.window, "get_wch")'], check=True)
    write(dest, data, 0o755)
    if windows:
        write(folder/"nib.cmd", b'@echo off\r\n"%~dp0runtime\\Scripts\\python.exe" "%~dp0nib" %*\r\n', 0o644)
    marker.write_text("Nib terminal editor\n")
    add_path(folder)
    print("Nib installed. Open a new terminal and type nib.")


if __name__ == "__main__":
    main()
