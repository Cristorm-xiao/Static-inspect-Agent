def accumulate(value, bucket=None):
    if bucket is None:
        bucket = []
    bucket.append(value)
    return bucket
