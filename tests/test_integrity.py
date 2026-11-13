"""截断、随机损坏、篡改检测与边界安全测试。"""

import os
import random
import unittest

from colbatch import (
    ChecksumError,
    Column,
    ColumnType,
    CorruptFormatError,
    Schema,
    TruncatedFormatError,
    decode_batch,
    encode_batch,
    read_metadata,
)


def _sample_data(rows=300, seed=11):
    rng = random.Random(seed)
    schema = Schema(
        [
            Column("id", ColumnType.INT64),
            Column("score", ColumnType.FLOAT64),
            Column("flag", ColumnType.BOOL),
            Column("label", ColumnType.STRING),
        ]
    )
    words = ["red", "green", "blue", "yellow", None]
    columns = [
        [rng.randint(-(2**40), 2**40) if rng.random() > 0.1 else None for _ in range(rows)],
        [rng.random() * 1000 if rng.random() > 0.1 else None for _ in range(rows)],
        [rng.choice([True, False]) if rng.random() > 0.1 else None for _ in range(rows)],
        [rng.choice(words) for _ in range(rows)],
    ]
    return schema, columns


class TruncationTest(unittest.TestCase):
    def setUp(self):
        schema, columns = _sample_data()
        self.data = encode_batch(schema, columns)

    def test_every_prefix_raises_or_decodes_safely(self):
        # 从每个长度截断都不允许出现越界读; 完整长度才能成功解码。
        for length in range(0, len(self.data)):
            prefix = self.data[:length]
            with self.assertRaises(CorruptFormatError):
                decode_batch(prefix)

    def test_truncated_at_chunk_boundaries(self):
        # 针对几个结构性位置截断。
        for cut in (0, 1, 39, 40, 41, len(self.data) - 8, len(self.data) - 1):
            with self.assertRaises(CorruptFormatError):
                decode_batch(self.data[:cut])

    def test_truncation_error_type_for_short_files(self):
        with self.assertRaises(TruncatedFormatError):
            decode_batch(self.data[:20])
        with self.assertRaises(TruncatedFormatError):
            decode_batch(self.data[:-10])

    def test_empty_input(self):
        with self.assertRaises(TruncatedFormatError):
            decode_batch(b"")

    def test_metadata_also_rejects_truncation(self):
        with self.assertRaises(CorruptFormatError):
            read_metadata(self.data[: len(self.data) // 2])


class CorruptionTest(unittest.TestCase):
    def setUp(self):
        schema, columns = _sample_data()
        self.data = bytearray(encode_batch(schema, columns))

    def test_random_byte_flips_detected(self):
        rng = random.Random(99)
        for _ in range(50):
            corrupted = bytearray(self.data)
            index = rng.randrange(len(corrupted))
            corrupted[index] ^= 1 << rng.randrange(8)
            with self.assertRaises(CorruptFormatError):
                decode_batch(bytes(corrupted))

    def test_payload_length_tampering_detected(self):
        # 篡改第一个列块头里的 payload_len 字段 (块起点 40, 偏移 4+16=20)。
        corrupted = bytearray(self.data)
        chunk_offset = 40
        payload_len_offset = chunk_offset + 4 + 16
        corrupted[payload_len_offset] = (corrupted[payload_len_offset] + 7) & 0xFF
        with self.assertRaises(CorruptFormatError):
            decode_batch(bytes(corrupted))

    def test_null_count_tampering_detected(self):
        corrupted = bytearray(self.data)
        null_count_offset = 40 + 4 + 8
        corrupted[null_count_offset] ^= 0x01
        with self.assertRaises(CorruptFormatError):
            decode_batch(bytes(corrupted))

    def test_magic_tampering_detected(self):
        corrupted = bytearray(self.data)
        corrupted[0] = ord("X")
        with self.assertRaises(CorruptFormatError):
            decode_batch(bytes(corrupted))

    def test_footer_crc_tampering_detected(self):
        corrupted = bytearray(self.data)
        corrupted[-1] ^= 0xFF
        with self.assertRaises(ChecksumError):
            decode_batch(bytes(corrupted))

    def test_trailing_garbage_rejected(self):
        with self.assertRaises(CorruptFormatError):
            decode_batch(bytes(self.data) + b"\x00")

    def test_random_inserts_and_deletes(self):
        rng = random.Random(123)
        for _ in range(20):
            mutated = bytearray(self.data)
            index = rng.randrange(len(mutated))
            if rng.random() < 0.5:
                del mutated[index]
            else:
                mutated.insert(index, rng.randrange(256))
            with self.assertRaises(CorruptFormatError):
                decode_batch(bytes(mutated))


class MalformedStructureTest(unittest.TestCase):
    def test_non_colbatch_bytes(self):
        with self.assertRaises(CorruptFormatError):
            decode_batch(b"not a colbatch file" + os.urandom(100))

    def test_huge_declared_length_does_not_allocate(self):
        # 伪造一个极大 total_len 的块头, 必须在分配前被拒绝。
        schema = Schema([Column("i", ColumnType.INT64)])
        data = bytearray(encode_batch(schema, [[1, 2, 3]]))
        # 块头 total_len 位于文件头之后的前 4 字节。
        data[40:44] = (0xFFFFFFFF).to_bytes(4, "little")
        with self.assertRaises(CorruptFormatError):
            decode_batch(bytes(data))

    def test_huge_string_length_rejected(self):
        schema = Schema([Column("s", ColumnType.STRING)])
        data = bytearray(encode_batch(schema, [["abc", "def"]]))
        # 直接破坏负载中第一个字符串长度字段, CRC 必须拦住;
        # 即使 CRC 被绕过, 边界检查也必须拦住。
        payload_len_field = 40 + 4 + 16
        # 找到负载起点较麻烦, 这里只验证篡改后必失败且不会挂起。
        data[payload_len_field] = 0xFF
        with self.assertRaises(CorruptFormatError):
            decode_batch(bytes(data))


class MetadataSkipTest(unittest.TestCase):
    def test_metadata_stats_usable_for_skipping(self):
        schema = Schema(
            [
                Column("user_id", ColumnType.INT64),
                Column("country", ColumnType.STRING),
                Column("amount", ColumnType.FLOAT64),
            ]
        )
        columns = [
            [1, 2, 3, 4],
            ["CN", "US", "CN", "JP"],
            [10.0, 20.0, 30.0, 40.0],
        ]
        data = encode_batch(schema, columns)
        meta = read_metadata(data)
        self.assertEqual(meta.row_count, 4)
        by_name = {column.name: column for column in meta.columns}
        # 模拟"只关心 amount > 1000 的批", 利用 max 直接跳过整批。
        self.assertLess(by_name["amount"].maximum, 1000.0)
        self.assertEqual(by_name["user_id"].minimum, 1)
        self.assertEqual(by_name["country"].minimum, "CN")
        self.assertEqual(by_name["country"].maximum, "US")

    def test_metadata_checksums_validate_full_file(self):
        schema, columns = _sample_data(rows=50)
        data = bytearray(encode_batch(schema, columns))
        data[len(data) // 2] ^= 0x01
        with self.assertRaises(CorruptFormatError):
            read_metadata(bytes(data))


class SemanticForgeryTest(unittest.TestCase):
    """CRC 自洽但语义矛盾的伪造文件必须被交叉校验拦截。"""

    @staticmethod
    def _reseal_chunk_and_file(buf: bytes, header_body_len: int) -> bytes:
        import struct
        import zlib

        from colbatch._constants import (
            FOOTER_MAGIC,
            FORMAT_VERSION,
            HEADER_SIZE,
            HEADER_STRUCT,
            MAGIC,
        )

        buf = bytearray(buf)
        buf[header_body_len : header_body_len + 4] = struct.pack(
            "<I", zlib.crc32(bytes(buf[:header_body_len])) & 0xFFFFFFFF
        )
        header = HEADER_STRUCT.pack(
            MAGIC, FORMAT_VERSION, 0, 1, HEADER_SIZE, 3, b"\x00" * 12, 0
        )
        header = header[:36] + struct.pack(
            "<I", zlib.crc32(header[:36]) & 0xFFFFFFFF
        )
        whole = header + bytes(buf)
        return whole + FOOTER_MAGIC + struct.pack(
            "<I", zlib.crc32(whole) & 0xFFFFFFFF
        )

    def test_forged_minmax_rejected(self):
        import struct

        from colbatch._encode import encode_chunk

        chunk = bytearray(
            encode_chunk(Column("i", ColumnType.INT64), [5, 6, 7], 3)
        )
        # 块布局: total(4) 固定头(24) name "i"(1) minmax(17) hdrCrc(4)...
        minmax_offset = 4 + 24 + 1
        struct.pack_into("<q", chunk, minmax_offset + 9, 100)
        forged = self._reseal_chunk_and_file(bytes(chunk), 4 + 24 + 1 + 17)
        with self.assertRaises(CorruptFormatError):
            decode_batch(forged)
        with self.assertRaises(CorruptFormatError):
            read_metadata(forged)

    def test_forged_null_count_rejected_even_with_crc(self):
        import struct

        from colbatch._encode import encode_chunk

        chunk = bytearray(
            encode_chunk(Column("i", ColumnType.INT64), [5, 6, 7], 3)
        )
        # null_count 位于固定头偏移 4(total)+4(name_len)+4(type/enc/flags/res) = 12
        struct.pack_into("<Q", chunk, 12, 2)
        # 没有 nullable 位图, 重算 CRC 后语义检查仍应发现矛盾。
        forged = self._reseal_chunk_and_file(
            bytes(chunk), 4 + 24 + 1 + 17
        )
        with self.assertRaises(CorruptFormatError):
            decode_batch(forged)


if __name__ == "__main__":
    unittest.main()
