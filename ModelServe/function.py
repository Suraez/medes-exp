import boto3
import os
import pickle
import numpy as np
import torch
import rnn
import gc
import ctypes

from time import time


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

    language = event['language']
    start_letters = event['start_letters']

    model_parameter_object_key = event['model_parameter_object_key']
    model_object_key = event['model_object_key']
    model_bucket = event['model_bucket']

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

    with open(parameter_path, 'rb') as pkl:
        params = pickle.load(pkl)

    all_categories = params['all_categories']
    n_categories = params['n_categories']
    all_letters = params['all_letters']
    n_letters = params['n_letters']

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
    # Mark remaining anonymous memory as KSM mergeable
    #
    # IMPORTANT:
    # Do this AFTER purging so KSM scans the final idle-warm
    # memory state.
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