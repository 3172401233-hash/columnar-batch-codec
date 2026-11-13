"""统计各类型在典型数据分布下的编码大小。

运行: python -m benchmarks.size_report
"""

import random

from colbatch import Column, ColumnType, Schema, decode_batch, encode_batch


ROWS = 10_000


def _measure(column: Column, values):
    data = encode_batch(Schema([column]), [values])
    _, _, metadata = decode_batch(data)
    info = metadata.columns[0]
    return {
        "total_file": len(data),
        "chunk": info.chunk_bytes,
        "payload": info.payload_bytes,
        "encoding": info.encoding,
        "nulls": info.null_count,
    }


def _print_row(name, stats, raw_size):
    print(
        f"{name:<34} {stats['encoding']:<10} "
        f"payload={stats['payload']:>8} B  chunk={stats['chunk']:>8} B  "
        f"file={stats['total_file']:>8} B  vs_raw={stats['payload'] / raw_size:6.2f}x"
    )


def main() -> None:
    rng = random.Random(7)
    print(f"rows per batch: {ROWS}\n")

    int_monotonic = list(range(ROWS))
    int_random = [rng.randint(-(2**63), 2**63 - 1) for _ in range(ROWS)]
    int_with_nulls = [v if rng.random() > 0.2 else None for v in int_monotonic]
    _print_row(
        "int64 monotonic (delta)",
        _measure(Column("c", ColumnType.INT64), int_monotonic),
        ROWS * 8,
    )
    _print_row(
        "int64 random (plain)",
        _measure(Column("c", ColumnType.INT64), int_random),
        ROWS * 8,
    )
    _print_row(
        "int64 20% null + delta",
        _measure(Column("c", ColumnType.INT64), int_with_nulls),
        ROWS * 8,
    )

    floats = [rng.uniform(-1e9, 1e9) for _ in range(ROWS)]
    _print_row(
        "float64 random",
        _measure(Column("c", ColumnType.FLOAT64), floats),
        ROWS * 8,
    )

    bools = [rng.choice([True, False]) for _ in range(ROWS)]
    _print_row(
        "bool random (bitmap)",
        _measure(Column("c", ColumnType.BOOL), bools),
        ROWS,
    )

    dict_words = ["red", "green", "blue", "yellow", "black"]
    strings_repeated = [rng.choice(dict_words) for _ in range(ROWS)]
    strings_unique = [f"unique-value-{index:08d}" for index in range(ROWS)]
    raw_repeated = sum(len(v.encode()) for v in strings_repeated) + 4 * ROWS
    raw_unique = sum(len(v.encode()) for v in strings_unique) + 4 * ROWS
    _print_row(
        "string low-cardinality (dict)",
        _measure(Column("c", ColumnType.STRING), strings_repeated),
        raw_repeated,
    )
    _print_row(
        "string all unique (plain)",
        _measure(Column("c", ColumnType.STRING), strings_unique),
        raw_unique,
    )

    all_null = [None] * ROWS
    _print_row(
        "int64 all null",
        _measure(Column("c", ColumnType.INT64), all_null),
        ROWS * 8,
    )
    print("\nempty batch:")
    empty = encode_batch(Schema([Column("c", ColumnType.INT64)]), [[]])
    print(f"  int64 zero rows -> {len(empty)} bytes")


if __name__ == "__main__":
    main()
