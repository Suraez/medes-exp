import cv2
from time import time


INPUT_VIDEO = "/action/input.mp4"
OUTPUT_VIDEO = "/tmp/input-output.avi"


def video_processing(video_path):
    video = cv2.VideoCapture(video_path)

    if not video.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))

    fourcc = cv2.VideoWriter_fourcc(*"XVID")

    out = cv2.VideoWriter(
        OUTPUT_VIDEO,
        fourcc,
        20.0,
        (width, height)
    )

    if not out.isOpened():
        video.release()
        raise RuntimeError("Could not create output video")

    start = time()
    frame_count = 0

    while video.isOpened():
        ret, frame = video.read()

        if not ret:
            break

        gray_frame = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2GRAY
        )

        tmp_file_path = "/tmp/tmp.jpg"

        cv2.imwrite(
            tmp_file_path,
            gray_frame
        )

        # Preserve original FunctionBench behavior:
        # read grayscale JPEG back as a normal 3-channel image.
        gray_frame = cv2.imread(tmp_file_path)

        out.write(gray_frame)

        frame_count += 1

    latency = time() - start

    video.release()
    out.release()

    return latency, frame_count


def main(event):
    latencies = {}
    timestamps = {}

    timestamps["starting_time"] = time()
    metadata = event.get("metadata", {})

    latency, frame_count = video_processing(
        INPUT_VIDEO
    )

    latencies["function_execution"] = latency
    timestamps["finishing_time"] = time()

    return {
        "latencies": latencies,
        "timestamps": timestamps,
        "metadata": metadata,
        "frames_processed": frame_count
    }