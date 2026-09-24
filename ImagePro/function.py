import sys
sys.path.insert(0, "/action")

from time import time
from PIL import Image

import ops

INPUT_IMAGE = "/action/input.jpg"


def image_processing(file_name, image_path):
    path_list = []

    start = time()

    with Image.open(image_path) as image:
        path_list += ops.flip(image, file_name)
        path_list += ops.rotate(image, file_name)
        path_list += ops.filter(image, file_name)
        path_list += ops.gray_scale(image, file_name)
        path_list += ops.resize(image, file_name)

    latency = time() - start

    return latency, path_list


def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()
    metadata = event.get("metadata", {})

    latency, path_list = image_processing(
        "input.jpg",
        INPUT_IMAGE
    )

    latencies["function_execution"] = latency
    timestamps["finishing_time"] = time()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata,
        "num_outputs": len(path_list)
    }