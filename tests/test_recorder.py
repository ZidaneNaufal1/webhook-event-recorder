from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import hmac
import http.client
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import recorder
from examples.fixture import receiver

ROOT = Path(__file__).resolve().parents[1]
SECRET = b"synthetic-test-secret-not-production-123456"
BODY = b'{"type":"test_event","data":{"value":1}}'


class ReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.temp.name) / "events.db"
        cls.fixture = receiver(cls.db, SECRET)
        cls.port = cls.fixture.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.__exit__(None, None, None)
        cls.temp.cleanup()

    def setUp(self):
        self.event_id = self._testMethodName

    def count(self):
        with closing(recorder.open_db(self.db)) as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def request(self, body=BODY, event_id=None, timestamp=None, overrides=None, path="/events", method="POST"):
        event_id = event_id or self.event_id
        stamp = str(int(time.time())) if timestamp is None else timestamp
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body)), "X-Webhook-Id": event_id, "X-Webhook-Timestamp": stamp,
                   "X-Webhook-Signature": recorder.signature(SECRET, stamp, event_id, body)}
        headers.update(overrides or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body, headers)
            response = conn.getresponse()
            status, raw = response.status, response.read()
            self.assertNotIn(SECRET, raw)
            return status, json.loads(raw) if raw else None
        finally:
            conn.close()

    def test_valid_persisted_exact_bytes(self):
        before = self.count()
        self.assertEqual(self.request()[0], 201)
        with closing(recorder.open_db(self.db)) as conn:
            row = conn.execute("SELECT * FROM events WHERE event_id = ?", (self.event_id,)).fetchone()
            self.assertEqual(bytes(row["body"]), BODY)
            self.assertEqual(row["event_type"], "test_event")
            self.assertTrue(row["received_at"].endswith("+00:00"))
        self.assertEqual(self.count(), before + 1)

    def test_duplicate_id_same_body_one_row(self):
        before = self.count()
        self.assertEqual(self.request()[0], 201)
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.count(), before + 1)

    def test_conflicting_body_cannot_replace_first(self):
        self.assertEqual(self.request()[0], 201)
        self.assertEqual(self.request(body=b'{"type":"changed","data":{}}')[0], 409)
        with closing(recorder.open_db(self.db)) as conn:
            self.assertEqual(bytes(conn.execute("SELECT body FROM events WHERE event_id=?", (self.event_id,)).fetchone()[0]), BODY)

    def test_invalid_signature_not_stored(self):
        before = self.count()
        self.assertEqual(self.request(overrides={"X-Webhook-Signature": "sha256=" + "0" * 64})[0], 401)
        self.assertEqual(self.count(), before)

    def test_id_tampering_breaks_authentication(self):
        stamp = str(int(time.time()))
        auth = recorder.signature(SECRET, stamp, "original-id", BODY)
        self.assertEqual(self.request(timestamp=stamp, overrides={"X-Webhook-Signature": auth})[0], 401)

    def test_body_tampering_breaks_authentication(self):
        stamp = str(int(time.time()))
        auth = recorder.signature(SECRET, stamp, self.event_id, BODY)
        self.assertEqual(self.request(body=b'{"type":"changed","data":{}}', timestamp=stamp, overrides={"X-Webhook-Signature": auth})[0], 401)

    def test_old_and_future_timestamps(self):
        before = self.count()
        for offset in [-600, 600]:
            self.assertEqual(self.request(timestamp=str(int(time.time()) + offset))[0], 401)
        self.assertEqual(self.count(), before)

    def test_timestamp_tampering_breaks_authentication(self):
        stamp = str(int(time.time()))
        auth = recorder.signature(SECRET, stamp, self.event_id, BODY)
        self.assertEqual(self.request(timestamp=str(int(stamp) - 1), overrides={"X-Webhook-Signature": auth})[0], 401)

    def test_invalid_auth_shapes(self):
        for header, value in [("X-Webhook-Id", "bad id"), ("X-Webhook-Timestamp", "not-a-number"), ("X-Webhook-Timestamp", "0" * 400), ("X-Webhook-Signature", "sha1=abc")]:
            self.assertEqual(self.request(overrides={header: value})[0], 401)

    def test_malformed_json_schema_and_duplicates(self):
        before = self.count()
        for body in [b"bad", b"\xff", b"[]", b"null", b'{}', b'{"type":"x","data":[]}', b'{"type":1,"data":{}}', b'{"type":"x","data":{},"extra":1}', b'{"type":"x","type":"y","data":{}}', b'{"type":"x","data":{"a":1,"a":2}}', b'{"type":"x","data":{"n":NaN}}', b'{"type":"x","data":{"n":1e999}}', b'{"type":"x","data":{"n":' + b"9" * 5000 + b'}}', b"[" * 1500 + b"]" * 1500]:
            with self.subTest(body=body[:80]):
                self.assertEqual(self.request(body=body)[0], 400)
        self.assertEqual(self.count(), before)

    def test_oversized_refused_without_storing(self):
        before = self.count()
        self.assertEqual(self.request(body=b"", overrides={"Content-Length": str(recorder.MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.count(), before)

    def test_exact_body_limit_accepted(self):
        prefix = b'{"type":"size_boundary","data":{"text":"'
        suffix = b'"}}'
        body = prefix + b"a" * (recorder.MAX_BODY - len(prefix) - len(suffix)) + suffix
        self.assertEqual(len(body), recorder.MAX_BODY)
        self.assertEqual(self.request(body=body)[0], 201)

    def test_empty_invalid_length_and_type(self):
        for overrides, expected in [({"Content-Length": "0"}, 400), ({"Content-Length": "-1"}, 400), ({"Content-Length": "abc"}, 400), ({"Content-Type": "text/plain"}, 415), ({"Transfer-Encoding": "chunked"}, 400), ({"Content-Encoding": "gzip"}, 400)]:
            body = b"" if "Content-Length" in overrides else BODY
            self.assertEqual(self.request(body=body, overrides=overrides)[0], expected)

    def test_route_and_method(self):
        self.assertEqual(self.request(path="/other")[0], 404)
        self.assertEqual(self.request(path="/events?token=x")[0], 404)
        self.assertEqual(self.request(body=b"", method="GET")[0], 405)

    def test_missing_and_duplicate_header(self):
        stamp = str(int(time.time()))
        for duplicate in [False, True]:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                conn.putrequest("POST", "/events")
                conn.putheader("Content-Length", str(len(BODY)))
                conn.putheader("Content-Type", "application/json")
                conn.putheader("X-Webhook-Id", self.event_id)
                conn.putheader("X-Webhook-Timestamp", stamp)
                if duplicate:
                    auth = recorder.signature(SECRET, stamp, self.event_id, BODY)
                    conn.putheader("X-Webhook-Signature", auth)
                    conn.putheader("X-Webhook-Signature", auth)
                conn.endheaders(BODY)
                response = conn.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
            finally:
                conn.close()

    def test_truncated_body(self):
        stamp = str(int(time.time()))
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as conn:
            raw = (f"POST /events HTTP/1.0\r\nContent-Length: 100\r\nContent-Type: application/json\r\nX-Webhook-Id: {self.event_id}\r\nX-Webhook-Timestamp: {stamp}\r\nX-Webhook-Signature: {recorder.signature(SECRET, stamp, self.event_id, BODY)}\r\n\r\n").encode("ascii") + BODY
            conn.sendall(raw)
            conn.shutdown(socket.SHUT_WR)
            self.assertIn(b" 400 ", conn.recv(1024))

    def test_replay_cli_same_server_deduplicates(self):
        self.assertEqual(self.request()[0], 201)
        before = self.count()
        result = subprocess.run([sys.executable, str(ROOT / "recorder.py"), "replay", "--db", str(self.db), "--event-id", self.event_id, "--port", str(self.port)], env=dict(os.environ, WEBHOOK_SECRET=SECRET.decode()), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], 200)
        self.assertEqual(self.count(), before)
        self.assertNotIn(SECRET.decode(), result.stdout + result.stderr)

    def test_list_cli_metadata_only(self):
        self.assertEqual(self.request()[0], 201)
        result = subprocess.run([sys.executable, str(ROOT / "recorder.py"), "list", "--db", str(self.db), "--limit", "1"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)[0]
        self.assertEqual(row["event_id"], self.event_id)
        self.assertNotIn("body", row)
        self.assertEqual(row["bytes"], len(BODY))


class FileAndProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "events.db"

    def test_secret_validation_without_disclosure(self):
        for value in ["", "x" * 31, "x" * 4097]:
            with patch.dict(os.environ, {"TEST_SECRET": value}), self.assertRaises(recorder.RecorderError):
                recorder.get_secret("TEST_SECRET")
        with self.assertRaises(recorder.RecorderError):
            recorder.get_secret("invalid name")
        with patch.dict(os.environ, {"TEST_SECRET": SECRET.decode()}):
            self.assertEqual(recorder.get_secret("TEST_SECRET"), SECRET)

    def test_signature_contract(self):
        expected = "sha256=" + hmac.new(SECRET, b"1792920000.event-1." + BODY, hashlib.sha256).hexdigest()
        self.assertEqual(recorder.signature(SECRET, "1792920000", "event-1", BODY), expected)

    def test_foreign_database_preserved(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TABLE keep (data TEXT)")
        original = self.db.read_bytes()
        with self.assertRaises(recorder.RecorderError):
            recorder.open_db(self.db, create=True)
        self.assertEqual(self.db.read_bytes(), original)

    def test_non_database_file_preserved(self):
        self.db.write_bytes(b"original")
        with self.assertRaises(sqlite3.Error):
            recorder.open_db(self.db, create=True)
        self.assertEqual(self.db.read_bytes(), b"original")

    def test_missing_database_not_created_for_reads(self):
        with self.assertRaises(recorder.RecorderError):
            recorder.open_db(self.db)
        self.assertFalse(self.db.exists())

    def test_schema_version_and_columns_checked(self):
        with closing(recorder.open_db(self.db, create=True)) as conn:
            conn.execute("PRAGMA user_version = 2")
        with self.assertRaises(recorder.RecorderError):
            recorder.open_db(self.db)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("PRAGMA user_version = 1")
            conn.execute("DROP TABLE events")
        with self.assertRaises(recorder.RecorderError):
            recorder.open_db(self.db)

    def test_failed_insert_rollback(self):
        with closing(recorder.open_db(self.db, create=True)) as conn, conn:
            conn.execute("CREATE TRIGGER fail BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            recorder.store_event(self.db, "rollback", str(int(time.time())), BODY, "test_event")
        with closing(recorder.open_db(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_symlink_database_refused(self):
        actual = Path(self.temp.name) / "actual.db"
        with closing(recorder.open_db(actual, create=True)):
            pass
        try:
            self.db.symlink_to(actual)
        except OSError:
            self.skipTest("Sistem tidak memberi izin membuat symlink.")
        with self.assertRaises(recorder.RecorderError):
            recorder.open_db(self.db)

    def test_replay_missing_event_without_network(self):
        with closing(recorder.open_db(self.db, create=True)):
            pass
        with patch.dict(os.environ, {"WEBHOOK_SECRET": SECRET.decode()}), patch("recorder.deliver") as send, redirect_stderr(io.StringIO()):
            code = recorder.main(["replay", "--db", str(self.db), "--event-id", "missing", "--port", "8766"])
        self.assertEqual(code, 2)
        send.assert_not_called()

    def test_bad_cli_arguments(self):
        for args in [["serve", "--port", "0"], ["replay", "--event-id", "x", "--port", "65536"], ["list", "--limit", "101"]]:
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                recorder.main(args + ["--db", str(self.db)])

    def test_list_empty(self):
        with closing(recorder.open_db(self.db, create=True)):
            pass
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(recorder.main(["list", "--db", str(self.db)]), 0)
        self.assertEqual(json.loads(output.getvalue()), [])

    def test_serve_without_secret_does_not_create_db(self):
        with patch.dict(os.environ, {"MISSING_SECRET": ""}), redirect_stderr(io.StringIO()):
            self.assertEqual(recorder.main(["serve", "--db", str(self.db), "--secret-env", "MISSING_SECRET"]), 2)
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
