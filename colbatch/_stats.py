"""列级最小/最大值统计的计算与序列化。

minmax 块布局: tag u8 后跟随类型相关的定长内容:
    int64   : tag=1, min i64(8) + max i64(8)
    float64 : tag=2, min f64(8) + max f64(8)   (存在 NaN 时整块省略)
    bool    : tag=3, min u8 + max u8
    string  : tag=4, min_len u32 + min bytes + max_len u32 + max bytes
字符串按 UTF-8 字节序比较, 得到的结果与输入顺序无关且完全确定。
"""

import math
import struct

from ._constants import (
    MINMAX_BOOL,
    MINMAX_FLOAT64,
    MINMAX_INT64,
    MINMAX_STRING,
)
from .errors import CorruptFormatError
from .schema import ColumnType


def build_minmax(values, col_type: ColumnType):
    """对非空值序列计算 minmax 字节; 没有非空值或含 NaN 时返回 None。"""
    if not values:
        return None
    if col_type is ColumnType.INT64:
        lo, hi = min(values), max(values)
        return bytes([MINMAX_INT64]) + struct.pack("<qq", lo, hi)
    if col_type is ColumnType.FLOAT64:
        floats = [float(v) for v in values]
        if any(math.isnan(v) for v in floats):
            return None
        lo, hi = min(floats), max(floats)
        return bytes([MINMAX_FLOAT64]) + struct.pack("<dd", lo, hi)
    if col_type is ColumnType.BOOL:
        lo = min(values)
        hi = max(values)
        return bytes([MINMAX_BOOL, int(lo), int(hi)])
    if col_type is ColumnType.STRING:
        encoded = [v.encode("utf-8") for v in values]
        lo = min(encoded)
        hi = max(encoded)
        return (
            bytes([MINMAX_STRING])
            + struct.pack("<I", len(lo))
            + lo
            + struct.pack("<I", len(hi))
            + hi
        )
    raise CorruptFormatError(f"unsupported type for minmax: {col_type}")


def parse_minmax(reader, byte_count: int):
    """解析 minmax 块, 返回 (tag, min, max) 或 None。"""
    if byte_count == 0:
        return None
    start = reader.pos
    tag = reader.read_u8()
    if tag == MINMAX_INT64:
        lo = reader.read_i64()
        hi = reader.read_i64()
        result = (tag, lo, hi)
    elif tag == MINMAX_FLOAT64:
        lo = reader.read_f64()
        hi = reader.read_f64()
        result = (tag, lo, hi)
    elif tag == MINMAX_BOOL:
        lo = bool(reader.read_u8())
        hi = bool(reader.read_u8())
        result = (tag, lo, hi)
    elif tag == MINMAX_STRING:
        lo_len = reader.read_u32()
        lo = reader.read(lo_len).decode("utf-8")
        hi_len = reader.read_u32()
        hi = reader.read(hi_len).decode("utf-8")
        result = (tag, lo, hi)
    else:
        raise CorruptFormatError(f"unknown minmax tag {tag}")
    if reader.pos - start != byte_count:
        raise CorruptFormatError("minmax block length mismatch")
    return result
