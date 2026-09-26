import boto3
import pandas as pd
import re
import io
import gc
import ctypes

from time import time


cleanup_re = re.compile('[^a-z]+')


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
# Original FeatureGen
# ============================================================

def cleanup(sentence):
    sentence = sentence.lower()
    sentence = cleanup_re.sub(' ', sentence).strip()
    return sentence


def main(args):

    bucket = args['input_bucket']
    key = args['key']

    endpoint_url = args['endpoint_url']
    aws_access_key_id = args['aws_access_key_id']
    aws_secret_access_key = args['aws_secret_access_key']

    metadata = args.get('metadata', {})

    # --------------------------------------------------------
    # MinIO / S3 client
    # --------------------------------------------------------

    s3 = boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )

    # --------------------------------------------------------
    # Download CSV from MinIO
    # --------------------------------------------------------

    obj = s3.get_object(
        Bucket=bucket,
        Key=key
    )

    df = pd.read_csv(
        io.BytesIO(
            obj['Body'].read()
        )
    )

    # --------------------------------------------------------
    # Original FeatureGen computation
    # --------------------------------------------------------

    start = time()

    df['Text'] = df['Text'].apply(cleanup)

    text = df['Text'].tolist()

    result = set()

    for item in text:
        result.update(
            item.split()
        )

    print(
        "Number of Feature : "
        + str(len(result))
    )

    feature = str(
        list(result)
    )

    feature = (
        feature
        .lstrip('[')
        .rstrip(']')
        .replace(' ', '')
    )

    latency = time() - start

    print(latency)

    # --------------------------------------------------------
    # Store intermediate feature file back in MinIO
    # --------------------------------------------------------

    write_key = (
        args['key']
        .split('.')[0]
        + ".txt"
    )

    s3.put_object(
        Body=feature,
        Bucket=bucket,
        Key=write_key
    )

    # Save the value needed for the response before deleting result.
    num_features = len(result)

    # ========================================================
    # Purge invocation-created state
    # ========================================================

    del df
    del text
    del result
    del feature
    del obj

    purge_stats = purge_memory()

    # ========================================================
    # Mark final idle anonymous memory as KSM mergeable
    # ========================================================

    ksm_stats = mark_anonymous_memory_mergeable()

    return {
        "latency": latency,
        "metadata": metadata,
        "num_features": num_features,
        "purge": purge_stats,
        "ksm": ksm_stats
    }