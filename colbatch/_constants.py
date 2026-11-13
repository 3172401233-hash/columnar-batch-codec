"""格式常量与字节布局定义。

所有多字节整数使用小端序。字符串均为合法 UTF-8。
"""

import struct

FORMAT_VERSION = 1

# 魔数与文件尾标记
MAGIC = b"CBF1"
FOOTER_MAGIC = b"CBFE"

# 文件头:
#   magic(4) version(2) flags(2) ncols(4) header_len(4)
#   nrows(8) reserved(12) crc(4)
HEADER_SIZE = 40
HEADER_CRC_OFFSET = 36
HEADER_STRUCT = struct.Struct("<4sHHIIQ12sI")

# 文件尾: magic(4) + 全文件 CRC32(4)
FOOTER_SIZE = 8

# 列块块头:
#   total_len u32 | name_len u32 | type u8 | encoding u8
#   | flags u8 | reserved u8 | null_count u64 | payload_len u32
#   | minmax_len u32 | name ... | minmax ... | crc32 u32
# "<IIBBBBQII": total_len(4) name_len(4) type(1) encoding(1) flags(1)
# reserved(1) null_count(8) payload_len(4) minmax_len(4) = 28 字节
CHUNK_HEADER_FIXED_SIZE = 28
CHUNK_HEADER_CRC_TRAILER = 4
CHUNK_MIN_TOTAL_SIZE = (
    CHUNK_HEADER_FIXED_SIZE
    + CHUNK_HEADER_CRC_TRAILER
    + 4  # 负载 CRC
)

# 块头 flags 位
FLAG_NULLABLE = 0x01
FLAG_HAS_MINMAX = 0x02

# 类型码
TYPE_INT64 = 1
TYPE_FLOAT64 = 2
TYPE_BOOL = 3
TYPE_STRING = 4

TYPE_CODES = {
    TYPE_INT64,
    TYPE_FLOAT64,
    TYPE_BOOL,
    TYPE_STRING,
}

# 编码码
ENCODING_PLAIN = 0
ENCODING_DELTA = 1
ENCODING_DICTIONARY = 2

# 最小/最大值标签
MINMAX_INT64 = 1
MINMAX_FLOAT64 = 2
MINMAX_BOOL = 3
MINMAX_STRING = 4

# 尺寸上限, 防止截断/伪造长度导致巨量内存分配
MAX_NAME_BYTES = 1 << 20  # 1 MiB
MAX_PAYLOAD_BYTES = 1 << 34  # 16 GiB
MAX_BATCH_ROWS = 1 << 40

# 64 位 varint 最多 10 字节; 第 10 字节只允许最低位为 1
VARINT_MAX_BYTES = 10
VARINT_LAST_BYTE_MAX = 0x01
