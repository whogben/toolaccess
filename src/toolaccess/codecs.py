from __future__ import annotations

import base64
import json
from typing import Any, Protocol, runtime_checkable

from .context import InvocationContext


@runtime_checkable
class ArgumentCodec(Protocol):
    def decode(
        self, value: Any, *, parameter_name: str, ctx: InvocationContext
    ) -> Any: ...


class IdentityCodec:
    def decode(self, value: Any, *, parameter_name: str, ctx: InvocationContext) -> Any:
        return value


class JsonObjectCodec:
    def decode(self, value: Any, *, parameter_name: str, ctx: InvocationContext) -> Any:
        if value is None or isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        raise ValueError(
            f"Expected dict, JSON string, or None for '{parameter_name}', got {type(value).__name__}"
        )


class JsonValueCodec:
    def decode(self, value: Any, *, parameter_name: str, ctx: InvocationContext) -> Any:
        if isinstance(value, str):
            return json.loads(value)
        return value


class CsvListCodec:
    def __init__(self, strip: bool = True, delimiter: str = ","):
        self.strip = strip
        self.delimiter = delimiter

    def decode(
        self, value: Any, *, parameter_name: str, ctx: InvocationContext
    ) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            items = value.split(self.delimiter)
            if self.strip:
                items = [item.strip() for item in items]
            return items
        raise ValueError(
            f"Expected list, string, or None for '{parameter_name}', got {type(value).__name__}"
        )


class Base64BytesCodec:
    def __init__(self, optional: bool = False):
        self.optional = optional

    def decode(
        self, value: Any, *, parameter_name: str, ctx: InvocationContext
    ) -> bytes | None:
        if value is None:
            if self.optional:
                return None
            raise ValueError(f"Expected base64 string for '{parameter_name}', got None")
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return base64.b64decode(value)
        raise ValueError(
            f"Expected bytes, base64 string, or None for '{parameter_name}', got {type(value).__name__}"
        )


# Singleton instances for convenience
identity_codec = IdentityCodec()
json_object_codec = JsonObjectCodec()
json_value_codec = JsonValueCodec()
csv_list_codec = CsvListCodec()
base64_bytes_codec = Base64BytesCodec()
