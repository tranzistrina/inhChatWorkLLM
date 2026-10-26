"""Portable launcher for inhCHAT and optional FreeDeepseekAPI proxy."""
import os
import signal
import subprocess
import sys
from pathlib import Path

from platform_utils import detached_flags, npm_executable

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def stop_tree(process):
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def main():
    if not PYTHON.is_file():
        print("Виртуальное окружение не найдено. Сначала выполните: python setup.py")
        return 1

    vendor = ROOT / ".vendor" / "FreeDeepseekAPI"
    proxy = None
    app = None
    log_file = None
    try:
        if (vendor / "deepseek-auth.json").is_file():
            npm = npm_executable()
            if npm:
                log = ROOT / "data" / "deepseek-proxy.log"
                log.parent.mkdir(exist_ok=True)
                log_file = log.open("ab")
                env = dict(os.environ, NON_INTERACTIVE="1")
                proxy = subprocess.Popen(
                    [npm, "start"], cwd=str(vendor), env=env,
                    stdout=log_file, stderr=subprocess.STDOUT, **detached_flags(),
                )
                print("DeepSeek proxy запускается на http://127.0.0.1:9655")
            else:
                print("Не найден npm: пропускаю запуск DeepSeek proxy.")
        else:
            print("DeepSeek proxy пока не авторизован, обычные API доступны.")

        app = subprocess.Popen(
            [str(PYTHON), "-u", str(ROOT / "run_server.py")],
            cwd=str(ROOT), **detached_flags(),
        )
        print("inhCHAT: http://127.0.0.1:6767", flush=True)
        return app.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        stop_tree(app)
        stop_tree(proxy)
        if log_file:
            log_file.close()


if __name__ == "__main__":
    sys.exit(main())
