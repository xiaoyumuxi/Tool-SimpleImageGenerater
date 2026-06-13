#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path.cwd()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name}. Fill it in .env or export it before running.")
    return value


def normalize_base_url(base_url: str) -> str:
    cleaned = base_url.strip().rstrip("/")
    suffixes = (
        "/models",
        "/images/generations",
        "/chat/completions",
        "/responses",
    )
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    if not cleaned.endswith("/v1"):
        cleaned = f"{cleaned}/v1"
    return cleaned


def request_json(method: str, url: str, api_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc


def extract_model_ids(response: dict[str, Any]) -> list[str]:
    data = response.get("data", response)
    if isinstance(data, dict):
        data = data.get("models", data.get("data", []))

    ids: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("name") or item.get("model")
                if isinstance(model_id, str):
                    ids.append(model_id)
    return ids


def model_score(model_id: str) -> int:
    compact = re.sub(r"[^a-z0-9]+", "", model_id.lower())
    lowered = model_id.lower()
    score = 0

    if "image2" in compact:
        score += 100
    if "gptimage2" in compact:
        score += 40
    if "image" in compact and "2" in compact:
        score += 25
    if "image-2" in lowered or "image_2" in lowered:
        score += 20
    if "preview" in lowered:
        score += 3
    if "edit" in lowered or "variation" in lowered:
        score -= 10
    return score


def pick_image2_model(model_ids: list[str]) -> str | None:
    scored = [(model_score(model_id), model_id) for model_id in model_ids]
    scored = [(score, model_id) for score, model_id in scored if score > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], -len(item[1])), reverse=True)
    return scored[0][1]


def choose_model(base_url: str, api_key: str) -> tuple[str, list[str]]:
    models_url = f"{base_url}/models"
    response = request_json("GET", models_url, api_key)
    model_ids = extract_model_ids(response)
    detected = pick_image2_model(model_ids)
    if detected:
        return detected, model_ids

    fallback = os.environ.get("OPENAI_IMAGE_MODEL", "").strip()
    if fallback:
        return fallback, model_ids

    available = ", ".join(model_ids[:20]) if model_ids else "none"
    raise SystemExit(
        "No image-2-like model was found from GET /models, and OPENAI_IMAGE_MODEL is not set. "
        f"First models seen: {available}"
    )


def sanitize_name(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip()[:60]).strip(".-_")
    return value or "image"


def unique_output_path(output_dir: Path, basename: str, extension: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = extension if extension.startswith(".") else f".{extension}"
    candidate = output_dir / f"{basename}{ext}"
    counter = 2
    while candidate.exists():
        candidate = output_dir / f"{basename}-{counter}{ext}"
        counter += 1
    return candidate


def infer_extension(url: str, content_type: str | None) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guessed in {".png", ".jpg", ".jpeg", ".webp"}:
            return guessed
    return ".png"


def download_image(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "image2-generate/0.1"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read(), infer_extension(url, response.headers.get("Content-Type"))


def save_images(response: dict[str, Any], output_dir: Path, prompt: str) -> list[Path]:
    data = response.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Image response has no data array: {json.dumps(response, ensure_ascii=False)[:1000]}")

    saved: list[Path] = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{sanitize_name(prompt)}-{stamp}"

    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("Image response item is not an object.")

        if isinstance(item.get("b64_json"), str):
            raw = base64.b64decode(item["b64_json"])
            extension = ".png"
        elif isinstance(item.get("url"), str):
            raw, extension = download_image(item["url"])
        else:
            raise RuntimeError("Image response item contains neither b64_json nor url.")

        suffix = f"-{index}" if len(data) > 1 else ""
        path = unique_output_path(output_dir, f"{base}{suffix}", extension)
        path.write_bytes(raw)
        saved.append(path)

    return saved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an image after auto-selecting an image-2 model.")
    parser.add_argument("--dry-run", action="store_true", help="Print selected model and payload without POSTing.")
    parser.add_argument("--list-models", action="store_true", help="Only GET /models and print detected IDs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(ROOT / ".env")

    api_key = env_required("OPENAI_API_KEY")
    base_url = normalize_base_url(env_required("OPENAI_BASE_URL"))
    model, model_ids = choose_model(base_url, api_key)

    print(f"Models endpoint: {base_url}/models")
    print(f"Selected model: {model}")

    if args.list_models:
        for model_id in model_ids:
            print(model_id)
        return 0

    payload: dict[str, Any] = {
        "model": model,
        "prompt": PROMPT.strip(),
        "size": os.environ.get("IMAGE_SIZE", "1024x1024"),
        "n": int(os.environ.get("IMAGE_N", "1")),
    }

    response_format = os.environ.get("IMAGE_RESPONSE_FORMAT", "").strip()
    if response_format:
        payload["response_format"] = response_format

    if args.dry_run:
        print(json.dumps({"url": f"{base_url}/images/generations", "payload": payload}, ensure_ascii=False, indent=2))
        return 0

    response = request_json("POST", f"{base_url}/images/generations", api_key, payload)
    output_dir = Path(os.environ.get("OUTPUT_DIR", "outputs")).expanduser()
    saved = save_images(response, output_dir, PROMPT)
    for path in saved:
        print(f"Saved: {path}")
    return 0


# Edit the prompt here. The script intentionally reads the prompt from this block.
PROMPT = """
A cinematic product-style image of a compact transparent cyberpunk music player
on a clean desk, soft studio lighting, crisp details, premium industrial design.
"""


if __name__ == "__main__":
    raise SystemExit(main())
