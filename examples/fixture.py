"""Fixture server loopback untuk pengujian dan demo sintetis."""
from contextlib import contextmanager
from threading import Thread
from recorder import make_server


@contextmanager
def receiver(db, secret):
    server = make_server(db, secret, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
