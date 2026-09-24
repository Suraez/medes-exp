from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

import pandas as pd
import joblib
from time import time
import re


# ============================================================
# Configuration
# ============================================================

DATASET_PATH = "/action/reviews50mb.csv"
MODEL_PATH = "/tmp/lr_model.pk"

cleanup_re = re.compile("[^a-z]+")


# ============================================================
# Text preprocessing
# ============================================================

def cleanup(sentence):
    sentence = str(sentence).lower()
    sentence = cleanup_re.sub(" ", sentence).strip()
    return sentence


# ============================================================
# OpenWhisk action
# ============================================================

def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()
    metadata = event.get("metadata", {})

    # --------------------------------------------------------
    # Load dataset
    # --------------------------------------------------------

    start = time()

    df = pd.read_csv(DATASET_PATH)

    load_data = time() - start
    latencies["load_data"] = load_data

    # --------------------------------------------------------
    # Train model
    # --------------------------------------------------------

    start = time()

    # Clean review text
    df["train"] = df["Text"].apply(cleanup)

    # Convert text to TF-IDF features
    tfidf_vector = TfidfVectorizer(
        min_df=100
    ).fit(df["train"])

    train = tfidf_vector.transform(df["train"])

    # Train Logistic Regression classifier
    model = LogisticRegression()
    model.fit(train, df["Score"])

    function_execution = time() - start
    latencies["function_execution"] = function_execution

    # --------------------------------------------------------
    # Save trained model locally
    # --------------------------------------------------------

    joblib.dump(model, MODEL_PATH)

    timestamps["finishing_time"] = time()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata
    }