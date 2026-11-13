"""直接针对负载解码器的畸形输入测试(绕过外层 CRC 组装)。"""

import struct
import unittest

from colbatch._constants import ENCODING_DELTA, ENCODING_DICTIONARY, ENCODING_PLAIN
from colbatch._decode import _decode_payload
from colbatch._varint import BoundsReader, encode_uvarint, zigzag_encode
from colbatch.errors import CorruptFormatError
from colbatch.schema import ColumnType


class PayloadDecodeTest(unittest.TestCase):
    def test_dictionary_index_out_of_range(self):
        payload = b"".join(
            [
                struct.pack("<I", 2),
                struct.pack("<I", 1), b"a",
                struct.pack("<I", 1), b"b",
                struct.pack("<I", 5),  # 下标 5 >= 条目数 2
            ]
        )
        with self.assertRaises(CorruptFormatError):
            _decode_payload(payload, ENCODING_DICTIONARY, ColumnType.STRING, 1)

    def test_dictionary_more_entries_than_values(self):
        payload = b"".join(
            [
                struct.pack("<I", 3),
                struct.pack("<I", 1), b"a",
                struct.pack("<I", 1), b"b",
                struct.pack("<I", 1), b"c",
            ]
        )
        with self.assertRaises(CorruptFormatError):
            _decode_payload(payload, ENCODING_DICTIONARY, ColumnType.STRING, 1)

    def test_plain_string_length_beyond_payload(self):
        payload = struct.pack("<I", 100) + b"short"
        with self.assertRaises(CorruptFormatError):
            _decode_payload(payload, ENCODING_PLAIN, ColumnType.STRING, 1)

    def test_trailing_bytes_rejected(self):
        payload = struct.pack("<q", 1) + b"\x00"
        with self.assertRaises(CorruptFormatError):
            _decode_payload(payload, ENCODING_PLAIN, ColumnType.INT64, 1)

    def test_delta_overflow_value_rejected(self):
        # first = INT64_MIN, delta = -1 会导致下溢。
        payload = struct.pack("<q", -(2**63)) + encode_uvarint(zigzag_encode(-1))
        with self.assertRaises(CorruptFormatError):
            _decode_payload(payload, ENCODING_DELTA, ColumnType.INT64, 2)

    def test_varint_max_bytes_enforced(self):
        reader = BoundsReader(bytes([0x80] * 10 + [0x00]))
        with self.assertRaises(CorruptFormatError):
            reader.read_uvarint()

    def test_bool_bitmap_padding_rejected(self):
        # 3 个布尔值却把第 4 个填充位写成 1。
        payload = bytes([0b00001011])
        with self.assertRaises(CorruptFormatError):
            _decode_payload(payload, ENCODING_PLAIN, ColumnType.BOOL, 3)

    def test_unknown_encoding_rejected(self):
        with self.assertRaises(CorruptFormatError):
            _decode_payload(b"", 99, ColumnType.INT64, 0)


if __name__ == "__main__":
    unittest.main()
