FROM openwhisk/action-python-v3.10:nightly

RUN pip install --no-cache-dir \
    pandas \
    scikit-learn

COPY reviews50mb.csv /action/reviews50mb.csv