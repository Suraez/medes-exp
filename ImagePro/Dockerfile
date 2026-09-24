FROM openwhisk/action-python-v3.10:nightly

RUN pip install --no-cache-dir Pillow

COPY ops.py /action/ops.py
COPY input.jpg /action/input.jpg

ENV PYTHONPATH="/action:${PYTHONPATH}"