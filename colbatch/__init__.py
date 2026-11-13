"""colbatch: 确定性的列式二进制批次编解码格式。

公开 API:
    Schema, Column, ColumnType
    encode_batch, decode_batch
    read_metadata
    ColumnMetadata, FileMetadata
    CorruptFormatError, TruncatedFormatError, ChecksumError

文件整体布局(所有多字节整数均为小端):
    +---------------------------------------------+
    | 文件头 (40 字节, 末尾 CRC32)                 |
    |   magic "CBF1" | version u16 | ...          |
    +---------------------------------------------+
    | 列块 0                                       |
    |   块头 (变长, 末尾 CRC32)                    |
    |   空值位图 (可选, 按 8 位对齐)               |
    |   负载 (长度在块头声明, 末尾 CRC32)          |
    +---------------------------------------------+
    | 列块 1 ...                                   |
    +---------------------------------------------+
    | 文件尾 "CBFE" + CRC32(前面全部字节)          |
    +---------------------------------------------+

设计目标:
    * 同一批数据始终编码成完全相同的字节(确定性), 字符串字典按 UTF-8
      字节序排序, 与插入顺序无关。
    * 所有长度/偏移都有上界校验, 截断文件只会抛出 TruncatedFormatError,
      不会发生越界读取。
    * 块头携带空值数量与可选最小/最大值, 读取元数据即可跳过无关块。
    * 每块负载、每个块头、文件头以及整个文件各有 CRC32 校验, 任何篡改
      都会被发现。
"""

from .errors import (
    ChecksumError,
    CorruptFormatError,
    TruncatedFormatError,
)
from .schema import Column, ColumnType, Schema
from .codec import (
    ColumnMetadata,
    FileMetadata,
    decode_batch,
    encode_batch,
    read_metadata,
)

__all__ = [
    "Column",
    "ColumnMetadata",
    "ColumnType",
    "ChecksumError",
    "CorruptFormatError",
    "FileMetadata",
    "Schema",
    "TruncatedFormatError",
    "decode_batch",
    "encode_batch",
    "read_metadata",
]

__version__ = "1.0.0"

FORMAT_VERSION = 1
