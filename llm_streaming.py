"""Incremental OpenAI-compatible chat completion reader.

Uses a single upstream streaming request. Text is yielded as the provider sends
SSE deltas, rather than buffering the entire completion.
"""
import json

import requests

from provider_service import auth_headers, candidate_base_urls, endpoint


def ndjson(kind, **fields):
    return json.dumps({"type": kind, **fields}, ensure_ascii=False) + "\n"


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


def stream_completion(provider, messages, transport=requests):
    bases = candidate_base_urls(provider["base_url"])
    headers = {"Accept": "text/event-stream", **auth_headers(provider["api_key"])}
    body = {
        "model": provider["model"],
        "messages": messages,
        "temperature": 0.3,
        "stream": True,
    }
    for index, base in enumerate(bases):
        with transport.post(
            endpoint(base, "chat/completions"),
            json=body,
            headers=headers,
            stream=True,
            timeout=(15, 300),
        ) as response:
            if response.status_code == 404 and index + 1 < len(bases):
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            # Some compatible APIs ignore stream=True and return ordinary JSON.
            if "application/json" in content_type:
                choices = response.json().get("choices") or []
                if not choices:
                    raise RuntimeError("API не вернул choices")
                value = _content_text(choices[0].get("message", {}).get("content"))
                if value:
                    yield value
                return

            for raw in response.iter_lines(chunk_size=1):
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                line = line.strip()
                if line == "data: [DONE]":
                    return
                if not line.startswith("data:"):
                    continue  # SSE comments, event names, keep-alives
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Некорректный SSE-ответ провайдера") from exc
                if event.get("error"):
                    raise RuntimeError("Ошибка LLM API: " + str(event["error"]))
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = _content_text(delta.get("content"))
                    if text:
                        yield text
            return
    raise RuntimeError("Не удалось найти endpoint chat/completions")
