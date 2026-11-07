"""Small request validator for the input subset of the checked-in OpenAPI contract."""

import json
from pathlib import Path

from errors import AppError

API = json.loads((Path(__file__).parent / "doc/openapi.json").read_text())
SCHEMAS = API["components"]["schemas"]


def validate(name, value):
    def check(s, v):
        if "$ref" in s:
            return check(SCHEMAS[s["$ref"].rsplit("/", 1)[-1]], v)
        kind = s.get("type")
        types = {
            "object": lambda x: isinstance(x, dict),
            "array": lambda x: isinstance(x, list),
            "string": lambda x: isinstance(x, str),
            "integer": lambda x: type(x) is int,
            "boolean": lambda x: type(x) is bool,
            "null": lambda x: x is None,
        }
        if kind and not types[kind](v):
            return False
        if "enum" in s and v not in s["enum"]:
            return False
        if "const" in s and v != s["const"]:
            return False
        if isinstance(v, dict):
            if not set(s.get("required", [])) <= set(v):
                return False
            if s.get("additionalProperties") is False and set(v) - set(
                s.get("properties", {})
            ):
                return False
            if any(
                not check(child, v[key])
                for key, child in s.get("properties", {}).items()
                if key in v
            ):
                return False
        if isinstance(v, str) and not s.get("minLength", 0) <= len(v) <= s.get(
            "maxLength", float("inf")
        ):
            return False
        if type(v) is int and not s.get("minimum", -float("inf")) <= v <= s.get(
            "maximum", float("inf")
        ):
            return False
        if isinstance(v, list):
            if not s.get("minItems", 0) <= len(v) <= s.get("maxItems", float("inf")):
                return False
            if any(not check(s.get("items", {}), x) for x in v):
                return False
        if "anyOf" in s and not any(check(child, v) for child in s["anyOf"]):
            return False
        return True

    if not check(SCHEMAS[name], value):
        raise AppError("INVALID_INPUT", "请求字段、类型或范围不符合接口契约。")
    return value
