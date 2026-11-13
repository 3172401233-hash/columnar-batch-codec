"""空值/布尔值位图工具。

位图按小端位序打包: 第 i 行存放在第 (i // 8) 字节的第 (i % 8) 位。
位图字节数 = ceil(nrows / 8), 尾部填充位必须为 0。
"""

from .errors import CorruptFormatError


def bitmap_byte_count(bit_count: int) -> int:
    return (bit_count + 7) // 8


def encode_bitmap(bits) -> bytes:
    """把布尔序列打包成位图, 并保证填充位为 0。"""
    count = len(bits)
    out = bytearray(bitmap_byte_count(count))
    for index, bit in enumerate(bits):
        if bit:
            out[index >> 3] |= 1 << (index & 7)
    return bytes(out)


def decode_bitmap(buf: bytes, count: int):
    """解包位图; 校验长度与尾部填充位。"""
    expected = bitmap_byte_count(count)
    if len(buf) != expected:
        raise CorruptFormatError(
            f"bitmap length {len(buf)} != expected {expected}"
        )
    if count & 7:
        padding_mask = 0xFF << (count & 7)
        if buf[-1] & padding_mask:
            raise CorruptFormatError("non-zero padding bits in bitmap")
    return [(buf[index >> 3] >> (index & 7)) & 1 for index in range(count)]


def count_ones(buf: bytes, count: int) -> int:
    """统计前 count 个位中的 1 个数, 填充位不计入。"""
    total = 0
    full_bytes, remainder = divmod(count, 8)
    for byte in buf[:full_bytes]:
        total += byte.bit_count()
    if remainder and full_bytes < len(buf):
        last = buf[full_bytes] & ((1 << remainder) - 1)
        total += last.bit_count()
    return total
