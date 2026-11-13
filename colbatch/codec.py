"""colbatch 顶层编解码 API。"""

import struct
import zlib
from dataclasses import dataclass

from ._constants import (
    ENCODING_DELTA,
    ENCODING_DICTIONARY,
    ENCODING_PLAIN,
    FOOTER_MAGIC,
    FOOTER_SIZE,
    FORMAT_VERSION,
    HEADER_CRC_OFFSET,
    HEADER_SIZE,
    HEADER_STRUCT,
    MAGIC,
    MAX_BATCH_ROWS,
)
from ._decode import _check_crc, decode_chunk
from ._encode import encode_chunk
from .errors import ChecksumError, CorruptFormatError, TruncatedFormatError
from .schema import Column, ColumnType, Schema, validate_value


@dataclass(frozen=True)
class ColumnMetadata:
    """列块头中可用于跳过无关块的统计信息。"""

    name: str
    type: ColumnType
    encoding: str
    null_count: int
    minimum: object
    maximum: object
    payload_bytes: int
    chunk_bytes: int


@dataclass(frozen=True)
class FileMetadata:
    """文件头与各列块头的只读视图。"""

    version: int
    row_count: int
    columns: tuple


_ENCODING_NAMES = {
    ENCODING_PLAIN: "plain",
    ENCODING_DELTA: "delta",
    ENCODING_DICTIONARY: "dictionary",
}


def _minmax_values(meta_minmax):
    if meta_minmax is None:
        return None, None
    return meta_minmax[1], meta_minmax[2]


def encode_batch(schema: Schema, columns) -> bytes:
    """把一批列数据编码成确定性的 colbatch 字节串。

    schema: 描述列名与类型的 Schema。
    columns: 与 schema 等长的序列, 每个元素是长度相同的值列表,
             元素为 None 表示该位置为空。
    """
    if not isinstance(schema, Schema):
        raise TypeError("schema must be a colbatch.Schema")
    columns = list(columns)
    if len(columns) != len(schema):
        raise ValueError("number of value columns does not match schema")

    nrows = len(columns[0])
    for index, values in enumerate(columns):
        if len(values) != nrows:
            raise ValueError(f"column {index} length differs from batch row count")
        if nrows > MAX_BATCH_ROWS:
            raise ValueError("batch too large")

    for column, values in zip(schema.columns, columns):
        for value in values:
            if value is not None:
                validate_value(value, column.type)

    header_body = HEADER_STRUCT.pack(
        MAGIC,
        FORMAT_VERSION,
        0,  # flags
        len(schema),
        HEADER_SIZE,  # 头长度字段, 固定值, 读取时交叉校验
        nrows,
        b"\x00" * 12,  # reserved
        0,  # header CRC 占位
    )
    header_crc = zlib.crc32(header_body[:HEADER_CRC_OFFSET]) & 0xFFFFFFFF
    header = header_body[:HEADER_CRC_OFFSET] + struct.pack("<I", header_crc)

    parts = [header]
    for column, values in zip(schema.columns, columns):
        parts.append(encode_chunk(column, list(values), nrows))

    body = b"".join(parts)
    whole_crc = zlib.crc32(body) & 0xFFFFFFFF
    return body + FOOTER_MAGIC + struct.pack("<I", whole_crc)


def _read_header(buf: bytes):
    if len(buf) < HEADER_SIZE:
        raise TruncatedFormatError("file shorter than fixed header")
    magic, version, flags, ncols, header_len, nrows, reserved, header_crc = (
        HEADER_STRUCT.unpack_from(buf, 0)
    )
    if magic != MAGIC:
        raise CorruptFormatError("bad magic: not a colbatch file")
    _check_crc(buf[:HEADER_CRC_OFFSET], header_crc, "file header")
    if version != FORMAT_VERSION:
        raise CorruptFormatError(f"unsupported format version {version}")
    if flags != 0:
        raise CorruptFormatError(f"unknown header flags {flags:#x}")
    if header_len != HEADER_SIZE:
        raise CorruptFormatError("header length field mismatch")
    if reserved != b"\x00" * 12:
        raise CorruptFormatError("non-zero reserved header bytes")
    if ncols == 0:
        raise CorruptFormatError("file must contain at least one column")
    if nrows > MAX_BATCH_ROWS:
        raise CorruptFormatError("row count exceeds limit")
    return version, ncols, nrows


def _verify_footer(buf: bytes) -> None:
    if len(buf) < HEADER_SIZE + FOOTER_SIZE:
        raise TruncatedFormatError("file too short to contain a footer")
    footer_magic = buf[-FOOTER_SIZE:-4]
    if footer_magic != FOOTER_MAGIC:
        raise TruncatedFormatError("footer marker missing or shifted")
    expected = struct.unpack_from("<I", buf, len(buf) - 4)[0]
    actual = zlib.crc32(buf[: -FOOTER_SIZE]) & 0xFFFFFFFF
    if actual != expected:
        raise ChecksumError("whole-file CRC mismatch: data tampered or corrupted")


def _iter_chunks(buf: bytes, ncols: int, nrows: int, decode_values: bool):
    offset = HEADER_SIZE
    decoded_columns = []
    metadatas = []
    schema_columns = []
    for index in range(ncols):
        column, values, meta, next_offset = decode_chunk(buf, offset, nrows)
        if index and column.name in {existing.name for existing in schema_columns}:
            raise CorruptFormatError("duplicate column name in file")
        schema_columns.append(column)
        if decode_values:
            decoded_columns.append(values)
        minimum, maximum = _minmax_values(meta["minmax"])
        encoding_name = _ENCODING_NAMES.get(meta["encoding"])
        if encoding_name is None:
            raise CorruptFormatError(f"unknown encoding code {meta['encoding']}")
        metadatas.append(
            ColumnMetadata(
                name=meta["name"],
                type=meta["type"],
                encoding=encoding_name,
                null_count=meta["null_count"],
                minimum=minimum,
                maximum=maximum,
                payload_bytes=meta["payload_len"],
                chunk_bytes=meta["chunk_len"],
            )
        )
        offset = next_offset

    if offset != len(buf) - FOOTER_SIZE:
        raise CorruptFormatError("trailing bytes after last column chunk")
    return schema_columns, decoded_columns, tuple(metadatas)


def read_metadata(data: bytes) -> FileMetadata:
    """只解析文件头与列块头并完成全部校验, 不还原数据值。

    适用于先看行数、空值数量、最小/最大值再决定是否跳过整批数据。
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    buf = bytes(data)
    _verify_footer(buf)
    version, ncols, nrows = _read_header(buf)
    _, _, metadatas = _iter_chunks(buf, ncols, nrows, decode_values=False)
    return FileMetadata(version=version, row_count=nrows, columns=metadatas)


def decode_batch(data: bytes):
    """解码 colbatch 字节串, 返回 (Schema, 按列组织的值列表, FileMetadata)。"""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    buf = bytes(data)
    _verify_footer(buf)
    version, ncols, nrows = _read_header(buf)
    schema_columns, decoded_columns, metadatas = _iter_chunks(
        buf, ncols, nrows, decode_values=True
    )
    schema = Schema(tuple(schema_columns))
    metadata = FileMetadata(version=version, row_count=nrows, columns=metadatas)
    return schema, decoded_columns, metadata
