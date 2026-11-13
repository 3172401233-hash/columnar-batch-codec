"""随机化 fuzz: 任意类型组合、空值密度与字符串基数下的往返。"""

import math
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


class FuzzRoundTripTest(unittest.TestCase):
    def test_random_batches(self):
        rng = random.Random(2026)
        for iteration in range(40):
            nrows = rng.randint(0, 500)
            columns_spec = []
            data_columns = []
            for column_index in range(rng.randint(1, 5)):
                col_type = rng.choice(list(ColumnType))
                name = f"c{column_index}"
                columns_spec.append(Column(name, col_type))
                null_density = rng.choice([0.0, 0.1, 0.5, 1.0])
                values = []
                for _ in range(nrows):
                    if rng.random() < null_density:
                        values.append(None)
                        continue
                    if col_type is ColumnType.INT64:
                        values.append(rng.randint(-(2**63), 2**63 - 1))
                    elif col_type is ColumnType.FLOAT64:
                        values.append(
                            rng.choice(
                                [
                                    rng.uniform(-1e6, 1e6),
                                    float("nan"),
                                    float("inf"),
                                    float("-inf"),
                                    0.0,
                                    -0.0,
                                ]
                            )
                        )
                    elif col_type is ColumnType.BOOL:
                        values.append(rng.choice([True, False]))
                    else:
                        vocabulary = [
                            "alpha",
                            "beta",
                            "gamma",
                            "",
                            "中文",
                            "a,b,c",
                            "emoji😀",
                            f"unique-{rng.randint(0, 10_000)}",
                        ]
                        values.append(rng.choice(vocabulary))
                data_columns.append(values)

            schema = Schema(columns_spec)
            encoded = encode_batch(schema, data_columns)
            decoded_schema, decoded, metadata = decode_batch(encoded)
            self.assertEqual(
                [c.type for c in decoded_schema],
                [c.type for c in columns_spec],
            )
            self.assertTrue(columns_equal(data_columns, decoded))
            self.assertEqual(metadata.row_count, nrows)
            # 确定性: 再次编码必须逐字节相同。
            self.assertEqual(encode_batch(decoded_schema, decoded), encoded)

    def test_all_null_and_empty_edges(self):
        for col_type in ColumnType:
            schema = Schema([Column("e", col_type)])
            for rows in (0, 1, 7, 8, 9, 64, 65):
                values = [None] * rows
                data = encode_batch(schema, [values])
                _, decoded, _ = decode_batch(data)
                self.assertEqual(decoded[0], values)


if __name__ == "__main__":
    unittest.main()
