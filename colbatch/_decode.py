"""列块负载与块头的解码逻辑。

每个读取动作都经过 BoundsReader 边界检查; 长度、偏移、下标、值域
都会被验证, 截断或伪造的文件无法触发越界读取。
"""

import math
import struct
import zlib

from ._bitmap import count_ones, decode_bitmap
from ._constants import (
    CHUNK_HEADER_FIXED_SIZE,
    ENCODING_DELTA,
    ENCODING_DICTIONARY,
    ENCODING_PLAIN,
    FLAG_HAS_MINMAX,
    FLAG_NULLABLE,
    MINMAX_BOOL,
    MINMAX_FLOAT64,
    MINMAX_INT64,
    MINMAX_STRING,
    MAX_NAME_BYTES,
    MAX_PAYLOAD_BYTES,
)
from ._stats import parse_minmax
from ._varint import BoundsReader, zigzag_decode
from .errors import ChecksumError, CorruptFormatError, TruncatedFormatError
from .schema import Column, ColumnType


def _check_crc(data: bytes, expected: int, what: str) -> None:
    actual = zlib.crc32(data) & 0xFFFFFFFF
    if actual != expected:
        raise ChecksumError(f"{what} CRC mismatch: expected {expected:#x}, got {actual:#x}")


def _decode_int64_plain(reader: BoundsReader, count: int):
    return [reader.read_i64() for _ in range(count)]


def _decode_int64_delta(reader: BoundsReader, count: int):
    if count == 0:
        if reader.remaining:
            raise CorruptFormatError("trailing bytes in empty delta payload")
        return []
    values = [reader.read_i64()]
    previous = values[0]
    for _ in range(1, count):
        delta = zigzag_decode(reader.read_uvarint())
        if not -(1 << 63) <= delta <= (1 << 63) - 1:
            raise CorruptFormatError("delta out of int64 range")
        value = previous + delta
        if not -(1 << 63) <= value <= (1 << 63) - 1:
            raise CorruptFormatError("delta-decoded value out of int64 range")
        values.append(value)
        previous = value
    if reader.remaining:
        raise CorruptFormatError("trailing bytes in delta payload")
    return values


def _decode_float64_plain(reader: BoundsReader, count: int):
    values = []
    for _ in range(count):
        values.append(reader.read_f64())
    if reader.remaining:
        raise CorruptFormatError("trailing bytes in float payload")
    return values


def _decode_bool_plain(reader: BoundsReader, count: int):
    raw = reader.read((count + 7) // 8)
    if reader.remaining:
        raise CorruptFormatError("trailing bytes in bool payload")
    return [bool(bit) for bit in decode_bitmap(raw, count)]


def _read_utf8(reader: BoundsReader, length: int) -> str:
    raw = reader.read(length)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorruptFormatError("invalid UTF-8 in string payload") from exc


def _decode_string_plain(reader: BoundsReader, count: int):
    values = []
    for _ in range(count):
        length = reader.read_u32()
        if length > reader.remaining:
            raise TruncatedFormatError("string entry exceeds payload bounds")
        values.append(_read_utf8(reader, length))
    if reader.remaining:
        raise CorruptFormatError("trailing bytes in plain string payload")
    return values


def _decode_string_dictionary(reader: BoundsReader, count: int):
    entry_count = reader.read_u32()
    if entry_count > count and count >= 0:
        raise CorruptFormatError("dictionary has more entries than values")
    dictionary = []
    for _ in range(entry_count):
        length = reader.read_u32()
        if length > reader.remaining:
            raise TruncatedFormatError("dictionary entry exceeds payload bounds")
        dictionary.append(_read_utf8(reader, length))
    values = []
    for _ in range(count):
        index = reader.read_u32()
        if index >= entry_count:
            raise CorruptFormatError(f"dictionary index {index} out of range")
        values.append(dictionary[index])
    if reader.remaining:
        raise CorruptFormatError("trailing bytes in dictionary payload")
    return values


def _decode_payload(payload: bytes, encoding: int, col_type: ColumnType, count: int):
    reader = BoundsReader(payload)
    if col_type is ColumnType.INT64:
        if encoding == ENCODING_PLAIN:
            values = _decode_int64_plain(reader, count)
        elif encoding == ENCODING_DELTA:
            values = _decode_int64_delta(reader, count)
        else:
            raise CorruptFormatError(f"int64 cannot use encoding {encoding}")
    elif col_type is ColumnType.FLOAT64:
        if encoding != ENCODING_PLAIN:
            raise CorruptFormatError(f"float64 cannot use encoding {encoding}")
        values = _decode_float64_plain(reader, count)
    elif col_type is ColumnType.BOOL:
        if encoding != ENCODING_PLAIN:
            raise CorruptFormatError(f"bool cannot use encoding {encoding}")
        values = _decode_bool_plain(reader, count)
    elif col_type is ColumnType.STRING:
        if encoding == ENCODING_PLAIN:
            values = _decode_string_plain(reader, count)
        elif encoding == ENCODING_DICTIONARY:
            values = _decode_string_dictionary(reader, count)
        else:
            raise CorruptFormatError(f"string cannot use encoding {encoding}")
    else:
        raise CorruptFormatError(f"unknown column type {col_type}")
    if reader.remaining:
        raise CorruptFormatError("payload was not fully consumed")
    return values


def _interleave_nulls(present, validity, nrows: int):
    """按空值位图把非空值序列还原成带 None 的完整列。"""
    if len(validity) != nrows:
        raise CorruptFormatError("validity bitmap length mismatch")
    if len(present) != sum(validity):
        raise CorruptFormatError("present value count does not match validity bitmap")
    result = []
    cursor = 0
    for is_valid in validity:
        if is_valid:
            result.append(present[cursor])
            cursor += 1
        else:
            result.append(None)
    return result


def _verify_minmax(present, col_type: ColumnType, minmax) -> None:
    """交叉校验块头统计与真实负载一致, 防止 CRC 自洽但统计说谎。"""
    # float64 列只要含 NaN, 编码端就按规范省略 minmax。
    if col_type is ColumnType.FLOAT64 and any(
        isinstance(value, float) and math.isnan(value) for value in present
    ):
        if minmax is not None:
            raise CorruptFormatError("minmax must be omitted when NaN is present")
        return
    if minmax is None:
        if present:
            raise CorruptFormatError("minmax missing for non-empty column")
        return
    tag, lo, hi = minmax
    if not present:
        raise CorruptFormatError("minmax present for fully-null column")
    if col_type is ColumnType.INT64:
        if tag != MINMAX_INT64 or (lo, hi) != (min(present), max(present)):
            raise CorruptFormatError("int64 minmax does not match payload")
    elif col_type is ColumnType.FLOAT64:
        if tag != MINMAX_FLOAT64 or (lo, hi) != (min(present), max(present)):
            raise CorruptFormatError("float64 minmax does not match payload")
    elif col_type is ColumnType.BOOL:
        if tag != MINMAX_BOOL or (lo, hi) != (min(present), max(present)):
            raise CorruptFormatError("bool minmax does not match payload")
    elif col_type is ColumnType.STRING:
        encoded = [v.encode("utf-8") for v in present]
        if tag != MINMAX_STRING or (lo, hi) != (
            min(encoded).decode("utf-8"),
            max(encoded).decode("utf-8"),
        ):
            raise CorruptFormatError("string minmax does not match payload")


def decode_chunk(buf: bytes, offset: int, nrows: int):
    """解码 offset 处的一个列块。

    返回 (Column, 完整列值 list, ColumnMetadata 所需字典, 下一 offset)。
    """
    if offset + CHUNK_HEADER_FIXED_SIZE > len(buf):
        raise TruncatedFormatError("chunk header truncated")

    total_len = struct.unpack_from("<I", buf, offset)[0]
    min_total = CHUNK_HEADER_FIXED_SIZE + 4 + 4
    if total_len < min_total:
        raise CorruptFormatError(f"chunk total_len {total_len} below minimum")
    if offset + total_len > len(buf):
        raise TruncatedFormatError("chunk body truncated")

    (
        total_len_field,
        name_len,
        type_code,
        encoding,
        flags,
        reserved,
        null_count,
        payload_len,
        minmax_len,
    ) = struct.unpack_from("<IIBBBBQII", buf, offset)
    if total_len_field != total_len:
        raise CorruptFormatError("chunk total_len field inconsistent")

    if reserved != 0:
        raise CorruptFormatError("non-zero reserved byte in chunk header")
    if flags & 0xFC:
        raise CorruptFormatError(f"unknown chunk flags {flags:#x}")
    if name_len == 0 or name_len > MAX_NAME_BYTES:
        raise CorruptFormatError("invalid column name length")
    if payload_len > MAX_PAYLOAD_BYTES:
        raise CorruptFormatError("payload length exceeds limit")
    if null_count > nrows:
        raise CorruptFormatError("null_count exceeds row count")

    header_body_end = offset + CHUNK_HEADER_FIXED_SIZE + name_len + minmax_len
    header_crc_end = header_body_end + 4
    header_crc = struct.unpack_from("<I", buf, header_body_end)[0]
    _check_crc(buf[offset:header_body_end], header_crc, "chunk header")

    header_reader = BoundsReader(buf, offset + CHUNK_HEADER_FIXED_SIZE, header_body_end)
    name = _read_utf8(header_reader, name_len)
    minmax = parse_minmax(header_reader, minmax_len)

    col_type = ColumnType.from_code(type_code)

    bitmap_bytes = (nrows + 7) // 8 if flags & FLAG_NULLABLE else 0
    payload_start = header_crc_end + bitmap_bytes
    payload_end = payload_start + payload_len
    expected_end = payload_end + 4
    if expected_end != offset + total_len:
        raise CorruptFormatError(
            "chunk layout lengths do not add up to total_len"
        )

    payload_crc = struct.unpack_from("<I", buf, payload_end)[0]
    payload = buf[payload_start:payload_end]
    _check_crc(payload, payload_crc, "payload")

    if flags & FLAG_NULLABLE:
        bitmap = buf[header_crc_end:payload_start]
        validity = decode_bitmap(bitmap, nrows)
        actual_nulls = nrows - count_ones(bitmap, nrows)
        if actual_nulls != null_count:
            raise CorruptFormatError("null_count does not match validity bitmap")
        present_count = nrows - null_count
    else:
        if null_count != 0:
            raise CorruptFormatError("null_count set without nullable flag")
        validity = [1] * nrows
        present_count = nrows

    present = _decode_payload(payload, encoding, col_type, present_count)
    values = _interleave_nulls(present, validity, nrows)

    has_minmax = bool(flags & FLAG_HAS_MINMAX)
    if has_minmax != (minmax is not None):
        raise CorruptFormatError("minmax flag/block inconsistency")
    _verify_minmax(present, col_type, minmax if has_minmax else None)

    meta = {
        "name": name,
        "type": col_type,
        "encoding": encoding,
        "null_count": null_count,
        "minmax": minmax,
        "payload_len": payload_len,
        "chunk_len": total_len,
    }
    return Column(name, col_type), values, meta, expected_end
