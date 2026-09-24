import pandas as pd

from sklearn.feature_extraction.text import TfidfVectorizer
from time import time
import re


DATASET_PATH = "/action/reviews50mb.csv"

cleanup_re = re.compile("[^a-z]+")


def cleanup(sentence):
    sentence = str(sentence).lower()
    sentence = cleanup_re.sub(" ", sentence).strip()
    return sentence


def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()
    metadata = event.get("metadata", {})

    # Load input dataset
    start = time()

    df = pd.read_csv(DATASET_PATH)

    latencies["load_data"] = time() - start

    # Feature generation
    start = time()

    # Same preprocessing performed by feature_extractor.py
    df["Text"] = df["Text"].apply(cleanup)

    text = df["Text"].tolist()

    # Generate vocabulary / TF-IDF features
    tfidf_vect = TfidfVectorizer()
    tfidf_vect.fit(text)

    feature_names = tfidf_vect.get_feature_names_out()

    latency = time() - start

    latencies["function_execution"] = latency
    timestamps["finishing_time"] = time()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata,
        "num_features": len(feature_names)
    }