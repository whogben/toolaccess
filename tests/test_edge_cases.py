"""Tests for edge cases and corner scenarios."""

from toolaccess import SurfaceSpec, ToolDefinition, ToolService


class TestEdgeCases:
    def test_tool_definition_auto_description(self):
        def my_function():
            """This is my docstring."""
            pass

        tool = ToolDefinition(func=my_function, name="my_function")
        assert tool.description == "This is my docstring."

    def test_tool_definition_no_docstring(self):
        def my_function():
            pass

        tool = ToolDefinition(func=my_function, name="my_function")
        assert tool.description is None

    def test_get_surface_spec_default(self):
        tool = ToolDefinition(func=lambda: None, name="test")
        spec = tool.surfaces.get("rest", SurfaceSpec())
        assert isinstance(spec, SurfaceSpec)

    def test_get_surface_spec_configured(self):
        custom_spec = SurfaceSpec(http_method="PUT", enabled=False)
        tool = ToolDefinition(
            func=lambda: None,
            name="test",
            surfaces={"rest": custom_spec},
        )
        spec = tool.surfaces.get("rest", SurfaceSpec())
        assert spec.http_method == "PUT"
        assert spec.enabled is False

    def test_multiple_tools_same_service(self):
        svc = ToolService("multi")

        @svc.tool(name="tool_a")
        def func_a():
            return "a"

        @svc.tool(name="tool_b")
        def func_b():
            return "b"

        assert len(svc.tools) == 2
        assert svc.tools[0].name == "tool_a"
        assert svc.tools[1].name == "tool_b"

    def test_normalize_tool_callable(self):
        svc = ToolService("test")

        def my_func():
            return "ok"

        tool = svc._normalize_tool(my_func)
        assert isinstance(tool, ToolDefinition)
        assert tool.name == "my_func"

    def test_normalize_tool_already_definition(self):
        svc = ToolService("test")
        existing = ToolDefinition(func=lambda: None, name="existing")

        result = svc._normalize_tool(existing)
        assert result is existing
