"""ZigZag + 无符号 varint 工具, 用于 int64 delta 编码。"""

from ._constants import VARINT_LAST_BYTE_MAX, VARINT_MAX_BYTES
from .errors import CorruptFormatError, TruncatedFormatError


def zigzag_encode(value: int) -> int:
    """有符号 64 位整数映射为非负整数。"""
    if not -(1 << 63) <= value <= (1 << 63) - 1:
        raise OverflowError("value out of int64 range")
    if value >= 0:
        return value << 1
    return ((-value) << 1) - 1


def zigzag_decode(value: int) -> int:
    """zigzag_encode 的逆运算, 返回有符号整数。"""
    return (value >> 1) ^ -(value & 1)


def encode_uvarint(value: int) -> bytes:
    """把非负整数编码成最小长度的无符号 varint。"""
    if value < 0:
        raise OverflowError("uvarint cannot encode negative values")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def uvarint_size(value: int) -> int:
    """encode_uvarint 输出的字节数。"""
    if value < 0:
        raise OverflowError("uvarint cannot encode negative values")
    size = 1
    while value >= 0x80:
        value >>= 7
        size += 1
    return size


def encode_zigzag_varint(value: int) -> bytes:
    return encode_uvarint(zigzag_encode(value))


class BoundsReader:
    """对 bytes 的只读游标, 任何越界读取都抛出 TruncatedFormatError。

    所有定长整数为小端序。varint 严格限制在 64 位范围内。
    """

    __slots__ = ("_buf", "_pos", "_end")

    def __init__(self, buf: bytes, start: int = 0, end: int | None = None) -> None:
        self._buf = buf
        self._pos = start
        self._end = len(buf) if end is None else end
        if not (0 <= self._pos <= self._end <= len(buf)):
            raise CorruptFormatError("reader range out of buffer bounds")

    @property
    def pos(self) -> int:
        return self._pos

    @property
    def remaining(self) -> int:
        return self._end - self._pos

    def read(self, size: int) -> bytes:
        if size < 0:
            raise CorruptFormatError("negative read size")
        new_pos = self._pos + size
        if new_pos > self._end:
            raise TruncatedFormatError(
                f"need {size} bytes at offset {self._pos}, "
                f"only {self._end - self._pos} available"
            )
        chunk = self._buf[self._pos:new_pos]
        self._pos = new_pos
        return chunk

    def read_u8(self) -> int:
        return self.read(1)[0]

    def read_u16(self) -> int:
        return int.from_bytes(self.read(2), "little")

    def read_u32(self) -> int:
        return int.from_bytes(self.read(4), "little")

    def read_u64(self) -> int:
        return int.from_bytes(self.read(8), "little")

    def read_i64(self) -> int:
        return int.from_bytes(self.read(8), "little", signed=True)

    def read_f64(self) -> float:
        import struct

        return struct.unpack("<d", self.read(8))[0]

    def read_uvarint(self) -> int:
        result = 0
        shift = 0
        for index in range(VARINT_MAX_BYTES):
            byte = self.read_u8()
            if index == VARINT_MAX_BYTES - 1:
                if byte & 0x80 or byte > VARINT_LAST_BYTE_MAX:
                    raise CorruptFormatError("varint exceeds uint64 range")
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
            shift += 7
        raise CorruptFormatError("varint exceeds uint64 range")

    def sub_reader(self, size: int) -> "BoundsReader":
        start = self._pos
        self.read(size)
        return BoundsReader(self._buf, start, start + size)

