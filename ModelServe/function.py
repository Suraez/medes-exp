import sys
sys.path.insert(0, "/action")

import pickle
import torch
import rnn

from time import time


PARAMETER_PATH = "/action/rnn_params.pkl"
MODEL_PATH = "/action/rnn_model.pth"


def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()

    language = event["language"]
    start_letters = event["start_letters"]
    metadata = event.get("metadata", {})

    start = time()

    # Load RNN parameters
    with open(PARAMETER_PATH, "rb") as pkl:
        params = pickle.load(pkl)

    all_categories = params["all_categories"]
    n_categories = params["n_categories"]
    all_letters = params["all_letters"]
    n_letters = params["n_letters"]

    # Construct model
    rnn_model = rnn.RNN(
        n_letters,
        128,
        n_letters,
        all_categories,
        n_categories,
        all_letters,
        n_letters
    )

    # Load trained weights
    rnn_model.load_state_dict(
        torch.load(
            MODEL_PATH,
            map_location="cpu"
        )
    )

    rnn_model.eval()

    # Generate names
    output_names = list(
        rnn_model.samples(
            language,
            start_letters
        )
    )

    latency = time() - start

    latencies["function_execution"] = latency
    timestamps["finishing_time"] = time()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata,
        "output_names": output_names
    }