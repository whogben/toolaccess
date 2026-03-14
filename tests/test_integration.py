"""Integration tests across servers and surfaces."""

import inspect
import json

import pytest
from fastapi.testclient import TestClient

from toolaccess import (
    AccessPolicy,
    CLIServer,
    InjectContext,
    InvocationContext,
    JsonRenderer,
    NoOpRenderer,
    OpenAPIServer,
    ServerManager,
    StreamableHTTPMCPServer,
    SurfaceSpec,
    ToolDefinition,
    ToolService,
    get_public_signature,
)
from toolaccess.codecs import CsvListCodec, JsonObjectCodec


class TestDecoratorAPI:
    def test_tool_decorator_basic(self):
        svc = ToolService("test")

        @svc.tool()
        def my_tool(x: int) -> int:
            return x * 2

        assert len(svc.tools) == 1
        assert svc.tools[0].name == "my_tool"
        assert svc.tools[0].func(5) == 10

    def test_tool_decorator_with_name(self):
        svc = ToolService("test")

        @svc.tool(name="custom_name")
        def my_tool(x: int) -> int:
            return x

        assert svc.tools[0].name == "custom_name"

    def test_tool_decorator_with_description(self):
        svc = ToolService("test")

        @svc.tool(description="Custom description")
        def my_tool():
            """Docstring that should be overridden."""
            pass

        assert svc.tools[0].description == "Custom description"

    def test_tool_decorator_with_surfaces(self):
        svc = ToolService("test")

        @svc.tool(surfaces={"rest": SurfaceSpec(http_method="GET")})
        def my_tool():
            return "ok"

        assert svc.tools[0].surfaces["rest"].http_method == "GET"

    def test_tool_decorator_with_access_policy(self):
        svc = ToolService("test")
        policy = AccessPolicy(require_authenticated=True)

        @svc.tool(access=policy)
        def my_tool():
            return "secret"

        assert svc.tools[0].access is policy

    def test_tool_decorator_with_codecs(self):
        svc = ToolService("test")

        @svc.tool(codecs={"data": JsonObjectCodec()})
        def my_tool(data: dict):
            return data

        assert "data" in svc.tools[0].codecs

    def test_tool_decorator_with_renderer(self):
        svc = ToolService("test")
        renderer = JsonRenderer()

        @svc.tool(renderer=renderer)
        def my_tool():
            return {"key": "val"}

        assert svc.tools[0].renderer is renderer


class TestToolWithAccessPolicy:
    def test_access_policy_enforced_rest(self):
        mgr = ServerManager("test")

        def admin_only():
            return "admin data"

        svc = ToolService(
            "admin",
            [
                ToolDefinition(
                    func=admin_only,
                    name="admin_only",
                    access=AccessPolicy(require_authenticated=True),
                )
            ],
        )

        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        # PermissionError is converted to HTTP 403 by the route handler
        response = client.post("/api/admin_only")
        assert response.status_code == 403
        assert "Authentication required" in response.text


class TestToolWithCodecs:
    def test_codecs_registered_on_tool(self):
        """Verify that codecs are properly registered on a tool definition."""

        def process_items(items: str) -> str:
            return items

        tool = ToolDefinition(
            func=process_items,
            name="process_items",
            codecs={
                "items": CsvListCodec(),
                "data": JsonObjectCodec(),
            },
        )

        # Verify codecs are stored on the tool definition
        assert "items" in tool.codecs
        assert "data" in tool.codecs
        assert isinstance(tool.codecs["items"], CsvListCodec)
        assert isinstance(tool.codecs["data"], JsonObjectCodec)

    @pytest.mark.asyncio
    async def test_codecs_applied_in_pipeline(self):
        """Verify that codecs are applied in the invoke_tool pipeline."""

        def process_items(items: list) -> list:
            return items

        tool = ToolDefinition(
            func=process_items,
            name="process_items",
            codecs={"items": CsvListCodec()},
        )

        from toolaccess import InvocationContext, invoke_tool

        ctx = InvocationContext(surface="rest")
        # The codec should decode "a,b,c" -> ["a", "b", "c"]
        result = await invoke_tool(tool, {"items": "a,b,c"}, ctx)
        assert result == ["a", "b", "c"]


class TestToolWithRenderer:
    def test_custom_renderer_rest(self):
        mgr = ServerManager("test")

        def get_data():
            return {"status": "ok"}

        svc = ToolService(
            "tools",
            [
                ToolDefinition(
                    func=get_data,
                    name="get_data",
                    renderer=JsonRenderer(),
                )
            ],
        )

        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        response = client.post("/api/get_data")
        assert response.status_code == 200
        # JsonRenderer returns a JSON string, which FastAPI then serializes as a string
        # Result is double-encoded JSON: "{\"status\": \"ok\"}"
        assert "status" in response.text and "ok" in response.text


class TestPublicSignatureHelpers:
    def test_get_public_signature_omits_injected_context(self):
        from typing import Annotated

        def describe(
            name: str,
            ctx: Annotated[InvocationContext, InjectContext()] = None,
        ) -> str:
            return name

        public_sig, public_annotations, context_param = get_public_signature(describe)

        assert context_param == "ctx"
        assert list(public_sig.parameters) == ["name"]
        assert "ctx" not in public_annotations
        assert public_annotations["name"] is str
        assert public_annotations["return"] is str


class TestRestServerWithContextInjection:
    def test_context_injected_to_handler(self):
        mgr = ServerManager("test")

        def get_surface(ctx: InvocationContext = None) -> str:
            return ctx.surface if ctx else "no_context"

        svc = ToolService(
            "tools",
            [ToolDefinition(func=get_surface, name="get_surface")],
        )
        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        response = client.post("/api/get_surface")
        assert response.status_code == 200
        assert response.json() == "rest"

    def test_context_with_annotated_injection(self):
        from typing import Annotated

        mgr = ServerManager("test")

        def get_surface(
            ctx: Annotated[InvocationContext, InjectContext()] = None,
        ) -> str:
            return ctx.surface if ctx else "no_context"

        svc = ToolService(
            "tools",
            [ToolDefinition(func=get_surface, name="get_surface")],
        )
        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        response = client.post("/api/get_surface")
        assert response.status_code == 200
        assert response.json() == "rest"

    def test_openapi_schema_omits_context_param(self):
        mgr = ServerManager("test")

        def greet(name: str, ctx: InvocationContext = None) -> str:
            return f"{name}:{ctx.surface if ctx else 'missing'}"

        svc = ToolService("tools", [ToolDefinition(func=greet, name="greet")])
        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        spec = client.get("/api/openapi.json").json()
        operation = spec["paths"]["/greet"]["post"]
        parameter_names = [param["name"] for param in operation.get("parameters", [])]

        assert parameter_names == ["name"]
        assert "ctx" not in parameter_names


class TestCliServerWithRenderer:
    def test_default_renderer_outputs_json(self, runner):
        mgr = ServerManager("test")

        def get_data():
            return {"name": "test", "value": 42}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=get_data, name="get_data")],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(mgr.cli, ["tools", "get_data"])
        assert result.exit_code == 0
        # PydanticJsonRenderer is the default
        parsed = json.loads(result.output.strip())
        assert parsed == {"name": "test", "value": 42}

    def test_custom_cli_renderer(self, runner):
        mgr = ServerManager("test")

        def get_data():
            return {"key": "val"}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=get_data, name="get_data")],
        )

        # Custom renderer that adds prefix
        class PrefixRenderer:
            def render(self, value, *, surface, ctx):
                return f"OUTPUT: {json.dumps(value)}"

        cli = CLIServer("tools", default_renderer=PrefixRenderer())
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(mgr.cli, ["tools", "get_data"])
        assert result.exit_code == 0
        assert "OUTPUT:" in result.output

    def test_cli_command_executes(self, runner):
        mgr = ServerManager("test")

        def simple_tool():
            return {"status": "executed"}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=simple_tool, name="simple")],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(mgr.cli, ["tools", "simple"])
        assert result.exit_code == 0
        # CLI uses PydanticJsonRenderer by default
        assert '"status": "executed"' in result.output

    def test_cli_context_injection_uses_hidden_param(self, runner):
        mgr = ServerManager("test")

        def describe(name: str, ctx: InvocationContext = None) -> str:
            return f"{name}:{ctx.surface if ctx else 'missing'}"

        svc = ToolService(
            "tools",
            [ToolDefinition(func=describe, name="describe")],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(mgr.cli, ["tools", "describe", "alice"])
        assert result.exit_code == 0
        assert "alice:cli" in result.output

    def test_cli_uses_typer_safe_signature_for_codec_backed_types(self, runner):
        mgr = ServerManager("test")

        def process(data: dict[str, object]) -> list[str]:
            return sorted(data.keys())

        svc = ToolService(
            "tools",
            [
                ToolDefinition(
                    func=process,
                    name="process",
                    codecs={"data": JsonObjectCodec()},
                )
            ],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        command_callback = cli.typer_app.registered_commands[0].callback
        command_sig = inspect.signature(command_callback)
        assert command_sig.parameters["data"].annotation is str

        result = runner.invoke(mgr.cli, ["tools", "process", '{"beta": 2, "alpha": 1}'])
        assert result.exit_code == 0
        assert json.loads(result.output.strip()) == ["alpha", "beta"]


class TestMcpServerWithPublicSignatures:
    def test_mcp_wrapper_omits_context_param_from_signature(self):
        def describe(name: str, ctx: InvocationContext = None) -> str:
            return f"{name}:{ctx.surface if ctx else 'missing'}"

        tool = ToolDefinition(func=describe, name="describe")
        mcp = StreamableHTTPMCPServer("tools")

        wrapped = mcp._wrap_for_mcp(tool)
        wrapped_sig = inspect.signature(wrapped)

        assert list(wrapped_sig.parameters) == ["name"]
        assert "ctx" not in wrapped.__annotations__
        assert wrapped(name="alice") == "alice:mcp"
