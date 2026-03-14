"""Tests for the tool invocation pipeline."""

import pytest

from toolaccess import (
    AccessPolicy,
    InvocationContext,
    JsonRenderer,
    NoOpRenderer,
    Principal,
    SurfaceSpec,
    ToolDefinition,
    invoke_tool,
)
from toolaccess.codecs import CsvListCodec, JsonObjectCodec
from toolaccess.pipeline import (
    call_user_func,
    decode_args,
    render_result,
    resolve_principal,
    validate_access,
)


@pytest.fixture
def mock_ctx():
    return InvocationContext(surface="rest")


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
