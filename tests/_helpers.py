"""测试共用工具。"""

import math

from colbatch import ColumnType


def values_equal(expected, actual):
    """NaN 感知的逐值比较, 同时校验 None 位置与 Python 类型。"""
    if len(expected) != len(actual):
        return False
    for left, right in zip(expected, actual):
        if left is None:
            if right is not None:
                return False
            continue
        if type(left) is not type(right):
            return False
        if isinstance(left, float) and math.isnan(left):
            if not (isinstance(right, float) and math.isnan(right)):
                return False
        elif left != right:
            return False
    return True


def columns_equal(expected, actual):
    return all(values_equal(a, b) for a, b in zip(expected, actual))


def type_of(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return ColumnType.BOOL
    if isinstance(value, int):
        return ColumnType.INT64
    if isinstance(value, float):
        return ColumnType.FLOAT64
    if isinstance(value, str):
        return ColumnType.STRING
    raise TypeError(type(value))
