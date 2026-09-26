from time import time


INPUT_PATH = "/action/mapreduce_input.txt"

subs = "</title><text>"

computer_language = [
    "JavaScript", "Java", "PHP", "Python", "C#",
    "C++", "Ruby", "CSS", "Objective-C", "Perl",
    "Scala", "Haskell", "MATLAB", "Clojure", "Groovy"
]


def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()
    metadata = event.get("metadata", {})

    output = {}

    for lang in computer_language:
        output[lang] = 0

    start = time()

    with open(INPUT_PATH, "r", encoding="utf-8", errors="ignore") as f:
        contents = f.read()

    for line in contents.splitlines():
        idx = line.find(subs)

        if idx == -1:
            continue

        text = line[idx + len(subs):]

        # Preserve the basic FunctionBench map operation
        for lang in computer_language:
            if lang in text:
                output[lang] += 1

    latency = time() - start

    latencies["function_execution"] = latency
    timestamps["finishing_time"] = time()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata,
        "output": output
    }