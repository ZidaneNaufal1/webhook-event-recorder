"""Kirim satu payload sintetis ke receiver localhost yang dipilih eksplisit."""
import argparse
import http.client
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recorder import deliver, get_secret, port_number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=port_number, required=True)
    parser.add_argument("--secret-env", default="WEBHOOK_SECRET")
    args = parser.parse_args()
    try:
        status = deliver(args.port, "demo-001", b'{"type":"study_completed","data":{"minutes":25}}', get_secret(args.secret_env))
    except (OSError, ValueError, http.client.HTTPException):
        print("Gagal mengirim demo; periksa secret dan receiver lokal.", file=sys.stderr)
        return 2
    print(f"HTTP {status}")
    return 0 if status in {200, 201} else 1


if __name__ == "__main__":
    raise SystemExit(main())
