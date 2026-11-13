"""逻辑数据类型与批次 schema 定义。"""

from dataclasses import dataclass
from enum import Enum

import math

from ._constants import (
    TYPE_BOOL,
    TYPE_CODES,
    TYPE_FLOAT64,
    TYPE_INT64,
    TYPE_STRING,
)
from .errors import CorruptFormatError


class ColumnType(Enum):
    INT64 = "int64"
    FLOAT64 = "float64"
    BOOL = "bool"
    STRING = "string"

    @property
    def code(self) -> int:
        return _TYPE_TO_CODE[self]

    @staticmethod
    def from_code(code: int) -> "ColumnType":
        try:
            return _CODE_TO_TYPE[code]
        except KeyError:
            raise CorruptFormatError(f"unknown column type code {code}") from None


_TYPE_TO_CODE = {
    ColumnType.INT64: TYPE_INT64,
    ColumnType.FLOAT64: TYPE_FLOAT64,
    ColumnType.BOOL: TYPE_BOOL,
    ColumnType.STRING: TYPE_STRING,
}
_CODE_TO_TYPE = {code: col_type for col_type, code in _TYPE_TO_CODE.items()}
assert set(_CODE_TO_TYPE) == TYPE_CODES


@dataclass(frozen=True)
class Column:
    """一列的声明信息。

    name: 列名, 非空 UTF-8 字符串, 同一 schema 内唯一。
    type: 列逻辑类型。
    """

    name: str
    type: ColumnType

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("column name must be a non-empty str")
        try:
            self.name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("column name must be valid UTF-8") from exc
        if not isinstance(self.type, ColumnType):
            raise ValueError("column type must be a ColumnType")


@dataclass(frozen=True)
class Schema:
    """批次的列集合。"""

    columns: tuple

    def __init__(self, columns) -> None:
        normalized = tuple(
            col if isinstance(col, Column) else Column(col[0], col[1])
            for col in columns
        )
        if not normalized:
            raise ValueError("schema must contain at least one column")
        names = [col.name for col in normalized]
        if len(set(names)) != len(names):
            raise ValueError("duplicate column names are not allowed")
        for col in normalized:
            if len(col.name.encode("utf-8")) > (1 << 20):
                raise ValueError("column name too long")
        object.__setattr__(self, "columns", normalized)

    def __len__(self) -> int:
        return len(self.columns)

    def __iter__(self):
        return iter(self.columns)


def validate_value(value, col_type: ColumnType) -> None:
    """编码前的单值类型检查 (None 由调用方处理)。

    bool 必须先于 int 判断, 因为 Python 的 bool 是 int 子类。
    """
    if col_type is ColumnType.BOOL:
        if not isinstance(value, bool):
            raise TypeError(f"expected bool, got {type(value).__name__}")
    elif col_type is ColumnType.INT64:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"expected int, got {type(value).__name__}")
        if not -(1 << 63) <= value <= (1 << 63) - 1:
            raise OverflowError("int64 value out of range")
    elif col_type is ColumnType.FLOAT64:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"expected float, got {type(value).__name__}")
        value_float = float(value)
        if math.isnan(value_float) or math.isinf(value_float):
            return
    elif col_type is ColumnType.STRING:
        if not isinstance(value, str):
            raise TypeError(f"expected str, got {type(value).__name__}")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("string must be valid UTF-8") from exc
