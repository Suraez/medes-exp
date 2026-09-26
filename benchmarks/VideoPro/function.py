import boto3
from botocore.client import Config
import uuid
from time import time
import cv2
import os
import gc
import ctypes


tmp = "/tmp/"
FILE_PATH_INDEX = 2


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
# Video processing
# ============================================================

def video_processing(object_key, video_path):

    # videos/input.mp4 -> input
    file_name = os.path.splitext(
        os.path.basename(object_key)
    )[0]

    result_file_path = tmp + file_name + '-output.avi'

    video = cv2.VideoCapture(video_path)

    width = int(video.get(3))
    height = int(video.get(4))

    fourcc = cv2.VideoWriter_fourcc(*'XVID')

    out = cv2.VideoWriter(
        result_file_path,
        fourcc,
        20.0,
        (width, height)
    )

    start = time()

    while video.isOpened():

        ret, frame = video.read()

        if ret:

            gray_frame = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2GRAY
            )

            tmp_file_path = tmp + 'tmp.jpg'

            cv2.imwrite(
                tmp_file_path,
                gray_frame
            )

            gray_frame = cv2.imread(
                tmp_file_path
            )

            out.write(gray_frame)

        else:
            break

    latency = time() - start

    video.release()
    out.release()

    return latency, result_file_path


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

    # --------------------------------------------------------
    # Download input video
    # --------------------------------------------------------

    download_path = (
        tmp +
        '{}'.format(uuid.uuid4()) +
        os.path.basename(object_key)
    )

    start = time()

    s3_client.download_file(
        input_bucket,
        object_key,
        download_path
    )

    download_latency = time() - start
    latencies["download_data"] = download_latency

    # --------------------------------------------------------
    # Video processing
    # --------------------------------------------------------

    video_processing_latency, upload_path = video_processing(
        object_key,
        download_path
    )

    latencies["function_execution"] = video_processing_latency

    # --------------------------------------------------------
    # Upload output
    # --------------------------------------------------------

    start = time()

    s3_client.upload_file(
        upload_path,
        output_bucket,
        os.path.basename(upload_path)
    )

    upload_latency = time() - start
    latencies["upload_data"] = upload_latency

    timestamps["finishing_time"] = time()

    # --------------------------------------------------------
    # Purge invocation-created Python/native heap state
    # --------------------------------------------------------

    purge_stats = purge_memory()

    # --------------------------------------------------------
    # Mark remaining anonymous memory as KSM mergeable
    # --------------------------------------------------------

    ksm_stats = mark_anonymous_memory_mergeable()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata,
        "purge": purge_stats,
        "ksm": ksm_stats
    }


# ============================================================
# HTTP server
# ============================================================

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


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


if __name__ == "__main__":

    server = HTTPServer(
        ("0.0.0.0", 8080),
        RequestHandler
    )

    print(
        "VideoPro HTTP server listening on port 8080",
        flush=True
    )

    server.serve_forever()