from __future__ import annotations

import inspect
from typing import Any, Callable

from .codecs import ArgumentCodec
from .context import (
    AccessPolicy,
    InvocationContext,
    Principal,
    PrincipalResolver,
    Surface,
)
from .definition import ToolDefinition, get_surface_spec
from .renderers import ResultRenderer, noop_renderer


async def resolve_principal(
    tool: ToolDefinition,
    ctx: InvocationContext,
    surface_resolver: PrincipalResolver | None = None,
) -> Principal | None:
    """Resolve the principal for this invocation.

    Priority:
    1. Tool's surface-specific principal_resolver
    2. Surface-provided resolver
    3. Existing ctx.principal
    """
    # Check tool's surface spec for a resolver
    surface_spec = get_surface_spec(tool, ctx.surface)
    if surface_spec.principal_resolver is not None:
        resolver = surface_spec.principal_resolver
        result = resolver(ctx)
        if inspect.isawaitable(result):
            result = await result
        return result

    # Fall back to surface-provided resolver
    if surface_resolver is not None:
        result = surface_resolver(ctx)
        if inspect.isawaitable(result):
            result = await result
        return result

    # Return existing principal (may be None)
    return ctx.principal


async def validate_access(
    access: AccessPolicy | None,
    ctx: InvocationContext,
) -> None:
    """Validate access policy against the invocation context.

    Raises:
        PermissionError: If any access requirement is not met.
    """
    if access is None:
        return  # No policy = allow all

    principal = ctx.principal

    # Check require_authenticated
    if access.require_authenticated:
        if principal is None or not principal.is_authenticated:
            raise PermissionError("Authentication required")

    # Check allow_anonymous
    if not access.allow_anonymous:
        if principal is None:
            raise PermissionError("Anonymous access not allowed")

    # Check allow_trusted_local
    if access.allow_trusted_local is not None:
        is_trusted = principal is not None and principal.is_trusted_local
        if access.allow_trusted_local and not is_trusted:
            raise PermissionError("Trusted local access required")
        if not access.allow_trusted_local and is_trusted:
            raise PermissionError("Trusted local access not allowed")

    # Check required_claims
    if access.required_claims:
        if principal is None:
            raise PermissionError("Claims required but no principal present")
        for claim_key, claim_value in access.required_claims.items():
            if claim_key not in principal.claims:
                raise PermissionError(f"Missing required claim: {claim_key}")
            if principal.claims[claim_key] != claim_value:
                raise PermissionError(f"Claim mismatch for: {claim_key}")


def decode_args(
    codecs: dict[str, ArgumentCodec],
    raw_args: dict[str, Any],
    ctx: InvocationContext,
) -> dict[str, Any]:
    """Decode raw arguments using configured codecs.

    Args:
        codecs: Mapping from arg name to codec.
        raw_args: Raw argument values from the surface.
        ctx: Invocation context.

    Returns:
        Decoded arguments dict.
    """
    decoded: dict[str, Any] = {}
    for key, value in raw_args.items():
        codec = codecs.get(key)
        if codec is not None:
            decoded[key] = codec.decode(value, parameter_name=key, ctx=ctx)
        else:
            decoded[key] = value
    return decoded


async def call_user_func(
    func: Callable,
    decoded_args: dict[str, Any],
    ctx: InvocationContext,
    context_param_name: str | None = None,
) -> Any:
    """Call the user function with decoded arguments.

    Args:
        func: The user function to call.
        decoded_args: Decoded argument values.
        ctx: Invocation context.
        context_param_name: If provided, inject ctx into decoded_args.

    Returns:
        Function result.
    """
    args = dict(decoded_args)
    if context_param_name is not None:
        args[context_param_name] = ctx

    result = func(**args)
    if inspect.isawaitable(result):
        result = await result
    return result


def render_result(
    tool: ToolDefinition,
    result: Any,
    ctx: InvocationContext,
    surface_default_renderer: ResultRenderer | None = None,
) -> Any:
    """Render the result using the appropriate renderer.

    Priority:
    1. Tool-level renderer
    2. Surface-spec renderer
    3. Surface default renderer
    4. noop_renderer
    """
    renderer: ResultRenderer | None = None

    # 1. Tool-level renderer
    if tool.renderer is not None:
        renderer = tool.renderer
    else:
        # 2. Surface-spec renderer
        surface_spec = get_surface_spec(tool, ctx.surface)
        if surface_spec.renderer is not None:
            renderer = surface_spec.renderer
        # 3. Surface default renderer
        elif surface_default_renderer is not None:
            renderer = surface_default_renderer

    # 4. Fallback to noop
    if renderer is None:
        renderer = noop_renderer

    return renderer.render(result, surface=ctx.surface, ctx=ctx)


async def invoke_tool(
    tool: ToolDefinition,
    raw_args: dict[str, Any],
    ctx: InvocationContext,
    context_param_name: str | None = None,
    surface_resolver: PrincipalResolver | None = None,
    surface_default_renderer: ResultRenderer | None = None,
) -> Any:
    """Main invocation pipeline for a tool.

    Steps:
    1. Resolve principal
    2. Validate access policy
    3. Decode arguments
    4. Call user function
    5. Render result

    Args:
        tool: Tool definition.
        raw_args: Raw arguments from the surface.
        ctx: Invocation context.
        context_param_name: Parameter name for context injection.
        surface_resolver: Surface-level principal resolver.
        surface_default_renderer: Surface-level default renderer.

    Returns:
        Rendered result.
    """
    # 1. Resolve principal
    ctx.principal = await resolve_principal(tool, ctx, surface_resolver)

    # 2. Validate access
    await validate_access(tool.access, ctx)

    # 3. Decode arguments
    decoded_args = decode_args(tool.codecs, raw_args, ctx)

    # 4. Call user function
    result = await call_user_func(tool.func, decoded_args, ctx, context_param_name)

    # 5. Render result
    return render_result(tool, result, ctx, surface_default_renderer)
