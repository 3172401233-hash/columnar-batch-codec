"""编解码过程中可能抛出的异常类型。"""


class CorruptFormatError(Exception):
    """文件不符合 colbatch 格式约定(魔数、长度、偏移、类型等)。"""


class TruncatedFormatError(CorruptFormatError):
    """文件在声明的结构结束前就被截断。"""


class ChecksumError(CorruptFormatError):
    """CRC32 校验失败, 数据被截断之外的方式损坏或篡改。"""
