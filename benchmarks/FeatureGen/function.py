import json
import os
import socket
import threading

from http.server import BaseHTTPRequestHandler, HTTPServer
from feature_extractor import main


# ============================================================
# HTTP request server
# ============================================================

class RequestHandler(BaseHTTPRequestHandler):

    def do_POST(self):

        if self.path != "/run":
            self.send_response(404)
            self.end_headers()
            return

        try:
            content_length = int(
                self.headers.get("Content-Length", 0)
            )

            body = self.rfile.read(content_length)

            event = json.loads(
                body.decode("utf-8")
            )

            result = main(event)

            response = json.dumps(result).encode("utf-8")

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json"
            )
            self.send_header(
                "Content-Length",
                str(len(response))
            )
            self.end_headers()

            self.wfile.write(response)

        except Exception as e:

            response = json.dumps({
                "error": str(e)
            }).encode("utf-8")

            self.send_response(500)
            self.send_header(
                "Content-Type",
                "application/json"
            )
            self.send_header(
                "Content-Length",
                str(len(response))
            )
            self.end_headers()

            self.wfile.write(response)

    def log_message(self, format, *args):
        return


# ============================================================
# Unix-domain control socket
# ============================================================

CONTROL_SOCKET = "/control/invoke.sock"


def unix_control_server():

    # Remove stale socket path when starting normally.
    try:
        os.unlink(CONTROL_SOCKET)
    except FileNotFoundError:
        pass

    server = socket.socket(
        socket.AF_UNIX,
        socket.SOCK_STREAM
    )

    server.bind(CONTROL_SOCKET)

    # Allow the host-side experiment script to access it.
    os.chmod(CONTROL_SOCKET, 0o666)

    server.listen(8)

    print(
        "FeatureGen Unix control socket listening at "
        + CONTROL_SOCKET,
        flush=True
    )

    while True:

        conn, _ = server.accept()

        try:
            request = b""

            while b"\n" not in request:
                chunk = conn.recv(65536)

                if not chunk:
                    break

                request += chunk

            if not request:
                continue

            line = request.split(b"\n", 1)[0]

            event = json.loads(
                line.decode("utf-8")
            )

            # Execute FeatureGen main() inside this same
            # long-lived checkpointed/restored Python process.
            result = main(event)

            response = (
                json.dumps(result) + "\n"
            ).encode("utf-8")

            conn.sendall(response)

        except Exception as e:

            response = (
                json.dumps({
                    "error": str(e)
                }) + "\n"
            ).encode("utf-8")

            try:
                conn.sendall(response)
            except Exception:
                pass

        finally:
            conn.close()


# ============================================================
# Start servers
# ============================================================

if __name__ == "__main__":

    # Deterministic per-container invocation path.
    control_thread = threading.Thread(
        target=unix_control_server,
        daemon=True
    )

    control_thread.start()

    # Preserve existing HTTP interface.
    server = HTTPServer(
        ("0.0.0.0", 8080),
        RequestHandler
    )

    print(
        "FeatureGen HTTP server listening on port 8080",
        flush=True
    )

    server.serve_forever()