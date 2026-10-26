"""Cross-platform setup: python setup.py [--skip-deepseek]."""
import argparse
import os
import secrets
import shutil
import subprocess
import sys
import venv
from pathlib import Path

from platform_utils import configure_console

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def venv_python():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def protect(path):
    if os.name != "nt":
        path.chmod(0o600)


def main():
    configure_console()
    parser = argparse.ArgumentParser(description="Установка inhCHAT на Windows/Linux/macOS")
    parser.add_argument("--skip-deepseek", action="store_true",
                        help="Не устанавливать дополнительный DeepSeek proxy (обычный чат продолжит работать)")
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        parser.error("Требуется Python 3.10 или новее")
    if not venv_python().is_file():
        print("Создаю виртуальное окружение...")
        venv.EnvBuilder(with_pip=True).create(str(VENV))
    python = str(venv_python())
    subprocess.run([python, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
                   cwd=ROOT, check=True)

    env_file = ROOT / ".env"
    if not env_file.exists():
        env_file.write_text(
            "APP_SECRET_KEY=" + secrets.token_urlsafe(48)
            + "\nPORT=6767\nDEEPSEEK_BASE_URL=http://127.0.0.1:9655/v1\n",
            encoding="utf-8",
        )
        protect(env_file)

    users = ROOT / "users.json"
    if not users.exists():
        users.write_text(
            '[\n  {"username": "admin", "password": "676769"}\n]\n',
            encoding="utf-8",
        )
        protect(users)
        print("Создан users.json с временным паролем admin/676769. ОБЯЗАТЕЛЬНО измените пароль.")

    for dirname in ("data", "workspace"):
        directory = ROOT / dirname
        directory.mkdir(exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o700)

    if not args.skip_deepseek:
        git = shutil.which("git")
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        vendor = ROOT / ".vendor" / "FreeDeepseekAPI"
        if not git or not npm:
            print("DeepSeek proxy не установлен: нужны Git и Node.js/npm. "
                  "Используйте другой OpenAI-compatible API или установите их позже.")
        else:
            vendor.parent.mkdir(exist_ok=True)
            try:
                if not (vendor / "package.json").exists():
                    subprocess.run(
                        [git, "clone", "https://github.com/ForgetMeAI/FreeDeepseekAPI.git", str(vendor)],
                        check=True, cwd=ROOT,
                    )
                subprocess.run([npm, "install"], cwd=vendor, check=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                print(f"Не удалось подготовить дополнительный DeepSeek proxy: {exc}")
                print("Чат с другими провайдерами останется доступен.")

    print("\nУстановка завершена. Запуск: python start.py")
    print("Открыть: http://127.0.0.1:6767")


if __name__ == "__main__":
    main()
