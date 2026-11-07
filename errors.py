"""Stable business errors; never expose provider responses or credentials."""


class AppError(ValueError):
    def __init__(self, code, message, status=400, details=None):
        super().__init__(message)
        self.code, self.status = code, status
        self.detail = {"code": code, "message": message, "details": details or {}}


def fields(data, allowed, required=()):
    if (
        not isinstance(data, dict)
        or set(data) - set(allowed)
        or not set(required) <= set(data)
    ):
        raise AppError("INVALID_INPUT", "请求字段不正确或缺少必填项。")
    return data


def text(value, maximum, label="文本", minimum=1):
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise AppError("INVALID_INPUT", f"{label}需要 {minimum}—{maximum} 个字符。")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AppError("INVALID_INPUT", f"{label}必须是有效 UTF-8 文本。") from None
    if "\x00" in value or (minimum and not value.strip()):
        raise AppError("INVALID_INPUT", f"{label}不能为空或包含 NUL。")
    return value.strip()


def positive(value, label="版本号"):
    if type(value) is not int or value < 1:
        raise AppError("INVALID_INPUT", f"{label}需要正整数。")
    return value
