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
OUTPUT_DIR=outputs
OPENAI_IMAGE_MODEL=gpt-image-1
```

`OPENAI_IMAGE_MODEL` is only used as a fallback when `GET /models` does not contain an `image-2`-like model.

## Run

```bash
UV_CACHE_DIR=.uv-cache uv run python image2_generate.py --dry-run
UV_CACHE_DIR=.uv-cache uv run python image2_generate.py
```

To inspect model IDs only:

```bash
UV_CACHE_DIR=.uv-cache uv run python image2_generate.py --list-models
```

Edit the image prompt at the bottom of `image2_generate.py`.
