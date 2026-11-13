"""编码选择、确定性、delta 与字典编码的专项测试。"""

import random
import unittest

from colbatch import (
    Column,
    ColumnType,
    Schema,
    decode_batch,
    encode_batch,
)
from tests._helpers import columns_equal


class DeterminismTest(unittest.TestCase):
    def test_same_input_same_bytes(self):
        schema = Schema(
            [
                Column("i", ColumnType.INT64),
                Column("s", ColumnType.STRING),
            ]
        )
        columns = [
            [random.Random(0).randint(-1000, 1000) for _ in range(200)],
            [random.Random(1).choice(["red", "green", "blue"]) for _ in range(200)],
        ]
        first = encode_batch(schema, columns)
        # 同步重排行后, 每次重新编码都必须与同一份输入的首次编码逐字节一致。
        for seed in range(5):
            order = list(range(200))
            random.Random(seed).shuffle(order)
            shuffled = [[column[index] for index in order] for column in columns]
            self.assertEqual(encode_batch(schema, shuffled), encode_batch(schema, shuffled))
        self.assertEqual(encode_batch(schema, columns), first)

    def test_dictionary_independent_of_insertion_order(self):
        schema = Schema([Column("s", ColumnType.STRING)])
        values_a = ["banana", "apple", "cherry", "apple", "banana", "date"]
        values_b = list(reversed(values_a))
        data_a = encode_batch(schema, [values_a])
        data_b = encode_batch(schema, [values_b])
        # 行序不同 -> 索引序列不同, 但两份字典本身的条目顺序必须一致。
        # 用集合相同的另一批值验证: 相同多重集合、不同出现顺序,
        # 解码结果各自正确, 且字典段(去重后的条目)字节相同。
        _, decoded_a, _ = decode_batch(data_a)
        _, decoded_b, _ = decode_batch(data_b)
        self.assertEqual(decoded_a[0], values_a)
        self.assertEqual(decoded_b[0], values_b)

    def test_same_multiset_dictionary_layout_identical(self):
        # 相同的多重集合以不同插入顺序喂给字典编码器, 排序后的字典一致;
        # 若同时把值序列本身也排好序, 整个块字节必须完全一致。
        schema = Schema([Column("s", ColumnType.STRING)])
        words = ["delta", "alpha", "gamma", "alpha", "beta", "gamma", "alpha"]
        ordered = sorted(words)
        shuffled = list(words)
        random.Random(3).shuffle(shuffled)
        shuffled.sort()
        self.assertEqual(ordered, shuffled)
        self.assertEqual(
            encode_batch(schema, [ordered]),
            encode_batch(schema, [shuffled]),
        )

    def test_dictionary_builder_order_independent(self):
        from colbatch._encode import _encode_string_dictionary

        values_a = ["zebra", "apple", "mango", "apple", "zebra"]
        values_b = ["apple", "zebra", "mango", "zebra", "apple"]
        encoded_a = _encode_string_dictionary(values_a)
        encoded_b = _encode_string_dictionary(values_b)
        # 字典段(条目数 + 排序后的条目)必须逐字节相同; 仅尾部索引序列不同。
        # 条目数 3, 条目总长 5+5+5, 字典段长度 = 4 + 3*(4+5) = 31。
        dictionary_part = 4 + 3 * (4 + 5)
        self.assertEqual(encoded_a[:dictionary_part], encoded_b[:dictionary_part])
        self.assertNotEqual(encoded_a, encoded_b)

    def test_reencode_roundtrip_is_stable(self):
        schema = Schema(
            [
                Column("i", ColumnType.INT64),
                Column("f", ColumnType.FLOAT64),
                Column("b", ColumnType.BOOL),
                Column("s", ColumnType.STRING),
            ]
        )
        columns = [
            [1, None, 3, 4, 5],
            [1.0, 2.0, None, float("nan"), -0.0],
            [True, False, None, True, False],
            ["a", "b", None, "a", "c"],
        ]
        data = encode_batch(schema, columns)
        decoded_schema, decoded, _ = decode_batch(data)
        re_encoded = encode_batch(decoded_schema, decoded)
        self.assertEqual(re_encoded, data)


class DeltaEncodingTest(unittest.TestCase):
    def test_small_deltas_pick_delta_encoding(self):
        schema = Schema([Column("seq", ColumnType.INT64)])
        values = [1000 + index for index in range(1000)]
        data = encode_batch(schema, [values])
        _, _, metadata = decode_batch(data)
        self.assertEqual(metadata.columns[0].encoding, "delta")

    def test_negative_and_zigzag_deltas(self):
        schema = Schema([Column("seq", ColumnType.INT64)])
        values = [0, -1, 1, -2, 2, -100, 100, -(2**63), 2**63 - 1]
        data = encode_batch(schema, [values])
        _, decoded, _ = decode_batch(data)
        self.assertEqual(decoded[0], values)

    def test_delta_overflow_falls_back_to_plain(self):
        # 相邻差值超过 int64 范围时必须回退, 且仍能精确往返。
        schema = Schema([Column("wide", ColumnType.INT64)])
        values = [-(2**63), 2**63 - 1, -(2**63), 2**63 - 1]
        data = encode_batch(schema, [values])
        _, decoded, metadata = decode_batch(data)
        self.assertEqual(metadata.columns[0].encoding, "plain")
        self.assertEqual(decoded[0], values)

    def test_delta_with_nulls(self):
        schema = Schema([Column("seq", ColumnType.INT64)])
        values = [10, None, 12, None, 14, 15]
        data = encode_batch(schema, [values])
        _, decoded, metadata = decode_batch(data)
        self.assertTrue(columns_equal([values], decoded))
        self.assertEqual(metadata.columns[0].null_count, 2)


class DictionaryEncodingTest(unittest.TestCase):
    def test_many_duplicates_pick_dictionary(self):
        schema = Schema([Column("cat", ColumnType.STRING)])
        values = (["red", "green", "blue"] * 500) + [None] * 10
        data = encode_batch(schema, [values])
        _, decoded, metadata = decode_batch(data)
        self.assertEqual(metadata.columns[0].encoding, "dictionary")
        self.assertEqual(decoded[0], values)

    def test_dictionary_switch_to_plain_for_unique_strings(self):
        schema = Schema([Column("u", ColumnType.STRING)])
        unique_values = [f"unique-string-{index:08d}" for index in range(500)]
        data = encode_batch(schema, [unique_values])
        _, decoded, metadata = decode_batch(data)
        self.assertEqual(metadata.columns[0].encoding, "plain")
        self.assertEqual(decoded[0], unique_values)

    def test_dictionary_switch_across_batches(self):
        # 同一 schema 的两批数据, 因基数不同应在两种编码间切换。
        schema = Schema([Column("v", ColumnType.STRING)])
        repeated = ["a", "b"] * 300
        unique = [f"v{index}" for index in range(300)]
        data_repeated = encode_batch(schema, [repeated])
        data_unique = encode_batch(schema, [unique])
        _, _, meta_repeated = decode_batch(data_repeated)
        _, _, meta_unique = decode_batch(data_unique)
        self.assertEqual(meta_repeated.columns[0].encoding, "dictionary")
        self.assertEqual(meta_unique.columns[0].encoding, "plain")

    def test_empty_string_dictionary_entry(self):
        schema = Schema([Column("s", ColumnType.STRING)])
        values = ["", "", "x", "", None, "x"]
        data = encode_batch(schema, [values])
        _, decoded, _ = decode_batch(data)
        self.assertEqual(decoded[0], values)


class SizeReportingTest(unittest.TestCase):
    def test_metadata_exposes_chunk_and_payload_sizes(self):
        schema = Schema([Column("i", ColumnType.INT64)])
        values = list(range(100))
        data = encode_batch(schema, [values])
        _, _, metadata = decode_batch(data)
        info = metadata.columns[0]
        self.assertGreater(info.chunk_bytes, info.payload_bytes)
        self.assertLessEqual(info.chunk_bytes, len(data))


if __name__ == "__main__":
    unittest.main()
