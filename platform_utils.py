"""Platform-specific process helpers shared by Windows and POSIX hosts."""
import os
import shutil
import subprocess
from pathlib import Path


def npm_executable():
    return shutil.which("npm.cmd" if os.name == "nt" else "npm")


def detached_flags():
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def start_deepseek_auth(root):
    vendor = Path(root) / ".vendor" / "FreeDeepseekAPI"
    if not (vendor / "package.json").is_file():
        raise RuntimeError("FreeDeepseekAPI не установлен. Выполните setup.py")
    npm = npm_executable()
    if not npm:
        raise RuntimeError("Для входа в DeepSeek нужен Node.js и npm в PATH")
    log_path = Path(root) / "data" / "deepseek-auth.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [npm, "run", "auth"],
            cwd=str(vendor),
            stdout=log,
            stderr=subprocess.STDOUT,
            **detached_flags(),
        )
    return process.pid
