"""Tests for codec implementations."""

import base64
import json

import pytest

from toolaccess import InvocationContext
from toolaccess.codecs import (
    Base64BytesCodec,
    CsvListCodec,
    IdentityCodec,
    JsonObjectCodec,
    JsonValueCodec,
    PydanticModelCodec,
    base64_bytes_codec,
    csv_list_codec,
    identity_codec,
    json_object_codec,
    json_value_codec,
)


@pytest.fixture
def mock_ctx():
    return InvocationContext(surface="rest")


class TestIdentityCodec:
    def test_passes_through_unchanged(self, mock_ctx):
        codec = IdentityCodec()
        assert codec.decode("hello", parameter_name="msg", ctx=mock_ctx) == "hello"
        assert codec.decode(123, parameter_name="num", ctx=mock_ctx) == 123
        assert codec.decode({"key": "val"}, parameter_name="dict", ctx=mock_ctx) == {
            "key": "val"
        }
        assert codec.decode([1, 2, 3], parameter_name="list", ctx=mock_ctx) == [1, 2, 3]
        assert codec.decode(None, parameter_name="none", ctx=mock_ctx) is None

    def test_singleton_instance(self):
        assert isinstance(identity_codec, IdentityCodec)


class TestJsonObjectCodec:
    def test_parses_json_string_to_dict(self, mock_ctx):
        codec = JsonObjectCodec()
        json_str = '{"name": "test", "value": 42}'
        result = codec.decode(json_str, parameter_name="data", ctx=mock_ctx)
        assert result == {"name": "test", "value": 42}

    def test_passes_through_dict(self, mock_ctx):
        codec = JsonObjectCodec()
        data = {"existing": "dict"}
        result = codec.decode(data, parameter_name="data", ctx=mock_ctx)
        assert result is data

    def test_handles_none(self, mock_ctx):
        codec = JsonObjectCodec()
        result = codec.decode(None, parameter_name="data", ctx=mock_ctx)
        assert result is None

    def test_invalid_json_raises(self, mock_ctx):
        codec = JsonObjectCodec()
        with pytest.raises(json.JSONDecodeError):
            codec.decode("not valid json", parameter_name="data", ctx=mock_ctx)

    def test_non_string_non_dict_raises(self, mock_ctx):
        codec = JsonObjectCodec()
        with pytest.raises(ValueError, match="Expected dict, JSON string, or None"):
            codec.decode(123, parameter_name="data", ctx=mock_ctx)

    def test_singleton_instance(self):
        assert isinstance(json_object_codec, JsonObjectCodec)


class TestJsonValueCodec:
    def test_parses_json_string_to_dict(self, mock_ctx):
        codec = JsonValueCodec()
        json_str = '{"key": "value"}'
        result = codec.decode(json_str, parameter_name="data", ctx=mock_ctx)
        assert result == {"key": "value"}

    def test_parses_json_string_to_list(self, mock_ctx):
        codec = JsonValueCodec()
        json_str = "[1, 2, 3]"
        result = codec.decode(json_str, parameter_name="items", ctx=mock_ctx)
        assert result == [1, 2, 3]

    def test_parses_json_string_to_int(self, mock_ctx):
        codec = JsonValueCodec()
        json_str = "42"
        result = codec.decode(json_str, parameter_name="num", ctx=mock_ctx)
        assert result == 42

    def test_parses_json_string_to_string(self, mock_ctx):
        codec = JsonValueCodec()
        json_str = '"hello"'
        result = codec.decode(json_str, parameter_name="msg", ctx=mock_ctx)
        assert result == "hello"

    def test_parses_json_null(self, mock_ctx):
        codec = JsonValueCodec()
        json_str = "null"
        result = codec.decode(json_str, parameter_name="val", ctx=mock_ctx)
        assert result is None

    def test_passes_through_non_string(self, mock_ctx):
        codec = JsonValueCodec()
        assert codec.decode(123, parameter_name="num", ctx=mock_ctx) == 123
        assert codec.decode([1, 2], parameter_name="list", ctx=mock_ctx) == [1, 2]
        assert codec.decode(None, parameter_name="none", ctx=mock_ctx) is None

    def test_invalid_json_raises(self, mock_ctx):
        codec = JsonValueCodec()
        with pytest.raises(json.JSONDecodeError):
            codec.decode("not valid json", parameter_name="data", ctx=mock_ctx)

    def test_singleton_instance(self):
        assert isinstance(json_value_codec, JsonValueCodec)


class TestCsvListCodec:
    def test_splits_comma_separated(self, mock_ctx):
        codec = CsvListCodec()
        result = codec.decode("a,b,c", parameter_name="items", ctx=mock_ctx)
        assert result == ["a", "b", "c"]

    def test_strips_whitespace_by_default(self, mock_ctx):
        codec = CsvListCodec()
        result = codec.decode("  a  ,  b  ,  c  ", parameter_name="items", ctx=mock_ctx)
        assert result == ["a", "b", "c"]

    def test_no_strip_when_disabled(self, mock_ctx):
        codec = CsvListCodec(strip=False)
        result = codec.decode("  a  ,  b  ", parameter_name="items", ctx=mock_ctx)
        assert result == ["  a  ", "  b  "]

    def test_custom_delimiter(self, mock_ctx):
        codec = CsvListCodec(delimiter=";")
        result = codec.decode("a;b;c", parameter_name="items", ctx=mock_ctx)
        assert result == ["a", "b", "c"]

    def test_handles_none(self, mock_ctx):
        codec = CsvListCodec()
        result = codec.decode(None, parameter_name="items", ctx=mock_ctx)
        assert result == []

    def test_passes_through_list(self, mock_ctx):
        codec = CsvListCodec()
        data = ["already", "a", "list"]
        result = codec.decode(data, parameter_name="items", ctx=mock_ctx)
        assert result is data

    def test_empty_string_gives_list_with_empty_string(self, mock_ctx):
        codec = CsvListCodec()
        result = codec.decode("", parameter_name="items", ctx=mock_ctx)
        assert result == [""]

    def test_singleton_instance(self):
        assert isinstance(csv_list_codec, CsvListCodec)


class TestBase64BytesCodec:
    def test_decodes_base64_string(self, mock_ctx):
        codec = Base64BytesCodec()
        b64_str = base64.b64encode(b"hello world").decode()
        result = codec.decode(b64_str, parameter_name="data", ctx=mock_ctx)
        assert result == b"hello world"

    def test_passes_through_bytes(self, mock_ctx):
        codec = Base64BytesCodec()
        data = b"already bytes"
        result = codec.decode(data, parameter_name="data", ctx=mock_ctx)
        assert result is data

    def test_handles_none_when_optional(self, mock_ctx):
        codec = Base64BytesCodec(optional=True)
        result = codec.decode(None, parameter_name="data", ctx=mock_ctx)
        assert result is None

    def test_none_raises_when_not_optional(self, mock_ctx):
        codec = Base64BytesCodec(optional=False)
        with pytest.raises(ValueError, match="Expected base64 string"):
            codec.decode(None, parameter_name="data", ctx=mock_ctx)

    def test_invalid_base64_raises(self, mock_ctx):
        codec = Base64BytesCodec()
        with pytest.raises(Exception):  # base64.binascii.Error
            codec.decode("!!!not valid base64!!!", parameter_name="data", ctx=mock_ctx)

    def test_non_string_non_bytes_raises(self, mock_ctx):
        codec = Base64BytesCodec()
        with pytest.raises(ValueError, match="Expected bytes, base64 string, or None"):
            codec.decode(123, parameter_name="data", ctx=mock_ctx)

    def test_singleton_instance(self):
        assert isinstance(base64_bytes_codec, Base64BytesCodec)


class TestPydanticModelCodec:
    def test_decode_json_string_to_pydantic_model(self, mock_ctx):
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        json_str = '{"name": "test", "value": 42}'
        result = codec.decode(json_str, parameter_name="data", ctx=mock_ctx)
        assert isinstance(result, TestModel)
        assert result.name == "test"
        assert result.value == 42

    def test_decode_dict_to_pydantic_model(self, mock_ctx):
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        data = {"name": "test", "value": 42}
        result = codec.decode(data, parameter_name="data", ctx=mock_ctx)
        assert isinstance(result, TestModel)
        assert result.name == "test"
        assert result.value == 42

    def test_passes_through_already_decoded_model(self, mock_ctx):
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        instance = TestModel(name="test", value=42)
        result = codec.decode(instance, parameter_name="data", ctx=mock_ctx)
        assert result is instance

    def test_handles_none(self, mock_ctx):
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        result = codec.decode(None, parameter_name="data", ctx=mock_ctx)
        assert result is None

    def test_raises_value_error_for_invalid_json(self, mock_ctx):
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        with pytest.raises(ValueError):
            codec.decode("not valid json", parameter_name="data", ctx=mock_ctx)
