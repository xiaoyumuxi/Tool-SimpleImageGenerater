# Image 2 Relay Generator

This is a small `uv` Python tool for OpenAI-compatible relay endpoints.

It reads `.env` from the current working directory, calls `GET /models`, selects the best model ID that looks related to `image-2`, then calls `/images/generations`.

## Configure

Edit `.env`:

```dotenv
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://your-relay.example.com/v1
```

Optional values:

```dotenv
IMAGE_SIZE=1024x1024
IMAGE_N=1
IMAGE_QUALITY=medium
OUTPUT_DIR=outputs
OPENAI_IMAGE_MODEL=gpt-image-1
```

`OPENAI_IMAGE_MODEL` is only used as a fallback when `GET /models` does not contain an `image-2`-like model.
`IMAGE_QUALITY`, `IMAGE_OUTPUT_FORMAT`, `IMAGE_BACKGROUND`, and `IMAGE_OUTPUT_COMPRESSION` are passed through when set.

Some relay providers block Python's default HTTP/TLS fingerprint. This project uses `curl_cffi` by default after `uv sync`, with browser-like headers. To force the standard-library client for debugging:

```dotenv
IMAGE_HTTP_CLIENT=urllib
```

## Run

```bash
UV_CACHE_DIR=.uv-cache uv sync
UV_CACHE_DIR=.uv-cache uv run python image2_generate.py --dry-run
UV_CACHE_DIR=.uv-cache uv run python image2_generate.py
```

To inspect model IDs only:

```bash
UV_CACHE_DIR=.uv-cache uv run python image2_generate.py --list-models
```

Edit the image prompt at the bottom of `image2_generate.py`.

## Web UI

Start the local app:

```bash
UV_CACHE_DIR=.uv-cache uv run python web_app.py
```

Then open:

```text
http://127.0.0.1:3000
```

The web UI lets you create conversations, edit prompts/messages, send image generation requests, render finished images directly in the page, rename conversations, search history, and archive conversations/messages. Archive actions copy rows into SQLite archive tables before removing them from the active tables.

Data is stored in `image_history.sqlite3`. Generated files still go to `OUTPUT_DIR` from `.env` (`outputs` by default).
