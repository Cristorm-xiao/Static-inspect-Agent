from math import *


def accumulate(value, bucket=[]):
    bucket.append(value)
    return bucket


try:
    result = eval("1 + 2")
except:
    result = 0
