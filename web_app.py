#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from image2_generate import (
    ROOT,
    generate_images,
    load_dotenv,
)


WEB_DIR = ROOT / "web"
DB_PATH = ROOT / "image_history.sqlite3"
DB_LOCK = threading.Lock()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with DB_LOCK, db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                model TEXT,
                image_urls TEXT NOT NULL DEFAULT '[]',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS archived_conversations (
                archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS archived_messages (
                archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id INTEGER NOT NULL,
                original_conversation_id INTEGER NOT NULL,
                archived_conversation_id INTEGER,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT,
                image_urls TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );
            """
        )


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    for key in ("image_urls", "metadata"):
        if key in value:
            try:
                value[key] = json.loads(value[key])
            except json.JSONDecodeError:
                value[key] = [] if key == "image_urls" else {}
    return value


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object.")
    return data


def send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any] | list[Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_error_json(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    send_json(handler, status, {"error": message})


def title_from_prompt(prompt: str) -> str:
    first_line = next((line.strip() for line in prompt.splitlines() if line.strip()), "Untitled")
    return first_line[:80]


def output_url_for(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        relative = path.name
    return "/" + urllib.parse.quote(str(relative).replace(os.sep, "/"))


def split_path(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


class ImageToolHandler(BaseHTTPRequestHandler):
    server_version = "ImageTool/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/conversations":
                self.list_conversations()
            elif parsed.path == "/api/archive":
                self.list_archive()
            elif parsed.path.startswith("/api/conversations/"):
                parts = split_path(parsed.path)
                if len(parts) == 3:
                    self.get_conversation(int(parts[2]))
                else:
                    send_error_json(self, 404, "Not found")
            else:
                self.serve_static(parsed.path)
        except Exception as exc:
            send_error_json(self, 500, str(exc))

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"", "/"}:
            file_path = WEB_DIR / "index.html"
            if file_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/conversations":
                self.create_conversation(read_json(self))
            elif parsed.path.startswith("/api/conversations/"):
                parts = split_path(parsed.path)
                if len(parts) == 4 and parts[3] == "generate":
                    self.generate_for_conversation(int(parts[2]), read_json(self))
                else:
                    send_error_json(self, 404, "Not found")
            else:
                send_error_json(self, 404, "Not found")
        except json.JSONDecodeError:
            send_error_json(self, 400, "Invalid JSON body.")
        except Exception as exc:
            send_error_json(self, 500, str(exc))

    def do_PATCH(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            parts = split_path(parsed.path)
            if len(parts) == 3 and parts[:2] == ["api", "conversations"]:
                self.update_conversation(int(parts[2]), read_json(self))
            elif len(parts) == 3 and parts[:2] == ["api", "messages"]:
                self.update_message(int(parts[2]), read_json(self))
            else:
                send_error_json(self, 404, "Not found")
        except json.JSONDecodeError:
            send_error_json(self, 400, "Invalid JSON body.")
        except Exception as exc:
            send_error_json(self, 500, str(exc))

    def do_DELETE(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            parts = split_path(parsed.path)
            if len(parts) == 3 and parts[:2] == ["api", "conversations"]:
                self.archive_conversation(int(parts[2]))
            elif len(parts) == 3 and parts[:2] == ["api", "messages"]:
                self.archive_message(int(parts[2]))
            else:
                send_error_json(self, 404, "Not found")
        except Exception as exc:
            send_error_json(self, 500, str(exc))

    def list_conversations(self) -> None:
        with DB_LOCK, db_connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*, COUNT(m.id) AS message_count
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                """
            ).fetchall()
        send_json(self, 200, [row_to_dict(row) for row in rows])

    def get_conversation(self, conversation_id: int) -> None:
        with DB_LOCK, db_connect() as conn:
            conversation = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if conversation is None:
                send_error_json(self, 404, "Conversation not found.")
                return
            messages = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
        payload = row_to_dict(conversation)
        payload["messages"] = [row_to_dict(row) for row in messages]
        send_json(self, 200, payload)

    def create_conversation(self, data: dict[str, Any]) -> None:
        prompt = str(data.get("prompt", "")).strip()
        title = str(data.get("title", "")).strip() or title_from_prompt(prompt)
        timestamp = now_iso()
        with DB_LOCK, db_connect() as conn:
            cursor = conn.execute(
                "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
                (title, timestamp, timestamp),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("Failed to create conversation.")
            conversation_id = int(cursor.lastrowid)
        self.get_conversation(conversation_id)

    def update_conversation(self, conversation_id: int, data: dict[str, Any]) -> None:
        title = str(data.get("title", "")).strip()
        if not title:
            send_error_json(self, 400, "Title is required.")
            return
        with DB_LOCK, db_connect() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now_iso(), conversation_id),
            )
            if cursor.rowcount == 0:
                send_error_json(self, 404, "Conversation not found.")
                return
        self.get_conversation(conversation_id)

    def generate_for_conversation(self, conversation_id: int, data: dict[str, Any]) -> None:
        prompt = str(data.get("prompt", "")).strip()
        if not prompt:
            send_error_json(self, 400, "Prompt is required.")
            return

        with DB_LOCK, db_connect() as conn:
            conversation = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if conversation is None:
                send_error_json(self, 404, "Conversation not found.")
                return
            timestamp = now_iso()
            conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, created_at, updated_at)
                VALUES (?, 'user', ?, ?, ?)
                """,
                (conversation_id, prompt, timestamp, timestamp),
            )
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title_from_prompt(prompt), timestamp, conversation_id),
            )

        try:
            model, payload, saved = generate_images(prompt)
            image_urls = [output_url_for(path) for path in saved]
            content = f"Generated {len(saved)} image(s)."
            metadata = {"payload": payload, "paths": [str(path) for path in saved]}
        except Exception as exc:
            model = None
            image_urls = []
            content = f"Generation failed: {exc}"
            metadata = {"error": str(exc)}

        timestamp = now_iso()
        with DB_LOCK, db_connect() as conn:
            conn.execute(
                """
                INSERT INTO messages
                    (conversation_id, role, content, model, image_urls, metadata, created_at, updated_at)
                VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    content,
                    model,
                    json.dumps(image_urls, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (timestamp, conversation_id))
        self.get_conversation(conversation_id)

    def update_message(self, message_id: int, data: dict[str, Any]) -> None:
        content = str(data.get("content", "")).strip()
        if not content:
            send_error_json(self, 400, "Content is required.")
            return
        with DB_LOCK, db_connect() as conn:
            row = conn.execute("SELECT conversation_id FROM messages WHERE id = ?", (message_id,)).fetchone()
            if row is None:
                send_error_json(self, 404, "Message not found.")
                return
            timestamp = now_iso()
            conn.execute(
                "UPDATE messages SET content = ?, updated_at = ? WHERE id = ?",
                (content, timestamp, message_id),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (timestamp, int(row["conversation_id"])),
            )
        self.get_conversation(int(row["conversation_id"]))

    def archive_conversation(self, conversation_id: int) -> None:
        with DB_LOCK, db_connect() as conn:
            conversation = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if conversation is None:
                send_error_json(self, 404, "Conversation not found.")
                return
            archived_at = now_iso()
            cursor = conn.execute(
                """
                INSERT INTO archived_conversations
                    (original_id, title, created_at, updated_at, archived_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(conversation["id"]),
                    conversation["title"],
                    conversation["created_at"],
                    conversation["updated_at"],
                    archived_at,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("Failed to archive conversation.")
            archived_conversation_id = int(cursor.lastrowid)
            messages = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
            for message in messages:
                conn.execute(
                    """
                    INSERT INTO archived_messages
                        (original_id, original_conversation_id, archived_conversation_id, role, content, model,
                         image_urls, metadata, created_at, updated_at, archived_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(message["id"]),
                        int(message["conversation_id"]),
                        archived_conversation_id,
                        message["role"],
                        message["content"],
                        message["model"],
                        message["image_urls"],
                        message["metadata"],
                        message["created_at"],
                        message["updated_at"],
                        archived_at,
                    ),
                )
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        send_json(self, 200, {"archived": True, "id": conversation_id})

    def archive_message(self, message_id: int) -> None:
        with DB_LOCK, db_connect() as conn:
            message = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            if message is None:
                send_error_json(self, 404, "Message not found.")
                return
            archived_at = now_iso()
            conn.execute(
                """
                INSERT INTO archived_messages
                    (original_id, original_conversation_id, role, content, model, image_urls, metadata,
                     created_at, updated_at, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(message["id"]),
                    int(message["conversation_id"]),
                    message["role"],
                    message["content"],
                    message["model"],
                    message["image_urls"],
                    message["metadata"],
                    message["created_at"],
                    message["updated_at"],
                    archived_at,
                ),
            )
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (archived_at, int(message["conversation_id"])),
            )
        self.get_conversation(int(message["conversation_id"]))

    def list_archive(self) -> None:
        with DB_LOCK, db_connect() as conn:
            conversations = conn.execute(
                "SELECT * FROM archived_conversations ORDER BY archived_at DESC"
            ).fetchall()
            messages = conn.execute("SELECT * FROM archived_messages ORDER BY archived_at DESC LIMIT 200").fetchall()
        send_json(
            self,
            200,
            {
                "conversations": [row_to_dict(row) for row in conversations],
                "messages": [row_to_dict(row) for row in messages],
            },
        )

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            file_path = WEB_DIR / "index.html"
        elif path.startswith("/outputs/"):
            file_path = (ROOT / path.lstrip("/")).resolve()
            if not str(file_path).startswith(str((ROOT / "outputs").resolve())):
                send_error_json(self, 403, "Forbidden")
                return
        else:
            file_path = (WEB_DIR / path.lstrip("/")).resolve()
            if not str(file_path).startswith(str(WEB_DIR.resolve())):
                send_error_json(self, 403, "Forbidden")
                return

        if not file_path.exists() or not file_path.is_file():
            send_error_json(self, 404, "Not found")
            return

        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif file_path.suffix in {".html", ".css"}:
            content_type = f"text/{file_path.suffix[1:]}; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the image generation web UI.")
    parser.add_argument("--host", default=os.environ.get("WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEB_PORT", "3000")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(ROOT / ".env")
    init_db()
    server = ThreadingHTTPServer((args.host, args.port), ImageToolHandler)
    print(f"Web UI: http://{args.host}:{args.port}")
    print(f"SQLite: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
