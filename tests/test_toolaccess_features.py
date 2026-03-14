"""Comprehensive tests for ToolAccess features."""

import base64
import inspect
import json
from dataclasses import dataclass
from typing import Annotated
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from toolaccess import (
    AccessPolicy,
    CLIServer,
    InjectContext,
    InvocationContext,
    JsonRenderer,
    NoOpRenderer,
    OpenAPIServer,
    Principal,
    PydanticJsonRenderer,
    ServerManager,
    StreamableHTTPMCPServer,
    SurfaceSpec,
    ToolDefinition,
    ToolService,
    get_context_param,
    get_public_signature,
    inject_context,
    invoke_tool,
)
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
from toolaccess.definition import (
    is_pydantic_model,
    get_pydantic_model_params,
)
from toolaccess.pipeline import (
    call_user_func,
    decode_args,
    render_result,
    resolve_principal,
    validate_access,
)


# =============================================================================
# 1. Context and Principal Tests
# =============================================================================


class TestInvocationContext:
    def test_create_with_all_fields(self):
        principal = Principal(kind="user", id="123", name="test_user")
        ctx = InvocationContext(
            surface="rest",
            principal=principal,
            raw_request={"headers": {}},
            raw_mcp_context=None,
            raw_cli_context=None,
            state={"key": "value"},
        )
        assert ctx.surface == "rest"
        assert ctx.principal == principal
        assert ctx.raw_request == {"headers": {}}
        assert ctx.state == {"key": "value"}

    def test_create_with_defaults(self):
        ctx = InvocationContext(surface="cli")
        assert ctx.surface == "cli"
        assert ctx.principal is None
        assert ctx.raw_request is None
        assert ctx.state == {}

    def test_all_surface_types(self):
        for surface in ["rest", "mcp", "cli"]:
            ctx = InvocationContext(surface=surface)
            assert ctx.surface == surface


class TestPrincipal:
    def test_principal_minimal(self):
        p = Principal(kind="anonymous")
        assert p.kind == "anonymous"
        assert p.id is None
        assert p.name is None
        assert p.claims == {}
        assert p.is_authenticated is False
        assert p.is_trusted_local is False

    def test_principal_full(self):
        p = Principal(
            kind="user",
            id="user-123",
            name="John Doe",
            claims={"role": "admin", "org": "acme"},
            is_authenticated=True,
            is_trusted_local=True,
        )
        assert p.kind == "user"
        assert p.id == "user-123"
        assert p.name == "John Doe"
        assert p.claims == {"role": "admin", "org": "acme"}
        assert p.is_authenticated is True
        assert p.is_trusted_local is True

    def test_principal_various_claims(self):
        # Empty claims
        p1 = Principal(kind="service", claims={})
        assert p1.claims == {}

        # Complex claims
        p2 = Principal(
            kind="user",
            claims={
                "scopes": ["read", "write"],
                "metadata": {"department": "engineering"},
            },
        )
        assert p2.claims["scopes"] == ["read", "write"]
        assert p2.claims["metadata"]["department"] == "engineering"


class TestAccessPolicy:
    def test_default_policy(self):
        policy = AccessPolicy()
        assert policy.require_authenticated is False
        assert policy.allow_anonymous is True
        assert policy.allow_trusted_local is None
        assert policy.required_claims == {}

    def test_authenticated_required(self):
        policy = AccessPolicy(require_authenticated=True)
        assert policy.require_authenticated is True

    def test_no_anonymous(self):
        policy = AccessPolicy(allow_anonymous=False)
        assert policy.allow_anonymous is False

    def test_trusted_local_required(self):
        policy = AccessPolicy(allow_trusted_local=True)
        assert policy.allow_trusted_local is True

    def test_trusted_local_denied(self):
        policy = AccessPolicy(allow_trusted_local=False)
        assert policy.allow_trusted_local is False

    def test_required_claims(self):
        policy = AccessPolicy(required_claims={"role": "admin", "tenant": "prod"})
        assert policy.required_claims == {"role": "admin", "tenant": "prod"}

    def test_combined_policy(self):
        policy = AccessPolicy(
            require_authenticated=True,
            allow_anonymous=False,
            allow_trusted_local=True,
            required_claims={"scope": "tools:execute"},
        )
        assert policy.require_authenticated is True
        assert policy.allow_anonymous is False
        assert policy.allow_trusted_local is True
        assert policy.required_claims == {"scope": "tools:execute"}


class TestSurfaceSpec:
    def test_default_spec(self):
        spec = SurfaceSpec()
        assert spec.enabled is True
        assert spec.http_method is None
        assert spec.renderer is None
        assert spec.principal_resolver is None

    def test_spec_with_method(self):
        spec = SurfaceSpec(http_method="GET")
        assert spec.http_method == "GET"

    def test_spec_disabled(self):
        spec = SurfaceSpec(enabled=False)
        assert spec.enabled is False


# =============================================================================
# 2. Codecs Tests
# =============================================================================


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


# =============================================================================
# 3. Renderers Tests
# =============================================================================


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


# =============================================================================
# 4. Context Injection Tests
# =============================================================================


class TestGetContextParam:
    def test_detects_annotated_inject_context(self):
        def func(ctx: Annotated[InvocationContext, InjectContext()]):
            return ctx

        result = get_context_param(func)
        assert result == "ctx"

    def test_detects_plain_invocation_context(self):
        def func(ctx: InvocationContext):
            return ctx

        result = get_context_param(func)
        assert result == "ctx"

    def test_returns_none_when_no_context_param(self):
        def func(a: int, b: str):
            return a + b

        result = get_context_param(func)
        assert result is None

    def test_detects_param_with_different_name(self):
        def func(request_context: Annotated[InvocationContext, InjectContext()]):
            return request_context

        result = get_context_param(func)
        assert result == "request_context"

    def test_ignores_other_annotated_params(self):
        def func(
            a: Annotated[int, "some metadata"],
            ctx: Annotated[InvocationContext, InjectContext()],
        ):
            return a

        result = get_context_param(func)
        assert result == "ctx"

    def test_multiple_params_only_one_context(self):
        def func(
            x: int,
            ctx: InvocationContext,
            y: str,
        ):
            return x

        result = get_context_param(func)
        assert result == "ctx"


class TestInjectContext:
    def test_factory_returns_inject_context(self):
        marker = inject_context()
        assert isinstance(marker, InjectContext)

    def test_inject_context_is_distinct_instances(self):
        m1 = inject_context()
        m2 = inject_context()
        assert isinstance(m1, InjectContext)
        assert isinstance(m2, InjectContext)


# =============================================================================
# 5. Pipeline Tests
# =============================================================================


@pytest.mark.asyncio
class TestResolvePrincipal:
    async def test_returns_existing_principal_when_no_resolvers(self):
        principal = Principal(kind="user", id="123")
        ctx = InvocationContext(surface="rest", principal=principal)
        tool = ToolDefinition(func=lambda: None, name="test")

        result = await resolve_principal(tool, ctx, None)
        assert result is principal

    async def test_returns_none_when_no_principal_and_no_resolver(self):
        ctx = InvocationContext(surface="rest", principal=None)
        tool = ToolDefinition(func=lambda: None, name="test")

        result = await resolve_principal(tool, ctx, None)
        assert result is None

    async def test_uses_sync_resolver(self):
        def resolver(ctx):
            return Principal(kind="resolved", id="sync")

        ctx = InvocationContext(surface="rest")
        tool = ToolDefinition(func=lambda: None, name="test")

        result = await resolve_principal(tool, ctx, resolver)
        assert result.kind == "resolved"
        assert result.id == "sync"

    async def test_uses_async_resolver(self):
        async def resolver(ctx):
            return Principal(kind="resolved", id="async")

        ctx = InvocationContext(surface="rest")
        tool = ToolDefinition(func=lambda: None, name="test")

        result = await resolve_principal(tool, ctx, resolver)
        assert result.kind == "resolved"
        assert result.id == "async"

    async def test_tool_resolver_takes_priority(self):
        def tool_resolver(ctx):
            return Principal(kind="tool_resolver")

        def surface_resolver(ctx):
            return Principal(kind="surface_resolver")

        tool = ToolDefinition(
            func=lambda: None,
            name="test",
            surfaces={"rest": SurfaceSpec(principal_resolver=tool_resolver)},
        )
        ctx = InvocationContext(surface="rest")

        result = await resolve_principal(tool, ctx, surface_resolver)
        assert result.kind == "tool_resolver"


@pytest.mark.asyncio
class TestValidateAccess:
    async def test_no_policy_allows_all(self):
        ctx = InvocationContext(surface="rest")
        # Should not raise
        await validate_access(None, ctx)

    async def test_require_authenticated_with_authenticated_principal(self):
        policy = AccessPolicy(require_authenticated=True)
        principal = Principal(kind="user", is_authenticated=True)
        ctx = InvocationContext(surface="rest", principal=principal)

        # Should not raise
        await validate_access(policy, ctx)

    async def test_require_authenticated_without_principal_raises(self):
        policy = AccessPolicy(require_authenticated=True)
        ctx = InvocationContext(surface="rest", principal=None)

        with pytest.raises(PermissionError, match="Authentication required"):
            await validate_access(policy, ctx)

    async def test_require_authenticated_with_unauthenticated_principal_raises(self):
        policy = AccessPolicy(require_authenticated=True)
        principal = Principal(kind="user", is_authenticated=False)
        ctx = InvocationContext(surface="rest", principal=principal)

        with pytest.raises(PermissionError, match="Authentication required"):
            await validate_access(policy, ctx)

    async def test_no_anonymous_with_principal_ok(self):
        policy = AccessPolicy(allow_anonymous=False)
        principal = Principal(kind="user")
        ctx = InvocationContext(surface="rest", principal=principal)

        # Should not raise
        await validate_access(policy, ctx)

    async def test_no_anonymous_without_principal_raises(self):
        policy = AccessPolicy(allow_anonymous=False)
        ctx = InvocationContext(surface="rest", principal=None)

        with pytest.raises(PermissionError, match="Anonymous access not allowed"):
            await validate_access(policy, ctx)

    async def test_trusted_local_required_with_trusted_ok(self):
        policy = AccessPolicy(allow_trusted_local=True)
        principal = Principal(kind="user", is_trusted_local=True)
        ctx = InvocationContext(surface="rest", principal=principal)

        # Should not raise
        await validate_access(policy, ctx)

    async def test_trusted_local_required_without_trusted_raises(self):
        policy = AccessPolicy(allow_trusted_local=True)
        principal = Principal(kind="user", is_trusted_local=False)
        ctx = InvocationContext(surface="rest", principal=principal)

        with pytest.raises(PermissionError, match="Trusted local access required"):
            await validate_access(policy, ctx)

    async def test_trusted_local_denied_with_trusted_raises(self):
        policy = AccessPolicy(allow_trusted_local=False)
        principal = Principal(kind="user", is_trusted_local=True)
        ctx = InvocationContext(surface="rest", principal=principal)

        with pytest.raises(PermissionError, match="Trusted local access not allowed"):
            await validate_access(policy, ctx)

    async def test_required_claims_present_ok(self):
        policy = AccessPolicy(required_claims={"role": "admin"})
        principal = Principal(kind="user", claims={"role": "admin"})
        ctx = InvocationContext(surface="rest", principal=principal)

        # Should not raise
        await validate_access(policy, ctx)

    async def test_required_claims_missing_raises(self):
        policy = AccessPolicy(required_claims={"role": "admin"})
        principal = Principal(kind="user", claims={})
        ctx = InvocationContext(surface="rest", principal=principal)

        with pytest.raises(PermissionError, match="Missing required claim: role"):
            await validate_access(policy, ctx)

    async def test_required_claims_mismatch_raises(self):
        policy = AccessPolicy(required_claims={"role": "admin"})
        principal = Principal(kind="user", claims={"role": "user"})
        ctx = InvocationContext(surface="rest", principal=principal)

        with pytest.raises(PermissionError, match="Claim mismatch for: role"):
            await validate_access(policy, ctx)

    async def test_required_claims_without_principal_raises(self):
        policy = AccessPolicy(required_claims={"role": "admin"})
        ctx = InvocationContext(surface="rest", principal=None)

        with pytest.raises(
            PermissionError, match="Claims required but no principal present"
        ):
            await validate_access(policy, ctx)


@pytest.mark.asyncio
class TestDecodeArgs:
    async def test_applies_codec(self, mock_ctx):
        codecs = {"data": JsonObjectCodec()}
        raw_args = {"data": '{"key": "val"}'}

        result = decode_args(codecs, raw_args, mock_ctx)
        assert result == {"data": {"key": "val"}}

    async def test_no_codec_passes_through(self, mock_ctx):
        codecs = {}
        raw_args = {"data": '{"key": "val"}'}

        result = decode_args(codecs, raw_args, mock_ctx)
        assert result == {"data": '{"key": "val"}'}

    async def test_mixed_codecs_and_no_codecs(self, mock_ctx):
        codecs = {"numbers": CsvListCodec()}
        raw_args = {"numbers": "1,2,3", "name": "test"}

        result = decode_args(codecs, raw_args, mock_ctx)
        assert result == {"numbers": ["1", "2", "3"], "name": "test"}

    async def test_multiple_codecs(self, mock_ctx):
        codecs = {
            "items": CsvListCodec(),
            "config": JsonObjectCodec(),
        }
        raw_args = {
            "items": "a,b,c",
            "config": '{"enabled": true}',
        }

        result = decode_args(codecs, raw_args, mock_ctx)
        assert result == {
            "items": ["a", "b", "c"],
            "config": {"enabled": True},
        }


@pytest.mark.asyncio
class TestCallUserFunc:
    async def test_calls_sync_function(self, mock_ctx):
        def func(a: int, b: int):
            return a + b

        decoded_args = {"a": 1, "b": 2}
        result = await call_user_func(func, decoded_args, mock_ctx, None)
        assert result == 3

    async def test_calls_async_function(self, mock_ctx):
        async def func(a: int, b: int):
            return a * b

        decoded_args = {"a": 3, "b": 4}
        result = await call_user_func(func, decoded_args, mock_ctx, None)
        assert result == 12

    async def test_injects_context(self, mock_ctx):
        def func(a: int, ctx: InvocationContext):
            return f"{a}-{ctx.surface}"

        decoded_args = {"a": 1}
        result = await call_user_func(func, decoded_args, mock_ctx, "ctx")
        assert result == "1-rest"

    async def test_injects_context_to_async(self, mock_ctx):
        async def func(ctx: InvocationContext):
            return ctx.surface

        decoded_args = {}
        result = await call_user_func(func, decoded_args, mock_ctx, "ctx")
        assert result == "rest"


@pytest.mark.asyncio
class TestRenderResult:
    async def test_tool_level_renderer_priority(self, mock_ctx):
        tool_renderer = NoOpRenderer()
        spec_renderer = JsonRenderer()

        tool = ToolDefinition(
            func=lambda: None,
            name="test",
            renderer=tool_renderer,
            surfaces={"rest": SurfaceSpec(renderer=spec_renderer)},
        )

        # Tool-level renderer should be used
        result = render_result(tool, "value", mock_ctx, JsonRenderer())
        assert result == "value"  # NoOpRenderer returns unchanged

    async def test_surface_spec_renderer_second_priority(self, mock_ctx):
        spec_renderer = JsonRenderer()

        tool = ToolDefinition(
            func=lambda: None,
            name="test",
            surfaces={"rest": SurfaceSpec(renderer=spec_renderer)},
        )

        result = render_result(tool, {"key": "val"}, mock_ctx, NoOpRenderer())
        assert result == '{"key": "val"}'

    async def test_surface_default_renderer_third_priority(self, mock_ctx):
        tool = ToolDefinition(func=lambda: None, name="test")
        default_renderer = JsonRenderer()

        result = render_result(tool, {"key": "val"}, mock_ctx, default_renderer)
        assert result == '{"key": "val"}'

    async def test_noop_fallback_when_no_renderer(self, mock_ctx):
        tool = ToolDefinition(func=lambda: None, name="test")

        result = render_result(tool, "value", mock_ctx, None)
        assert result == "value"


@pytest.mark.asyncio
class TestInvokeTool:
    async def test_full_pipeline_sync_function(self):
        def add(a: int, b: int) -> int:
            return a + b

        tool = ToolDefinition(func=add, name="add")
        ctx = InvocationContext(surface="rest")

        result = await invoke_tool(tool, {"a": "1", "b": "2"}, ctx)
        # Note: args are not decoded by default (no codecs), so they're strings.
        # String concatenation confirms the pipeline called the function.
        assert result == "12"

    async def test_full_pipeline_with_codecs(self):
        def process(data: dict, items: list) -> dict:
            return {"data": data, "items": items}

        tool = ToolDefinition(
            func=process,
            name="process",
            codecs={
                "data": JsonObjectCodec(),
                "items": CsvListCodec(),
            },
        )
        ctx = InvocationContext(surface="rest")

        result = await invoke_tool(
            tool,
            {"data": '{"key": "val"}', "items": "a,b,c"},
            ctx,
        )
        assert result == {"data": {"key": "val"}, "items": ["a", "b", "c"]}

    async def test_full_pipeline_with_context_injection(self):
        def get_surface(ctx: InvocationContext) -> str:
            return ctx.surface

        tool = ToolDefinition(func=get_surface, name="get_surface")
        ctx = InvocationContext(surface="mcp")

        result = await invoke_tool(tool, {}, ctx, context_param_name="ctx")
        assert result == "mcp"

    async def test_full_pipeline_with_access_denied(self):
        def secret() -> str:
            return "secret"

        tool = ToolDefinition(
            func=secret,
            name="secret",
            access=AccessPolicy(require_authenticated=True),
        )
        ctx = InvocationContext(surface="rest", principal=None)

        with pytest.raises(PermissionError):
            await invoke_tool(tool, {}, ctx)

    async def test_full_pipeline_with_renderer(self):
        def get_data() -> dict:
            return {"status": "ok"}

        tool = ToolDefinition(
            func=get_data,
            name="get_data",
            renderer=JsonRenderer(),
        )
        ctx = InvocationContext(surface="rest")

        result = await invoke_tool(tool, {}, ctx)
        assert result == '{"status": "ok"}'


# =============================================================================
# 6. Integration Tests
# =============================================================================


@pytest.fixture
def runner():
    return CliRunner()


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


# =============================================================================
# 7. Additional Edge Case Tests
# =============================================================================


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


# =============================================================================
# 8. Pydantic Model Codec Tests
# =============================================================================


class TestPydanticModelCodec:
    def test_decode_json_string_to_pydantic_model(self, mock_ctx):
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
        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        instance = TestModel(name="test", value=42)
        result = codec.decode(instance, parameter_name="data", ctx=mock_ctx)
        assert result is instance

    def test_handles_none(self, mock_ctx):
        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        result = codec.decode(None, parameter_name="data", ctx=mock_ctx)
        assert result is None

    def test_raises_value_error_for_invalid_json(self, mock_ctx):
        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        with pytest.raises(ValueError):
            codec.decode("not valid json", parameter_name="data", ctx=mock_ctx)


class TestIsPydanticModel:
    def test_returns_true_for_pydantic_base_model_subclass(self):
        class TestModel(BaseModel):
            name: str
            value: int

        assert is_pydantic_model(TestModel) is True

    def test_returns_false_for_regular_types(self):
        assert is_pydantic_model(str) is False
        assert is_pydantic_model(int) is False
        assert is_pydantic_model(dict) is False
        assert is_pydantic_model(list) is False

    def test_returns_false_for_pydantic_model_instance(self, mock_ctx):
        class TestModel(BaseModel):
            name: str

        instance = TestModel(name="test")
        assert is_pydantic_model(instance) is False


class TestGetPydanticModelParams:
    def test_correctly_identifies_pydantic_model_parameters(self):
        class UserModel(BaseModel):
            name: str
            email: str

        class ConfigModel(BaseModel):
            timeout: int
            debug: bool

        def process_user(user: UserModel, config: ConfigModel, name: str) -> str:
            return name

        result = get_pydantic_model_params(process_user)
        assert result == {"user": UserModel, "config": ConfigModel}

    def test_returns_empty_dict_when_no_pydantic_params(self):
        def simple_func(a: int, b: str) -> str:
            return f"{a}-{b}"

        result = get_pydantic_model_params(simple_func)
        assert result == {}


class TestToolWithPydanticParamRest:
    def test_tool_with_pydantic_param_via_rest(self):
        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str
            age: int

        def create_user(user: UserInput) -> dict:
            return {"created": True, "name": user.name, "age": user.age}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=create_user, name="create_user")],
        )
        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        response = client.post(
            "/api/create_user", json={"name": "John", "age": 30}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["created"] is True
        assert result["name"] == "John"
        assert result["age"] == 30


class TestOpenAPISchemaWithPydanticModel:
    def test_openapi_spec_contains_pydantic_model_schema(self):
        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str = Field(description="The user's full name")
            age: int = Field(description="The user's age in years")

        def create_user(user: UserInput) -> dict:
            return {"created": True}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=create_user, name="create_user")],
        )
        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        spec = client.get("/api/openapi.json").json()

        schema = spec["components"]["schemas"].get("UserInput")
        assert schema is not None
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]

        name_schema = schema["properties"]["name"]
        assert name_schema["type"] == "string"
        assert "The user's full name" in str(name_schema.get("description", ""))


class TestToolWithPydanticParamCli:
    def test_tool_with_pydantic_param_via_cli(self, runner):
        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str
            age: int

        def create_user(user: UserInput) -> dict:
            return {"created": True, "name": user.name, "age": user.age}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=create_user, name="create_user")],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(
            mgr.cli, ["tools", "create_user", '{"name": "Alice", "age": 25}']
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["created"] is True
        assert parsed["name"] == "Alice"
        assert parsed["age"] == 25


class TestOptionalPydanticParam:
    def test_tool_with_optional_pydantic_param_via_rest(self):
        """Test Optional[pydantic model] parameter via REST server."""
        from typing import Optional
        from fastapi.testclient import TestClient

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str
            age: int

        def update_user(user: Optional[UserInput] = None) -> dict:
            if user is None:
                return {"updated": False}
            return {"updated": True, "name": user.name, "age": user.age}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=update_user, name="update_user", surfaces={"rest": SurfaceSpec(http_method="POST")})],
        )

        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)

        response = client.post("/api/update_user", json={"name": "Bob", "age": 30})
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] is True
        assert data["name"] == "Bob"
        assert data["age"] == 30

    def test_tool_with_optional_pydantic_param_via_rest_null(self):
        """Test Optional[pydantic model] parameter with null value via REST."""
        from typing import Optional
        from fastapi.testclient import TestClient

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str

        def update_user(user: Optional[UserInput] = None) -> dict:
            if user is None:
                return {"updated": False}
            return {"updated": True, "name": user.name}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=update_user, name="update_user", surfaces={"rest": SurfaceSpec(http_method="POST")})],
        )

        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)

        response = client.post("/api/update_user", json=None)
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] is False

    def test_tool_with_optional_pydantic_param_via_cli(self, runner):
        """Test Optional[pydantic model] parameter via CLI server."""
        from typing import Optional

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str
            age: int

        def update_user(user: UserInput) -> dict:
            return {"updated": True, "name": user.name, "age": user.age}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=update_user, name="update_user")],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(
            mgr.cli, ["tools", "update_user", '{"name": "Charlie", "age": 35}']
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["updated"] is True
        assert parsed["name"] == "Charlie"
        assert parsed["age"] == 35

    def test_tool_with_optional_pydantic_param_via_cli_null(self, runner):
        """Test Optional[pydantic model] parameter with null via CLI."""
        from typing import Optional

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str

        def update_user(user: Optional[UserInput] = None) -> dict:
            if user is None:
                return {"updated": False}
            return {"updated": True, "name": user.name}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=update_user, name="update_user")],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(mgr.cli, ["tools", "update_user"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["updated"] is False

    def test_tool_with_optional_pydantic_param_openapi_schema(self):
        """Test that Optional[pydantic model] shows in OpenAPI schema."""
        from typing import Optional

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str = Field(description="The user's name")
            age: int = Field(description="The user's age")

        def update_user(user: Optional[UserInput] = None) -> dict:
            return {"updated": user is not None}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=update_user, name="update_user", surfaces={"rest": SurfaceSpec(http_method="POST")})],
        )

        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        spec = client.get("/api/openapi.json").json()

        schema = spec["components"]["schemas"].get("UserInput")
        assert schema is not None
        assert "properties" in schema
        assert schema["properties"]["name"]["description"] == "The user's name"

    def test_get_pydantic_model_params_with_optional(self):
        """Test get_pydantic_model_params detects Optional pydantic models."""
        from typing import Optional
        from toolaccess.definition import get_pydantic_model_params

        class UserInput(BaseModel):
            name: str
            age: int

        def update_user(user: Optional[UserInput] = None) -> dict:
            pass

        params = get_pydantic_model_params(update_user)
        assert "user" in params
        assert params["user"] is UserInput

    def test_is_pydantic_model_with_optional(self):
        """Test is_pydantic_model detects Optional pydantic models."""
        from typing import Optional
        from toolaccess.definition import is_pydantic_model

        class UserInput(BaseModel):
            name: str

        assert is_pydantic_model(Optional[UserInput]) is True
        assert is_pydantic_model(UserInput) is True
        assert is_pydantic_model(Optional[str]) is False

