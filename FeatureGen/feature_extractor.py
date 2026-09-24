import boto3
import pandas as pd
import re
import io

from time import time


cleanup_re = re.compile('[^a-z]+')


def cleanup(sentence):
    sentence = sentence.lower()
    sentence = cleanup_re.sub(' ', sentence).strip()
    return sentence


def main(args):

    bucket = args['input_bucket']
    key = args['key']

    endpoint_url = args['endpoint_url']
    aws_access_key_id = args['aws_access_key_id']
    aws_secret_access_key = args['aws_secret_access_key']

    metadata = args.get('metadata', {})

    # --------------------------------------------------------
    # MinIO / S3 client
    # --------------------------------------------------------

    s3 = boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )

    # --------------------------------------------------------
    # Download CSV from MinIO
    #
    # This replaces only:
    # pd.read_csv("s3://" + path)
    #
    # Core FeatureGen computation remains unchanged.
    # --------------------------------------------------------

    obj = s3.get_object(
        Bucket=bucket,
        Key=key
    )

    df = pd.read_csv(
        io.BytesIO(
            obj['Body'].read()
        )
    )

    # --------------------------------------------------------
    # Original FeatureGen computation
    # --------------------------------------------------------

    start = time()

    df['Text'] = df['Text'].apply(cleanup)

    text = df['Text'].tolist()

    result = set()

    for item in text:
        result.update(
            item.split()
        )

    print(
        "Number of Feature : "
        + str(len(result))
    )

    feature = str(
        list(result)
    )

    feature = (
        feature
        .lstrip('[')
        .rstrip(']')
        .replace(' ', '')
    )

    latency = time() - start

    print(latency)

    # --------------------------------------------------------
    # Store intermediate feature file back in MinIO
    # --------------------------------------------------------

    write_key = (
        args['key']
        .split('.')[0]
        + ".txt"
    )

    s3.put_object(
        Body=feature,
        Bucket=bucket,
        Key=write_key
    )

    return {
        "latency": latency,
        "metadata": metadata,
        "num_features": len(result)
    }