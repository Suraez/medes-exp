FROM openwhisk/action-python-v3.10:nightly

RUN pip install --no-cache-dir \
    torch \
    numpy

COPY rnn.py /action/rnn.py
COPY rnn_model.pth /action/rnn_model.pth
COPY rnn_params.pkl /action/rnn_params.pkl

ENV PYTHONPATH="/action:${PYTHONPATH}"