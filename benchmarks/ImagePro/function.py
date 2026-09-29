import boto3
import uuid
import os
import gc
import ctypes
from time import time
from PIL import Image

import ops

import socket
import threading



import json
from http.server import BaseHTTPRequestHandler, HTTPServer

FILE_NAME_INDEX = 1


# ============================================================
# Purging
# ============================================================

libc = ctypes.CDLL("libc.so.6", use_errno=True)

libc.malloc_trim.argtypes = [ctypes.c_size_t]
libc.malloc_trim.restype = ctypes.c_int


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

MADV_MERGEABLE = 12

libc.madvise.argtypes = [
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_int
]
libc.madvise.restype = ctypes.c_int


def mark_anonymous_memory_mergeable():
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
        if "w" not in permissions or "p" not in permissions:
            continue

        # Only anonymous mappings
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


# ============================================================
# Original ImagePro computation
# ============================================================

def image_processing(file_name, image_path):
    path_list = []

    start = time()

    with Image.open(image_path) as image:
        tmp = image

        path_list += ops.flip(image, file_name)
        path_list += ops.rotate(image, file_name)
        path_list += ops.filter(image, file_name)
        path_list += ops.gray_scale(image, file_name)
        path_list += ops.resize(image, file_name)

    latency = time() - start

    print("PATH_LIST", path_list)

    return latency, path_list


# ============================================================
# Main
# ============================================================

def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()

    input_bucket = event['input_bucket']
    object_key = event['object_key']
    output_bucket = event['output_bucket']
    endpoint_url = event['endpoint_url']
    aws_access_key_id = event['aws_access_key_id']
    aws_secret_access_key = event['aws_secret_access_key']
    metadata = event['metadata']

    # --------------------------------------------------------
    # MinIO client
    # --------------------------------------------------------

    s3_client = boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )

    # images/input.jpg -> input.jpg
    file_name = os.path.basename(object_key)

    # --------------------------------------------------------
    # Download input
    # --------------------------------------------------------

    start = time()

    download_path = '/tmp/{}-{}'.format(
        uuid.uuid4(),
        file_name
    )

    s3_client.download_file(
        input_bucket,
        object_key,
        download_path
    )

    download_latency = time() - start
    latencies["download_data"] = download_latency

    # --------------------------------------------------------
    # Original image processing
    # --------------------------------------------------------

    image_processing_latency, path_list = image_processing(
        file_name,
        download_path
    )

    latencies["function_execution"] = image_processing_latency

    print("PATH_LIST OUTSIDE", path_list)

    # --------------------------------------------------------
    # Upload outputs
    # --------------------------------------------------------

    start = time()

    for upload_path in path_list:
        s3_client.upload_file(
            upload_path,
            output_bucket,
            upload_path.split("/")[FILE_NAME_INDEX]
        )

    upload_latency = time() - start
    latencies["upload_data"] = upload_latency

    timestamps["finishing_time"] = time()

    # --------------------------------------------------------
    # Release invocation-specific Python references
    # --------------------------------------------------------

    del path_list
    del upload_path

    # --------------------------------------------------------
    # Purge
    # --------------------------------------------------------

    purge_stats = purge_memory()

    # --------------------------------------------------------
    # Mark remaining anonymous memory KSM-mergeable
    # --------------------------------------------------------

    ksm_stats = mark_anonymous_memory_mergeable()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata,
        "purge": purge_stats,
        "ksm": ksm_stats
    }


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

    try:
        os.unlink(CONTROL_SOCKET)
    except FileNotFoundError:
        pass

    server = socket.socket(
        socket.AF_UNIX,
        socket.SOCK_STREAM
    )

    server.bind(CONTROL_SOCKET)

    os.chmod(CONTROL_SOCKET, 0o666)

    server.listen(8)

    print(
        "ImagePro Unix control socket listening at "
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

            # Execute ImagePro main() inside this same
            # long-lived checkpointed/restored process.
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

if __name__ == "__main__":

    control_thread = threading.Thread(
        target=unix_control_server,
        daemon=True
    )

    control_thread.start()

    server = HTTPServer(
        ("0.0.0.0", 8080),
        RequestHandler
    )

    print(
        "ImagePro HTTP server listening on port 8080",
        flush=True
    )

    server.serve_forever()