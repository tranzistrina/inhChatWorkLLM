"""Media helpers for multimodal chat input and OpenAI-compatible image generation."""
import base64
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests

from provider_service import auth_headers, candidate_base_urls, endpoint


IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def is_image_path(path):
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def image_data_url(path):
    p = Path(path)
    mime = IMAGE_EXTENSIONS.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0]
    if not mime or not mime.startswith("image/"):
        return None
    return "data:%s;base64,%s" % (mime, base64.b64encode(p.read_bytes()).decode("ascii"))


def multimodal_content(text, image_paths):
    content = [{"type": "text", "text": str(text or "")}]
    for path in image_paths or []:
        try:
            data_url = image_data_url(path)
        except OSError:
            continue
        if data_url:
            content.append({"type": "image_url", "image_url": {"url": data_url, "detail": "auto"}})
    return content


def image_paths_under(root, relative_paths):
    result = []
    root = Path(root).resolve()
    for rel in relative_paths or []:
        p = (root / rel).resolve()
        if p.is_file() and root in p.parents and is_image_path(p):
            result.append(p)
    return result


def _provider_hosts(provider):
    hosts = set()
    for candidate in candidate_base_urls(provider["base_url"]):
        host = urlsplit(candidate).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def _save_image_bytes(data, output_dir, index):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / ("generated_%02d.png" % index)
    path.write_bytes(data)
    return path


def generate_image(provider, prompt, output_dir, prefix="generated"):
    """Generate one image through a provider marked as kind=image.

    Supports the common /images/generations response with b64_json and trusted
    provider-host URLs. The latter are downloaded locally so Work Mode can reuse
    the resulting image as a normal artifact.
    """
    if not str(prompt or "").strip():
        raise ValueError("Пустой промпт изображения")
    headers = {"Content-Type": "application/json", **auth_headers(provider["api_key"])}
    body = {"model": provider["model"], "prompt": str(prompt).strip(), "n": 1}
    last_error = None
    response = None
    for base in candidate_base_urls(provider["base_url"]):
        try:
            response = requests.post(
                endpoint(base, "images/generations"),
                json=body,
                headers=headers,
                timeout=300,
            )
            if response.status_code == 404 and base != candidate_base_urls(provider["base_url"])[-1]:
                continue
            response.raise_for_status()
            break
        except Exception as exc:
            last_error = exc
            response = None
    if response is None:
        raise RuntimeError("Не удалось обратиться к image API%s" % (": %s" % last_error if last_error else ""))

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Image API вернул не JSON") from exc

    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError("Image API не вернул изображение")

    out = []
    provider_hosts = _provider_hosts(provider)
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json")
        if b64:
            try:
                raw = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError) as exc:
                raise RuntimeError("Image API вернул некорректный base64") from exc
            path = Path(output_dir) / ("%s_%02d.png" % (re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)[:40] or "generated", index))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            out.append(path)
            continue

        url = str(item.get("url") or "").strip()
        parsed = urlsplit(url)
        if url and parsed.scheme in {"http", "https"} and parsed.hostname and parsed.hostname.lower() in provider_hosts:
            downloaded = requests.get(url, timeout=120, headers=auth_headers(provider["api_key"]))
            downloaded.raise_for_status()
            mime = downloaded.headers.get("Content-Type", "")
            if not mime.startswith("image/"):
                raise RuntimeError("Image API вернул URL не изображения")
            suffix = mimetypes.guess_extension(mime.split(";", 1)[0].strip()) or ".png"
            path = Path(output_dir) / ("%s_%02d%s" % (re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)[:40] or "generated", index, suffix))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(downloaded.content)
            out.append(path)
            continue
    if not out:
        raise RuntimeError("Image API не вернул поддерживаемый b64_json или URL изображения")
    return out
