from time import time
import six
import json
import gc
import ctypes
from chameleon import PageTemplate


BIGTABLE_ZPT = """\
<table xmlns="http://www.w3.org/1999/xhtml"
xmlns:tal="http://xml.zope.org/namespaces/tal">
<tr tal:repeat="row python: options['table']">
<td tal:repeat="c python: row.values()">
<span tal:define="d python: c + 1"
tal:attributes="class python: 'column-' + %s(d)"
tal:content="python: d" />
</td>
</tr>
</table>""" % six.text_type.__name__


# ============================================================
# KSM
# ============================================================

# Alpine uses musl rather than glibc, so we do not use
# glibc-specific malloc_trim().
libc = ctypes.CDLL(None, use_errno=True)

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

        # Writable private mappings only
        if "w" not in permissions or "p" not in permissions:
            continue

        # Anonymous mappings only
        if inode != "0":
            continue

        pathname = parts[5] if len(parts) >= 6 else ""

        # Exclude stack
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
# Original HTTPServe workload
# ============================================================

def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()

    num_of_rows = event['num_of_rows']
    num_of_cols = event['num_of_cols']
    metadata = event['metadata']

    start = time()

    tmpl = PageTemplate(BIGTABLE_ZPT)

    data = {}

    for i in range(num_of_cols):
        data[str(i)] = i

    table = [data for x in range(num_of_rows)]
    options = {'table': table}

    data = tmpl.render(options=options)

    latency = time() - start

    latencies["function_execution"] = latency
    timestamps["finishing_time"] = time()

    # ========================================================
    # Release invocation-specific objects
    # ========================================================

    del data
    del options
    del table
    del tmpl

    # ========================================================
    # Garbage collection
    # ========================================================

    gc_collected = gc.collect()

    # ========================================================
    # Mark remaining anonymous memory KSM-mergeable
    # ========================================================

    ksm_stats = mark_anonymous_memory_mergeable()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata,
        "gc_collected": gc_collected,
        "ksm": ksm_stats
    }