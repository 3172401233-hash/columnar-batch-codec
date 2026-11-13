# columnar-batch-codec
Python 列式批数据二进制编解码库，支持空值位图、字典编码和列级统计信息

## 特性

- 四种逻辑类型：有符号 `int64`、`float64`、`bool`、UTF-8 `string`，每列允许 `null`
- `int64` 支持 delta + ZigZag varint 编码（delta 溢出 int64 时自动回退 plain）
- `string` 支持字典编码，字典条目按 UTF-8 字节序排序，与插入顺序无关
- 列块头携带类型、编码、空值数量、可选最小/最大值，`read_metadata()` 无需解码数据
  即可依据统计信息跳过整批
- 确定性：同一批数据永远编码成相同字节
- 安全读取：所有长度/偏移/下标均有边界与值域校验，截断文件只抛
  `TruncatedFormatError`，不会越界读取
- 三层 CRC32：文件头、每个列块头、每块负载各自校验，文件尾再对整文件做一次 CRC

## 用法

```python
from colbatch import Schema, Column, ColumnType, encode_batch, decode_batch, read_metadata

schema = Schema([
    Column("id", ColumnType.INT64),
    Column("name", ColumnType.STRING),
])
columns = [
    [1, None, 3],
    ["red", "green", None],
]

data = encode_batch(schema, columns)          # bytes
schema2, values, metadata = decode_batch(data) # 精确恢复类型与 null 位置

meta = read_metadata(data)  # 只解析头部与列统计, 不还原数据
for column in meta.columns:
    print(column.name, column.null_count, column.minimum, column.maximum)
```

## 文件格式

所有多字节整数为小端序。

```
文件头 40B: magic "CBF1" | version u16 | flags u16 | ncols u32
           | header_len u32 | nrows u64 | reserved 12B | crc32 u32
列块 (重复 ncols 次):
    块头: total_len u32 | name_len u32 | type u8 | encoding u8
         | flags u8 | reserved u8 | null_count u64
         | payload_len u32 | minmax_len u32 | name | minmax | crc32 u32
    空值位图 (flags 指示, ceil(nrows/8) 字节, 尾部填充位必须为 0)
    负载 (payload_len 字节)
    负载 crc32 u32
文件尾: magic "CBFE" | crc32(文件头与全部列块)
```

- 空值位图按位置标记非空行，负载只存放非空值
- `float64` 的所有 NaN 统一为 canonical quiet NaN；含 NaN 的列省略 min/max
- 字符串 plain 编码为 `[u32 字节长度][UTF-8 字节]...`；字典编码为
  `entry_count u32` 后接排序后的条目，再接每个值的 `u32` 下标
- 编码选择是确定性的：多种编码等长时按固定优先级（int64 优先 delta，
  string 优先字典）选择

## 测试与体积报告

```bash
python -m unittest          # 49 个测试: 往返/全 null/空批次/NaN/Inf/
                            # 字典切换/截断穷举/随机损坏/模糊测试
python -m benchmarks.size_report
```

10,000 行批次的典型负载体积（相对原始定长大小）：

| 列类型 | 编码 | 负载大小 | 比例 |
| --- | --- | --- | --- |
| int64 单调递增 | delta | 10,007 B | 0.13x |
| int64 随机 64 位 | plain | 80,000 B | 1.00x |
| float64 随机 | plain | 80,000 B | 1.00x |
| bool | 位图 | 1,250 B | 0.12x |
| string 低基数 | 字典 | 40,047 B | 0.47x |
| string 全唯一 | plain | 250,000 B | 1.00x |
| 全 null 列 | 空负载 | 0 B | 0.00x |
