"""Receiver webhook localhost dengan HMAC, SQLite dan replay lokal."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time

__version__ = "1.0.0"
MAX_BODY = 65536
WINDOW = 300
APP_ID = 1464156741
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
SCHEMA = """CREATE TABLE events (
    event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL,
    signed_at INTEGER NOT NULL, event_type TEXT NOT NULL, body BLOB NOT NULL
);"""


class RecorderError(ValueError):
    pass


def plain_path(path: Path) -> Path:
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RecorderError("Path symlink/reparse tidak didukung.")
    return path


def open_db(path: Path, create=False):
    path = plain_path(path)
    new = not path.exists()
    if new and not create:
        raise RecorderError("Database belum tersedia.")
    if not new and not path.is_file():
        raise RecorderError("Database harus berupa file biasa.")
    if new:
        with path.open("xb"):
            pass
    conn = sqlite3.connect(path, timeout=3)
    try:
        if new:
            with conn:
                conn.execute(SCHEMA)
                conn.execute(f"PRAGMA application_id = {APP_ID}")
                conn.execute("PRAGMA user_version = 1")
        elif conn.execute("PRAGMA application_id").fetchone()[0] != APP_ID or conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise RecorderError("Database bukan milik recorder schema 1.")
        fields = [r[1] for r in conn.execute("PRAGMA table_info(events)")]
        if fields != ["event_id", "received_at", "signed_at", "event_type", "body"]:
            raise RecorderError("Struktur database recorder tidak sesuai.")
        conn.row_factory = sqlite3.Row
        return conn
    except BaseException:
        conn.close()
        raise


def get_secret(env_name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env_name):
        raise RecorderError("Nama variabel lingkungan tidak valid.")
    try:
        secret = os.environ.get(env_name, "").encode("utf-8")
    except UnicodeError:
        raise RecorderError("Secret harus UTF-8.") from None
    if not 32 <= len(secret) <= 4096:
        raise RecorderError("Variabel secret wajib berisi 32–4096 byte UTF-8.")
    return secret


def signature(secret: bytes, timestamp: str, event_id: str, body: bytes) -> str:
    message = timestamp.encode("ascii") + b"." + event_id.encode("ascii") + b"." + body
    return "sha256=" + hmac.new(secret, message, hashlib.sha256).hexdigest()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecorderError("Kunci JSON berulang.")
        result[key] = value
    return result


def reject_constant(value):
    raise RecorderError("Konstanta JSON tidak valid.")


def parse_integer(value):
    if len(value.lstrip("-")) > 128:
        raise RecorderError("Integer JSON maksimal 128 digit.")
    return int(value)


def parse_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise RecorderError("Angka JSON harus finite.")
    return number


def parse_event(body: bytes):
    if not 1 <= len(body) <= MAX_BODY:
        raise RecorderError("Ukuran payload tidak valid.")
    try:
        event = json.loads(body.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=reject_constant, parse_int=parse_integer, parse_float=parse_float)
    except (UnicodeError, ValueError, RecursionError):
        raise RecorderError("Payload bukan JSON UTF-8 yang valid.") from None
    if not isinstance(event, dict) or set(event) != {"type", "data"} or not isinstance(event["type"], str) or not NAME.fullmatch(event["type"]) or not isinstance(event["data"], dict):
        raise RecorderError("Payload wajib memiliki type (nama ASCII) dan data (object) saja.")
    return event


def store_event(db: Path, event_id, timestamp, body, event_type):
    with closing(open_db(db)) as conn, conn:
        # Write lock sebelum pemeriksaan agar dua pengirim tidak sama-sama insert.
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT body FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if previous is not None:
            return 200 if bytes(previous["body"]) == body else 409
        conn.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                     (event_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), int(timestamp), event_type, body))
    return 201


def make_server(db: Path, secret: bytes, port: int = 8766) -> HTTPServer:
    if not isinstance(secret, bytes) or not 32 <= len(secret) <= 4096:
        raise RecorderError("Secret tidak valid.")
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise RecorderError("Port tidak valid.")
    db = plain_path(db)
    with closing(open_db(db, create=True)):
        pass

    class Handler(BaseHTTPRequestHandler):
        server_version = "WebhookRecorder/" + __version__
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, *args):
            # Tidak mencatat URL/header/payload/secret secara otomatis.
            pass

        def reply(self, code, message):
            self.close_connection = True
            body = json.dumps({"result": message}).encode("ascii")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            except (OSError, TimeoutError):
                pass

        def do_GET(self):
            self.reply(405, "method_not_allowed")

        do_HEAD = do_GET
        do_PUT = do_GET
        do_DELETE = do_GET

        def do_POST(self):
            if len(self.headers.get_all("Content-Length", [])) != 1:
                return self.reply(400, "missing_or_duplicate_header")
            length = self.headers["Content-Length"]
            if not re.fullmatch(r"[0-9]{1,6}", length):
                return self.reply(400, "invalid_length")
            size = int(length)
            if size > MAX_BODY:
                return self.reply(413, "payload_too_large")
            if size == 0:
                return self.reply(400, "empty_body")
            # Baca body kecil berframing jelas sebelum respons penolakan, untuk
            # mencegah reset TCP Windows akibat byte request yang belum dibaca.
            try:
                body = self.rfile.read(size)
                if len(body) != size:
                    return self.reply(400, "incomplete_body")
            except (OSError, TimeoutError):
                return self.reply(408, "read_timeout")
            if self.path != "/events":
                return self.reply(404, "not_found")
            # Tidak memproses streaming/chunked/compressed request body.
            if self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Content-Encoding"):
                return self.reply(400, "unsupported_encoding")
            names = ["Content-Type", "X-Webhook-Id", "X-Webhook-Timestamp", "X-Webhook-Signature"]
            if any(len(self.headers.get_all(key, [])) != 1 for key in names):
                return self.reply(400, "missing_or_duplicate_header")
            media, event_id, timestamp, supplied = (self.headers[key] for key in names)
            if media.lower() not in {"application/json", "application/json; charset=utf-8"}:
                return self.reply(415, "unsupported_media_type")
            if not NAME.fullmatch(event_id) or not re.fullmatch(r"[1-9][0-9]{9,11}", timestamp) or not re.fullmatch(r"sha256=[0-9a-f]{64}", supplied):
                return self.reply(401, "invalid_authentication")
            if abs(int(time.time()) - int(timestamp)) > WINDOW:
                return self.reply(401, "expired_timestamp")
            if not hmac.compare_digest(supplied, signature(secret, timestamp, event_id, body)):
                return self.reply(401, "invalid_signature")
            try:
                event = parse_event(body)
            except RecorderError:
                return self.reply(400, "invalid_payload")
            try:
                code = store_event(db, event_id, timestamp, body, event["type"])
            except (RecorderError, OSError, sqlite3.Error):
                return self.reply(503, "storage_unavailable")
            return self.reply(code, {201: "stored", 200: "duplicate", 409: "id_conflict"}[code])

    # Local only. Tidak ada parameter host, tunnel, atau binding publik.
    return HTTPServer(("127.0.0.1", port), Handler)


def deliver(port: int, event_id: str, body: bytes, secret: bytes):
    if not 1 <= port <= 65535 or not NAME.fullmatch(event_id):
        raise RecorderError("Port atau ID event tidak valid.")
    parse_event(body)
    timestamp = str(int(time.time()))
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("POST", "/events", body=body, headers={
            "Content-Type": "application/json", "X-Webhook-Id": event_id,
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature": signature(secret, timestamp, event_id, body)})
        response = conn.getresponse()
        # Tidak mengikuti redirect; batasi respons dari proses lokal lain.
        response.read(1024)
        return response.status
    finally:
        conn.close()


def port_number(value):
    try:
        port = int(value)
        if not 1 <= port <= 65535:
            raise ValueError
        return port
    except ValueError:
        raise argparse.ArgumentTypeError("Port harus 1–65535.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Simpan webhook HMAC di localhost; replay lokal.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Receiver localhost POST /events")
    serve.add_argument("--port", type=port_number, default=8766)
    listing = sub.add_parser("list", help="Metadata event, tanpa body")
    listing.add_argument("--limit", type=int, choices=range(1, 101), default=20)
    replay = sub.add_parser("replay", help="Kirim ulang satu event ke receiver localhost")
    replay.add_argument("--event-id", required=True)
    replay.add_argument("--port", type=port_number, required=True)
    for command in (serve, replay):
        command.add_argument("--secret-env", default="WEBHOOK_SECRET")
    for command in (serve, listing, replay):
        command.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            server = make_server(args.db, get_secret(args.secret_env), args.port)
            print(f"Receiver lokal: http://127.0.0.1:{args.port}/events; Ctrl+C untuk berhenti.", flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
            return 0
        with closing(open_db(args.db)) as conn:
            if args.command == "list":
                rows = conn.execute("SELECT event_id, received_at, signed_at, event_type, length(body) AS bytes FROM events ORDER BY rowid DESC LIMIT ?", (args.limit,)).fetchall()
                print(json.dumps([dict(row) for row in rows], indent=2, ensure_ascii=True))
                return 0
            if not NAME.fullmatch(args.event_id):
                raise RecorderError("ID event tidak valid.")
            secret = get_secret(args.secret_env)
            row = conn.execute("SELECT body FROM events WHERE event_id = ?", (args.event_id,)).fetchone()
            if row is None:
                raise RecorderError("Event tidak ditemukan.")
            body = bytes(row["body"])
        status = deliver(args.port, args.event_id, body, secret)
        print(json.dumps({"event_id": args.event_id, "status": status}))
        return 0 if status in {200, 201} else 1
    except KeyboardInterrupt:
        print("Receiver dihentikan.", file=sys.stderr)
        return 130
    except RecorderError as exc:
        print(f"Gagal: {exc}", file=sys.stderr)
    except (OSError, sqlite3.Error, http.client.HTTPException):
        print("Gagal: koneksi atau database tidak tersedia.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
