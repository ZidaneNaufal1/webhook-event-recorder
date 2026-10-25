"""Demo sintetis end-to-end tanpa layanan eksternal atau secret persisten."""
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from recorder import deliver, open_db
from examples.fixture import receiver


def main():
    secret_text = secrets.token_hex(32)
    secret = secret_text.encode("ascii")
    env = dict(os.environ, WEBHOOK_DEMO_SECRET=secret_text)
    body = b'{"type":"study_completed","data":{"minutes":25}}'
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)
        source, target = path / "source.db", path / "target.db"
        with receiver(source, secret) as port:
            assert deliver(port, "demo-001", body, secret) == 201
            assert deliver(port, "demo-001", body, secret) == 200
            assert deliver(port, "demo-001", b'{"type":"study_completed","data":{"minutes":99}}', secret) == 409
            assert deliver(port, "wrong-secret", body, b"x" * 32) == 401
            with closing(open_db(source)) as conn:
                assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        original = source.read_bytes()
        with receiver(target, secret) as port:
            command = [sys.executable, str(ROOT / "recorder.py"), "replay", "--db", str(source), "--event-id", "demo-001", "--port", str(port), "--secret-env", "WEBHOOK_DEMO_SECRET"]
            for expected in [201, 200]:
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                assert result.returncode == 0, result.stderr
                assert json.loads(result.stdout)["status"] == expected
            with closing(open_db(target)) as conn:
                row = conn.execute("SELECT body FROM events WHERE event_id = 'demo-001'").fetchone()
                assert bytes(row["body"]) == body
                assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert source.read_bytes() == original
        print("Demo lulus: simpan 201, duplikat 200, konflik 409, HMAC salah 401.")
        print("Replay CLI ke database lokal kedua: 201 lalu 200; payload identik dan sumber utuh.")
        print("Secret sementara tidak dicetak atau disimpan; tidak ada layanan eksternal dihubungi.")


if __name__ == "__main__":
    main()
