import boto3
import os
import pickle
import numpy as np
import torch
import rnn
import gc
import ctypes
import json
import socket
import threading

from time import time
from http.server import BaseHTTPRequestHandler, HTTPServer

tmp = "/tmp/"


# ============================================================
# libc setup: malloc_trim + MADV_MERGEABLE
# ============================================================

libc = ctypes.CDLL("libc.so.6", use_errno=True)

# malloc_trim
libc.malloc_trim.argtypes = [ctypes.c_size_t]
libc.malloc_trim.restype = ctypes.c_int

# madvise
MADV_MERGEABLE = 12

libc.madvise.argtypes = [
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_int
]

libc.madvise.restype = ctypes.c_int


# ============================================================
# Purging
# ============================================================

def purge_memory():
    collected = gc.collect()
    trimmed = libc.malloc_trim(0)

    return {
        "gc_collected": collected,
        "malloc_trim_result": trimmed
    }


# ============================================================
# KSM
# ============================================================

def mark_anonymous_memory_mergeable():
    """
    Mark writable private anonymous mappings of this Python
    process as MADV_MERGEABLE so Linux KSM can scan them.
    """

    marked_regions = 0
    marked_bytes = 0
    failed_regions = 0

    with open("/proc/self/maps", "r") as f:
        mappings = f.readlines()

    for line in mappings:
        parts = line.split()

        if len(parts) < 5:
            continue

        address_range = parts[0]
        permissions = parts[1]
        inode = parts[4]

        # Only writable private mappings
        if "w" not in permissions:
            continue

        if "p" not in permissions:
            continue

        # Anonymous/special mappings
        if inode != "0":
            continue

        pathname = parts[5] if len(parts) >= 6 else ""

        # Do not mark the process stack
        if pathname == "[stack]":
            continue

        start_str, end_str = address_range.split("-")

        start = int(start_str, 16)
        end = int(end_str, 16)
        length = end - start

        ret = libc.madvise(
            ctypes.c_void_p(start),
            ctypes.c_size_t(length),
            MADV_MERGEABLE
        )

        if ret == 0:
            marked_regions += 1
            marked_bytes += length
        else:
            failed_regions += 1

    return {
        "ksm_marked_regions": marked_regions,
        "ksm_marked_mb": marked_bytes / (1024 * 1024),
        "ksm_failed_regions": failed_regions
    }


"""
Language
 - Italian, German, Portuguese, Chinese, Greek, Polish, French
 - English, Spanish, Arabic, Crech, Russian, Irish, Dutch
 - Scottish, Vietnamese, Korean, Japanese
"""


# ============================================================
# Original FunctionBench ModelServe
# ============================================================

def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()

    language = event["language"]
    start_letters = event["start_letters"]

    model_parameter_object_key = event["model_parameter_object_key"]
    model_object_key = event["model_object_key"]
    model_bucket = event["model_bucket"]

    endpoint_url = event["endpoint_url"]
    aws_access_key_id = event["aws_access_key_id"]
    aws_secret_access_key = event["aws_secret_access_key"]

    metadata = event["metadata"]

    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )

    # ========================================================
    # Download model files if not already cached in /tmp
    # ========================================================

    parameter_path = os.path.join(
        tmp,
        os.path.basename(model_parameter_object_key)
    )

    model_path = os.path.join(
        tmp,
        os.path.basename(model_object_key)
    )

    start = time()

    if not os.path.isfile(parameter_path):
        s3_client.download_file(
            model_bucket,
            model_parameter_object_key,
            parameter_path
        )

    if not os.path.isfile(model_path):
        s3_client.download_file(
            model_bucket,
            model_object_key,
            model_path
        )

    download_data = time() - start
    latencies["download_data"] = download_data

    # ========================================================
    # Original ModelServe inference
    # ========================================================

    start = time()

    with open(parameter_path, "rb") as pkl:
        params = pickle.load(pkl)

    all_categories = params["all_categories"]
    n_categories = params["n_categories"]
    all_letters = params["all_letters"]
    n_letters = params["n_letters"]

    rnn_model = rnn.RNN(
        n_letters,
        128,
        n_letters,
        all_categories,
        n_categories,
        all_letters,
        n_letters
    )

    rnn_model.load_state_dict(
        torch.load(model_path)
    )

    rnn_model.eval()

    output_names = list(
        rnn_model.samples(
            language,
            start_letters
        )
    )

    latency = time() - start
    latencies["function_execution"] = latency

    # ========================================================
    # Purge invocation-created state
    # ========================================================

    del output_names
    del rnn_model
    del params

    purge_stats = purge_memory()

    # ========================================================
    # Mark final idle-warm anonymous memory as mergeable
    # ========================================================

    ksm_stats = mark_anonymous_memory_mergeable()

    timestamps["finishing_time"] = time()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata,
        "purge": purge_stats,
        "ksm": ksm_stats
    }




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

    # Remove stale socket path if one exists.
    # This is important when starting normally rather than
    # restoring from a checkpoint.
    try:
        os.unlink(CONTROL_SOCKET)
    except FileNotFoundError:
        pass

    server = socket.socket(
        socket.AF_UNIX,
        socket.SOCK_STREAM
    )

    server.bind(CONTROL_SOCKET)

    # Allow the host-side experiment script to access the socket.
    os.chmod(CONTROL_SOCKET, 0o666)

    server.listen(8)

    print(
        "ModelServe Unix control socket listening at "
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

            # IMPORTANT:
            # main() executes inside this same long-lived
            # checkpointed/restored Python process.
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
# Start server
# ============================================================

if __name__ == "__main__":

    # Start deterministic per-container control channel.
    control_thread = threading.Thread(
        target=unix_control_server,
        daemon=True
    )

    control_thread.start()

    # Keep original HTTP interface available.
    server = HTTPServer(
        ("0.0.0.0", 8080),
        RequestHandler
    )

    print(
        "ModelServe HTTP server listening on port 8080",
        flush=True
    )

    server.serve_forever()