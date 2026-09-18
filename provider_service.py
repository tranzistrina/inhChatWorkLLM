"""Provider integration helpers for OpenAI-compatible APIs.

The web routes deliberately keep the existing HTTP contract. This module owns
provider URL normalization, model-list parsing, authentication headers and the
small amount of endpoint compatibility logic needed by different
OpenAI-compatible servers.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from typing import Optional

import requests


class ProviderConfigError(ValueError):
    """Raised when a provider configuration cannot be used."""


@dataclass(frozen=True)
class ModelDiscoveryResult:
    models: list[str]
    base_url: str


def normalize_base_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ProviderConfigError("Укажите Base URL")

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderConfigError("Base URL должен начинаться с http:// или https://")

    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def candidate_base_urls(base_url: str) -> list[str]:
    base = normalize_base_url(base_url)
    candidates = [base]
    path = urlsplit(base).path.rstrip("/")
    if not path.endswith("/v1"):
        candidates.append(base + "/v1")
    return list(dict.fromkeys(candidates))


def endpoint(base_url: str, resource: str) -> str:
    return normalize_base_url(base_url).rstrip("/") + "/" + resource.lstrip("/")


def auth_headers(api_key: str) -> dict[str, str]:
    key = str(api_key or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def extract_models(payload) -> list[str]:
    """Accept the common OpenAI shape and a few harmless compatible variants."""
    items = payload
    if isinstance(payload, dict):
        items = payload.get("data")
        if items is None:
            items = payload.get("models")
    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        model_id = item.get("id") if isinstance(item, dict) else item
        if model_id is not None:
            model_id = str(model_id).strip()
            if model_id and model_id not in result:
                result.append(model_id)
    return result


def discover_models(
    base_url: str,
    api_key: str = "",
    *,
    timeout: float = 15,
    session=requests,
) -> ModelDiscoveryResult:
    last_error: Optional[Exception] = None

    for candidate in candidate_base_urls(base_url):
        try:
            response = session.get(
                endpoint(candidate, "models"),
                headers=auth_headers(api_key),
                timeout=timeout,
            )
            response.raise_for_status()
            models = extract_models(response.json())
            if not models:
                raise ProviderConfigError("API не вернул список моделей")
            return ModelDiscoveryResult(models=models, base_url=normalize_base_url(candidate))
        except ProviderConfigError:
            raise
        except Exception as exc:
            last_error = exc

    raise ProviderConfigError(
        "Не удалось получить модели"
        + (f": {last_error}" if last_error else "")
    )
