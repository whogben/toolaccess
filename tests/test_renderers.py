"""Tests for renderer implementations."""

import json

from pydantic import BaseModel

from toolaccess import (
    JsonRenderer,
    NoOpRenderer,
    PydanticJsonRenderer,
)


class TestNoOpRenderer:
    def test_returns_value_unchanged(self, mock_ctx):
        renderer = NoOpRenderer()
        assert renderer.render("hello", surface="rest", ctx=mock_ctx) == "hello"
        assert renderer.render(123, surface="rest", ctx=mock_ctx) == 123
        assert renderer.render({"key": "val"}, surface="rest", ctx=mock_ctx) == {
            "key": "val"
        }


class TestJsonRenderer:
    def test_renders_dict_as_json(self, mock_ctx):
        renderer = JsonRenderer()
        result = renderer.render({"name": "test"}, surface="rest", ctx=mock_ctx)
        assert result == '{"name": "test"}'

    def test_renders_list_as_json(self, mock_ctx):
        renderer = JsonRenderer()
        result = renderer.render([1, 2, 3], surface="rest", ctx=mock_ctx)
        assert result == "[1, 2, 3]"

    def test_renders_string_as_json(self, mock_ctx):
        renderer = JsonRenderer()
        result = renderer.render("hello", surface="rest", ctx=mock_ctx)
        assert result == '"hello"'

    def test_renders_with_indent(self, mock_ctx):
        renderer = JsonRenderer(indent=2)
        result = renderer.render({"a": 1}, surface="rest", ctx=mock_ctx)
        assert '{\n  "a": 1\n}' == result

    def test_renders_with_sorted_keys(self, mock_ctx):
        renderer = JsonRenderer(sort_keys=True)
        result = renderer.render({"z": 1, "a": 2}, surface="rest", ctx=mock_ctx)
        assert result == '{"a": 2, "z": 1}'

    def test_handles_non_serializable_with_default_str(self, mock_ctx):
        renderer = JsonRenderer()

        class CustomObj:
            def __str__(self):
                return "custom_obj"

        result = renderer.render(CustomObj(), surface="rest", ctx=mock_ctx)
        assert result == '"custom_obj"'


class TestPydanticJsonRenderer:
    def test_renders_pydantic_model(self, mock_ctx):
        class TestModel(BaseModel):
            name: str
            value: int

        renderer = PydanticJsonRenderer()
        model = TestModel(name="test", value=42)
        result = renderer.render(model, surface="rest", ctx=mock_ctx)
        assert json.loads(result) == {"name": "test", "value": 42}

    def test_renders_regular_dict(self, mock_ctx):
        renderer = PydanticJsonRenderer()
        result = renderer.render({"key": "val"}, surface="rest", ctx=mock_ctx)
        assert json.loads(result) == {"key": "val"}

    def test_uses_by_alias(self, mock_ctx):
        from pydantic import ConfigDict

        class TestModel(BaseModel):
            model_config = ConfigDict(populate_by_name=True)
            field_name: str

        renderer = PydanticJsonRenderer(by_alias=True)
        model = TestModel(field_name="test")
        # model_dump with by_alias=True should still work
        result = renderer.render(model, surface="rest", ctx=mock_ctx)
        parsed = json.loads(result)
        assert "field_name" in parsed

    def test_renders_with_indent(self, mock_ctx):
        class TestModel(BaseModel):
            a: int

        renderer = PydanticJsonRenderer(indent=2)
        model = TestModel(a=1)
        result = renderer.render(model, surface="rest", ctx=mock_ctx)
        assert "{\n" in result

    def test_handles_non_serializable_with_default_str(self, mock_ctx):
        renderer = PydanticJsonRenderer()

        class CustomObj:
            def __str__(self):
                return "custom"

        result = renderer.render(CustomObj(), surface="rest", ctx=mock_ctx)
        assert result == '"custom"'
