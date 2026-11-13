"""单列、多列、空批次、全 null、NaN/Inf 等往返与类型恢复测试。"""

import math
import unittest

from colbatch import (
    Column,
    ColumnType,
    Schema,
    decode_batch,
    encode_batch,
    read_metadata,
)
from tests._helpers import columns_equal, values_equal


class SingleColumnRoundTripTest(unittest.TestCase):
    def _check(self, col_type, values):
        schema = Schema([Column("c0", col_type)])
        data = encode_batch(schema, [values])
        decoded_schema, columns, metadata = decode_batch(data)
        self.assertEqual(decoded_schema.columns[0].type, col_type)
        self.assertEqual(metadata.row_count, len(values))
        self.assertTrue(values_equal(values, columns[0]))
        return data, columns[0], metadata

    def test_int64_basic(self):
        values = [0, 1, -1, 2**63 - 1, -(2**63), 42, -42]
        data, _, meta = self._check(ColumnType.INT64, values)
        col_meta = meta.columns[0]
        self.assertEqual(col_meta.minimum, -(2**63))
        self.assertEqual(col_meta.maximum, 2**63 - 1)
        self.assertEqual(col_meta.null_count, 0)

    def test_int64_with_nulls(self):
        values = [1, None, 3, None, None, -7]
        self._check(ColumnType.INT64, values)

    def test_float64_basic(self):
        values = [0.0, -0.0, 3.14159, -2.5e100, 1e-300]
        _, decoded, _ = self._check(ColumnType.FLOAT64, values)
        self.assertTrue(all(isinstance(v, float) for v in decoded))

    def test_float64_nan_and_inf(self):
        values = [
            float("nan"),
            float("inf"),
            float("-inf"),
            None,
            math.nan,
            1.0,
        ]
        data, decoded, meta = self._check(ColumnType.FLOAT64, values)
        self.assertTrue(math.isnan(decoded[0]))
        self.assertTrue(math.isinf(decoded[1]) and decoded[1] > 0)
        self.assertTrue(math.isinf(decoded[2]) and decoded[2] < 0)
        self.assertIsNone(decoded[3])
        # 含 NaN 时按规范不输出 min/max
        self.assertIsNone(meta.columns[0].minimum)
        self.assertIsNone(meta.columns[0].maximum)

    def test_float64_minmax_without_nan(self):
        values = [float("inf"), float("-inf"), 0.5, -100.0]
        _, _, meta = self._check(ColumnType.FLOAT64, values)
        self.assertEqual(meta.columns[0].minimum, float("-inf"))
        self.assertEqual(meta.columns[0].maximum, float("inf"))

    def test_bool_basic(self):
        values = [True, False, True, True, False, None, True]
        _, decoded, _ = self._check(ColumnType.BOOL, values)
        self.assertTrue(all(v is None or type(v) is bool for v in decoded))

    def test_string_basic(self):
        values = ["hello", "", "世界\u4e2d\u6587", "a,b,c", "line\nbreak", None, "hello"]
        _, decoded, _ = self._check(ColumnType.STRING, values)
        self.assertTrue(all(v is None or type(v) is str for v in decoded))

    def test_string_emoji_and_null_byte(self):
        values = ["😀\u0000x", "é", None, ""]
        self._check(ColumnType.STRING, values)


class MultiColumnRoundTripTest(unittest.TestCase):
    def test_four_types_together(self):
        schema = Schema(
            [
                Column("id", ColumnType.INT64),
                Column("score", ColumnType.FLOAT64),
                Column("active", ColumnType.BOOL),
                Column("label", ColumnType.STRING),
            ]
        )
        columns = [
            [1, None, 3, 4, -(2**63)],
            [1.1, 2.2, None, float("nan"), float("inf")],
            [True, False, True, None, False],
            ["x", "y", None, "z", "x"],
        ]
        data = encode_batch(schema, columns)
        decoded_schema, decoded, metadata = decode_batch(data)
        self.assertEqual([c.name for c in decoded_schema], ["id", "score", "active", "label"])
        self.assertTrue(columns_equal(columns, decoded))
        self.assertEqual(metadata.row_count, 5)
        self.assertEqual(len(metadata.columns), 4)

    def test_null_positions_preserved(self):
        schema = Schema([Column("a", ColumnType.INT64), Column("b", ColumnType.STRING)])
        columns = [[None, 2, None, 4], ["p", None, "q", None]]
        _, decoded, _ = decode_batch(encode_batch(schema, columns))
        self.assertEqual([v is None for v in decoded[0]], [True, False, True, False])
        self.assertEqual([v is None for v in decoded[1]], [False, True, False, True])


class EmptyBatchTest(unittest.TestCase):
    def test_zero_rows_all_types(self):
        schema = Schema(
            [
                Column("i", ColumnType.INT64),
                Column("f", ColumnType.FLOAT64),
                Column("b", ColumnType.BOOL),
                Column("s", ColumnType.STRING),
            ]
        )
        columns = [[] for _ in range(4)]
        data = encode_batch(schema, columns)
        decoded_schema, decoded, metadata = decode_batch(data)
        self.assertEqual(metadata.row_count, 0)
        self.assertEqual(decoded, [[], [], [], []])
        meta = read_metadata(data)
        self.assertEqual(meta.row_count, 0)
        for column_meta in meta.columns:
            self.assertEqual(column_meta.null_count, 0)
            self.assertIsNone(column_meta.minimum)


class AllNullTest(unittest.TestCase):
    def test_all_null_each_type(self):
        for col_type in ColumnType:
            with self.subTest(col_type=col_type):
                schema = Schema([Column("n", col_type)])
                values = [None] * 20
                data = encode_batch(schema, [values])
                _, decoded, metadata = decode_batch(data)
                self.assertEqual(decoded[0], values)
                self.assertEqual(metadata.columns[0].null_count, 20)
                self.assertIsNone(metadata.columns[0].minimum)
                self.assertEqual(metadata.columns[0].payload_bytes, 0)


class TypeValidationTest(unittest.TestCase):
    def test_bool_rejected_as_int(self):
        schema = Schema([Column("i", ColumnType.INT64)])
        with self.assertRaises(TypeError):
            encode_batch(schema, [[True]])

    def test_int_rejected_as_bool(self):
        schema = Schema([Column("b", ColumnType.BOOL)])
        with self.assertRaises(TypeError):
            encode_batch(schema, [[1]])

    def test_int_out_of_range(self):
        schema = Schema([Column("i", ColumnType.INT64)])
        with self.assertRaises(OverflowError):
            encode_batch(schema, [[2**63]])

    def test_string_rejected_for_int(self):
        schema = Schema([Column("i", ColumnType.INT64)])
        with self.assertRaises(TypeError):
            encode_batch(schema, [["1"]])

    def test_column_length_mismatch(self):
        schema = Schema(
            [Column("a", ColumnType.INT64), Column("b", ColumnType.INT64)]
        )
        with self.assertRaises(ValueError):
            encode_batch(schema, [[1, 2], [1]])


if __name__ == "__main__":
    unittest.main()
