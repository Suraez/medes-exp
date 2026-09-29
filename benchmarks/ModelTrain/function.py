import boto3
import ctypes
import gc
import json

from http.server import BaseHTTPRequestHandler, HTTPServer

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.externals import joblib

import pandas as pd
from time import time
import re
import io

import os
import socket
import threading


cleanup_re = re.compile('[^a-z]+')
tmp = '/tmp/'


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


# ============================================================
# Original FunctionBench ModelTrain
# ============================================================

def cleanup(sentence):
    sentence = sentence.lower()
    sentence = cleanup_re.sub(' ', sentence).strip()
    return sentence


def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()

    dataset_bucket = event['dataset_bucket']
    dataset_object_key = event['dataset_object_key']

    model_bucket = event['model_bucket']
    model_object_key = event['model_object_key']

    endpoint_url = event['endpoint_url']
    aws_access_key_id = event['aws_access_key_id']
    aws_secret_access_key = event['aws_secret_access_key']

    metadata = event['metadata']

    s3_client = boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )

    # --------------------------------------------------------
    # Download dataset
    # --------------------------------------------------------

    start = time()

    obj = s3_client.get_object(
        Bucket=dataset_bucket,
        Key=dataset_object_key
    )

    download_data = time() - start
    latencies["download_data"] = download_data

    df = pd.read_csv(
        io.BytesIO(
            obj['Body'].read()
        )
    )

    # --------------------------------------------------------
    # Train model
    # --------------------------------------------------------

    start = time()

    df['train'] = df['Text'].apply(cleanup)

    tfidf_vector = TfidfVectorizer(
        min_df=100
    ).fit(
        df['train']
    )

    train = tfidf_vector.transform(
        df['train']
    )

    model = LogisticRegression()

    model.fit(
        train,
        df['Score']
    )

    function_execution = time() - start
    latencies["function_execution"] = function_execution

    # --------------------------------------------------------
    # Save model
    # --------------------------------------------------------

    model_file_path = (
        tmp + model_object_key
    )

    joblib.dump(
        model,
        model_file_path
    )

    # --------------------------------------------------------
    # Upload model
    # --------------------------------------------------------

    start = time()

    s3_client.upload_file(
        model_file_path,
        model_bucket,
        model_object_key
    )

    upload_data = time() - start
    latencies["upload_data"] = upload_data

    # ========================================================
    # Purge invocation-created state
    # ========================================================

    del df
    del train
    del tfidf_vector
    del model
    del obj

    purge_stats = purge_memory()

    # ========================================================
    # Mark remaining anonymous memory as KSM mergeable
    #
    # Do this AFTER purging so KSM operates on the final
    # idle-warm memory state.
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
# Lightweight HTTP request server
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

            # Execute the SAME benchmark function.
            result = main(event)

            response = json.dumps(
                result
            ).encode("utf-8")

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

        print(
            "%s - - [%s] %s" %
            (
                self.client_address[0],
                self.log_date_time_string(),
                format % args
            ),
            flush=True
        )


# ============================================================
# Unix-domain control server
#
# Used only by the Docker/CRIU experiment so that a specific
# restored clone can be invoked deterministically.
#
# The benchmark execution itself is still main(event).
# ============================================================

CONTROL_SOCKET = "/control/invoke.sock"


def unix_control_server():

    # Remove a stale socket path if one exists.
    try:
        os.unlink(CONTROL_SOCKET)
    except OSError:
        pass

    server = socket.socket(
        socket.AF_UNIX,
        socket.SOCK_STREAM
    )

    server.bind(CONTROL_SOCKET)

    # Allow the host-side experiment script to connect.
    os.chmod(CONTROL_SOCKET, 0o666)

    server.listen(1)

    print(
        "Unix control server listening on {}".format(
            CONTROL_SOCKET
        ),
        flush=True
    )

    while True:

        conn, _ = server.accept()

        try:
            # Protocol:
            #
            # client sends:
            #     <JSON>\n
            #
            # server returns:
            #     <JSON>\n

            chunks = []

            while True:

                data = conn.recv(65536)

                if not data:
                    break

                chunks.append(data)

                if b"\n" in data:
                    break

            request = b"".join(chunks)

            # Only process the first newline-delimited request.
            request = request.split(b"\n", 1)[0]

            event = json.loads(
                request.decode("utf-8")
            )

            # IMPORTANT:
            # Execute ModelTrain in THIS restored Python process.
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

    # Start the per-container Unix control channel.
    control_thread = threading.Thread(
        target=unix_control_server
    )

    control_thread.daemon = True
    control_thread.start()

    # Keep the existing HTTP interface.
    server = HTTPServer(
        ("0.0.0.0", 8080),
        RequestHandler
    )

    print(
        "ModelTrain server listening on port 8080",
        flush=True
    )

    server.serve_forever()