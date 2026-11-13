"""列块负载与块头的编码逻辑。"""

import math
import struct
import zlib

from ._bitmap import bitmap_byte_count, encode_bitmap
from ._constants import (
    ENCODING_DELTA,
    ENCODING_DICTIONARY,
    ENCODING_PLAIN,
    FLAG_HAS_MINMAX,
    FLAG_NULLABLE,
    MAX_PAYLOAD_BYTES,
)
from ._stats import build_minmax
from ._varint import encode_zigzag_varint
from .errors import CorruptFormatError
from .schema import Column, ColumnType


def _encode_int64_plain(values) -> bytes:
    return b"".join(struct.pack("<q", value) for value in values)


def _encode_int64_delta(values) -> bytes:
    """非空 int64 的 delta 编码。

    布局: first i64 | 后续每个 delta 的 zigzag varint。
    任何 delta 超出 int64 范围时返回 None, 由调用方回退到 plain。
    """
    if not values:
        return b""
    parts = [struct.pack("<q", values[0])]
    previous = values[0]
    for value in values[1:]:
        delta = value - previous
        if not -(1 << 63) <= delta <= (1 << 63) - 1:
            return None
        parts.append(encode_zigzag_varint(delta))
        previous = value
    return b"".join(parts)


def _encode_float64_plain(values) -> bytes:
    """float64 定长编码。

    所有 NaN 统一成 IEEE-754 canonical quiet NaN, 保证编码确定且可往返。
    """
    canonical_nan = struct.unpack("<Q", struct.pack("<d", float("nan")))[0]
    out = bytearray()
    for value in values:
        number = float(value)
        if math.isnan(number):
            out.extend(struct.pack("<Q", canonical_nan))
        else:
            out.extend(struct.pack("<d", number))
    return bytes(out)


def _encode_bool_plain(values) -> bytes:
    return encode_bitmap(values)


def _encode_string_plain(values) -> bytes:
    """字符串 plain: 依次 [u32 字节长度][UTF-8 字节]。"""
    parts = []
    for value in values:
        raw = value.encode("utf-8")
        parts.append(struct.pack("<I", len(raw)))
        parts.append(raw)
    return b"".join(parts)


def _encode_string_dictionary(values):
    """字典编码, 与插入顺序无关。

    字典条目按 UTF-8 字节序排序后分配下标, 因此同一批值永远产生
    相同的字典与下标序列。
    布局:
        entry_count u32
        每个条目: len u32 + UTF-8 字节
        每个值的下标: u32
    """
    unique = sorted({value.encode("utf-8") for value in values})
    if len(unique) > 0xFFFFFFFF:
        raise CorruptFormatError("dictionary has too many entries")
    index_of = {raw: index for index, raw in enumerate(unique)}
    parts = [struct.pack("<I", len(unique))]
    for raw in unique:
        parts.append(struct.pack("<I", len(raw)))
        parts.append(raw)
    for value in values:
        parts.append(struct.pack("<I", index_of[value.encode("utf-8")]))
    return b"".join(parts)


def _encode_payload(values, col_type: ColumnType):
    """返回 (encoding 码, payload 字节), 在可选编码间做确定性选择。"""
    if col_type is ColumnType.INT64:
        if not values:
            return ENCODING_PLAIN, b""
        plain = _encode_int64_plain(values)
        delta = _encode_int64_delta(values)
        if delta is not None and len(delta) <= len(plain):
            return ENCODING_DELTA, delta
        return ENCODING_PLAIN, plain
    if col_type is ColumnType.FLOAT64:
        return ENCODING_PLAIN, _encode_float64_plain(values)
    if col_type is ColumnType.BOOL:
        return ENCODING_PLAIN, _encode_bool_plain(values)
    if col_type is ColumnType.STRING:
        if not values:
            return ENCODING_PLAIN, b""
        plain = _encode_string_plain(values)
        dictionary = _encode_string_dictionary(values)
        if len(dictionary) <= len(plain):
            return ENCODING_DICTIONARY, dictionary
        return ENCODING_PLAIN, plain
    raise CorruptFormatError(f"unsupported column type {col_type}")


def encode_chunk(column: Column, values, nrows: int) -> bytes:
    """把一列编码成完整列块(块头 + 位图 + 负载 + 负载 CRC)。

    values 长度必须等于 nrows, 元素为 None 表示空值。
    """
    if len(values) != nrows:
        raise CorruptFormatError("column length does not match row count")

    present = [value for value in values if value is not None]
    null_count = nrows - len(present)

    flags = 0
    bitmap = b""
    if null_count:
        flags |= FLAG_NULLABLE
        bitmap = encode_bitmap([value is not None for value in values])

    encoding, payload = _encode_payload(present, column.type)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise CorruptFormatError("payload exceeds size limit")

    minmax = build_minmax(present, column.type)
    if minmax is not None:
        flags |= FLAG_HAS_MINMAX

    name_bytes = column.name.encode("utf-8")
    minmax_bytes = minmax or b""
    payload_crc = zlib.crc32(payload) & 0xFFFFFFFF

    header_without_total = b"".join(
        (
            struct.pack(
                "<IBBBBQII",
                len(name_bytes),
                column.type.code,
                encoding,
                flags,
                0,  # reserved
                null_count,
                len(payload),
                len(minmax_bytes),
            ),
            name_bytes,
            minmax_bytes,
        )
    )
    total_len = 4 + len(header_without_total) + 4 + len(bitmap) + len(payload) + 4
    if total_len > 0xFFFFFFFF:
        raise CorruptFormatError("chunk too large")

    header_body = struct.pack("<I", total_len) + header_without_total
    header_crc = zlib.crc32(header_body) & 0xFFFFFFFF
    header = header_body + struct.pack("<I", header_crc)

    chunk = b"".join(
        (
            header,
            bitmap,
            payload,
            struct.pack("<I", payload_crc),
        )
    )
    if len(chunk) != total_len:
        raise CorruptFormatError("chunk length bookkeeping error")
    return chunk


def expected_bitmap_bytes(flags: int, nrows: int) -> int:
    if flags & FLAG_NULLABLE:
        return bitmap_byte_count(nrows)
    return 0


__all__ = ["encode_chunk", "expected_bitmap_bytes"]
