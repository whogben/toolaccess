# ToolAccess vNext Proposal

## Summary

`ToolAccess` already removes a lot of surface-level boilerplate for simple cases, but it stops one layer too early for real multi-surface applications.

In `gpdb-admin`, the business logic is already transport-agnostic. The remaining bulk in `entry.py` comes from generic adapter work that many ToolAccess users are likely to hit:

- resolving caller identity differently for REST, MCP, and CLI
- normalizing transport-shaped inputs into canonical Python values
- rendering results differently for CLI versus API surfaces
- repeating the same tool declaration three times because each surface injects different context objects

This proposal keeps application-specific auth and permissions in the app, while making ToolAccess better at the generic parts of multi-transport exposure.

## Current Friction

Today, ToolAccess mostly mounts the raw function directly:

- REST exposes the function as a FastAPI route
- MCP exposes the function as a FastMCP tool, with a narrow JSON-string wrapper
- CLI exposes the function as a Typer command

That is enough for stateless tools with primitive arguments, but it breaks down when the same operation needs:

- request identity
- transport-neutral authorization checks
- structured input coercion
- surface-specific output behavior

The result is that the application writes separate wrappers per surface even when the underlying operation is the same.

## Design Goals

1. Keep ToolAccess generic and application-agnostic.
2. Avoid pushing app-specific auth, RBAC, or domain types into ToolAccess.
3. Let applications define one canonical tool and expose it to multiple surfaces.
4. Make caller context, input normalization, and output rendering first-class.
5. Preserve the current "plain function, minimal boilerplate" path for simple users.

## Non-Goals

- ToolAccess should not own permission rules.
- ToolAccess should not require a specific auth model.
- ToolAccess should not force every tool to use framework-specific request objects.
- ToolAccess should not become a full policy engine.

## Proposed Additions

## 1. Invocation Context

Add a transport-neutral context object that can be injected into tools.

```python
from dataclasses import dataclass, field
from typing import Any, Literal

Surface = Literal["rest", "mcp", "cli"]


@dataclass
class Principal:
    kind: str
    id: str | None = None
    name: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)
    is_authenticated: bool = False
    is_trusted_local: bool = False


@dataclass
class InvocationContext:
    surface: Surface
    principal: Principal | None = None
    raw_request: Any | None = None
    raw_mcp_context: Any | None = None
    raw_cli_context: Any | None = None
    state: dict[str, Any] = field(default_factory=dict)
```

### Why this helps

Instead of writing:

- one REST wrapper that accepts `Request`
- one MCP wrapper that accepts `Context`
- one CLI wrapper that accepts nothing and hardcodes trust

an application can write one tool that accepts `InvocationContext`.

### Important property

The raw underlying transport objects can still be exposed when needed, but only as escape hatches. Most tools should never need them.

## 2. Per-Surface Principal Resolvers

ToolAccess should let each server resolve a principal generically.

```python
from typing import Awaitable, Callable

PrincipalResolver = Callable[[InvocationContext], Principal | Awaitable[Principal | None]]
```

Example server configuration:

```python
rest = OpenAPIServer(
    path_prefix="/api",
    title="Admin API",
    principal_resolver=resolve_rest_principal,
)

mcp = SSEMCPServer(
    "gpdb",
    principal_resolver=resolve_mcp_principal,
)

cli = CLIServer(
    "gpdb",
    principal_resolver=lambda ctx: Principal(
        kind="local_system",
        is_authenticated=True,
        is_trusted_local=True,
    ),
)
```

### Why this helps

ToolAccess stays generic. The application still decides how to authenticate. But once resolved, every tool gets the same shape.

## 3. Tool-Level Access Requirements

Tool definitions should be able to declare generic access expectations, with enforcement delegated through extensible hooks.

```python
@dataclass
class AccessPolicy:
    require_authenticated: bool = False
    allow_anonymous: bool = True
    allow_trusted_local: bool | None = None
    required_claims: dict[str, Any] = field(default_factory=dict)
```

```python
@dataclass
class ToolDefinition:
    func: Callable
    name: str
    http_method: HttpMethod = "POST"
    description: str | None = None
    include_surfaces: set[Surface] | None = None
    access: AccessPolicy | None = None
    codecs: dict[str, "ArgumentCodec"] = field(default_factory=dict)
    renderer: "ResultRenderer | None" = None
```

### Why this helps

This covers the common 80 percent:

- public tool
- authenticated tool
- trusted-local-only CLI tool
- caller must carry a particular claim

Applications with richer policy can still do final checks inside the service layer.

### Important boundary

This is not intended to replace domain authorization. It is a front-door contract for generic caller requirements.

## 4. First-Class Argument Codecs

ToolAccess currently has one MCP-only JSON-string workaround. Generalize that into reusable codecs available to all surfaces.

```python
class ArgumentCodec(Protocol):
    def decode(self, value: Any, *, parameter_name: str, ctx: InvocationContext) -> Any: ...
```

Built-in codecs could include:

- `JsonObjectCodec()`
- `JsonValueCodec()`
- `CsvListCodec(strip=True)`
- `Base64BytesCodec()`
- `IdentityCodec()`

Example:

```python
ToolDefinition(
    func=create_graph_node,
    name="graph_node_create",
    codecs={
        "data": JsonObjectCodec(),
        "tags": CsvListCodec(),
        "payload_base64": Base64BytesCodec(optional=True),
    },
)
```

### Why this helps

This removes generic parsing logic from app entrypoints while still letting the business function receive canonical Python values.

### Good rule for codecs

Codecs should be opt-in and per-parameter. ToolAccess should not try to infer too much magic from arbitrary type annotations.

## 5. First-Class Result Renderers

CLI often wants different behavior from REST and MCP:

- print pretty JSON
- flatten a Pydantic model
- render tables later
- suppress transport metadata

ToolAccess should let surfaces render a returned value without forcing app-specific wrapper functions.

```python
class ResultRenderer(Protocol):
    def render(self, value: Any, *, surface: Surface, ctx: InvocationContext) -> Any: ...
```

Built-in renderers could include:

- `JsonRenderer(indent=2, sort_keys=True)`
- `PydanticJsonRenderer(by_alias=False, indent=2, sort_keys=True)`
- `NoOpRenderer()`

Per-surface defaulting:

```python
cli = CLIServer("gpdb", default_renderer=PydanticJsonRenderer())
rest = OpenAPIServer("/api", title="API")
mcp = SSEMCPServer("gpdb")
```

Or per tool:

```python
ToolDefinition(
    func=get_graph_schema,
    name="graph_schema_get",
    renderer=PydanticJsonRenderer(by_alias=True),
)
```

### Why this helps

This removes wrappers whose only purpose is "call service, then `model_dump`, then print JSON".

## 6. One Tool, Multiple Surface Specs

ToolDefinition should support per-surface behavior declaratively instead of requiring separate wrapper functions for each server.

```python
@dataclass
class SurfaceSpec:
    enabled: bool = True
    http_method: HttpMethod | None = None
    renderer: ResultRenderer | None = None
    principal_resolver: PrincipalResolver | None = None


@dataclass
class ToolDefinition:
    func: Callable
    name: str
    description: str | None = None
    surfaces: dict[Surface, SurfaceSpec] = field(default_factory=dict)
    access: AccessPolicy | None = None
    codecs: dict[str, ArgumentCodec] = field(default_factory=dict)
```

Example:

```python
ToolDefinition(
    func=graph_overview,
    name="graph_overview",
    surfaces={
        "rest": SurfaceSpec(http_method="GET"),
        "mcp": SurfaceSpec(),
        "cli": SurfaceSpec(renderer=PydanticJsonRenderer()),
    },
    access=AccessPolicy(require_authenticated=True),
)
```

### Why this helps

Applications should not need:

- `_build_rest_graph_content_tools(...)`
- `_build_cli_graph_content_tools(...)`
- `_build_mcp_graph_content_tools(...)`

when the actual tool contract is the same.

## 7. A Context Injection Marker

ToolAccess should define an explicit way to request `InvocationContext` injection.

Two reasonable options:

### Option A: annotation-based

```python
async def graph_overview(graph_id: str, ctx: InvocationContext) -> GraphOverview:
    ...
```

### Option B: helper marker

```python
from typing import Annotated

async def graph_overview(
    graph_id: str,
    ctx: Annotated[InvocationContext, InjectContext()],
) -> GraphOverview:
    ...
```

I prefer the marker-based approach because it is future-proof if ToolAccess later injects more framework-owned values.

## Suggested API Shape

The simplest user-facing layer would be a decorator API on top of `ToolDefinition`.

```python
tools = ToolService("admin")


@tools.tool(
    name="graph_node_create",
    surfaces={
        "rest": {"http_method": "POST"},
        "mcp": {},
        "cli": {"renderer": PydanticJsonRenderer(by_alias=True)},
    },
    access={"require_authenticated": True},
    codecs={
        "data": JsonObjectCodec(),
        "tags": CsvListCodec(),
        "payload_base64": Base64BytesCodec(optional=True),
    },
)
async def graph_node_create(
    graph_id: str,
    type: str,
    data: dict[str, object],
    name: str = "",
    schema_name: str = "",
    owner_id: str = "",
    parent_id: str = "",
    tags: list[str] | None = None,
    payload_base64: bytes | None = None,
    payload_mime: str = "",
    payload_filename: str = "",
    ctx: InvocationContext = inject_context(),
) -> GraphNodeDetail:
    return await graph_content.create_graph_node(
        graph_id=graph_id,
        type=type,
        data=data,
        name=name,
        schema_name=schema_name,
        owner_id=owner_id,
        parent_id=parent_id,
        tags=tags or [],
        payload=payload_base64,
        payload_mime=payload_mime,
        payload_filename=payload_filename,
        current_user=require_current_user(ctx),
        allow_local_system=is_trusted_local(ctx),
    )
```

This keeps business logic in app code, but collapses transport setup into data.

## gpdb Before/After Sketch

## Current Shape

In gpdb, a single domain operation like `get_graph_overview` currently needs:

- one REST wrapper
- one MCP wrapper
- one CLI wrapper
- REST middleware to attach user state
- MCP-specific token plumbing
- CLI-specific JSON emission
- helper coercion functions

The duplication is structural rather than domain-driven.

## Desired Shape

With the proposed ToolAccess features, gpdb could write something conceptually closer to:

```python
graph_tools = ToolService("admin-graph")


@graph_tools.tool(
    name="graph_overview",
    surfaces={
        "rest": {"http_method": "GET"},
        "mcp": {},
        "cli": {"renderer": PydanticJsonRenderer()},
    },
    access={"require_authenticated": True},
)
async def graph_overview(graph_id: str, ctx: InvocationContext) -> GraphOverview:
    return await graph_content.get_graph_overview(
        graph_id=graph_id,
        current_user=current_user_or_none(ctx),
        allow_local_system=is_trusted_local(ctx),
    )
```

That is a real reduction in entrypoint code while still keeping gpdb-specific authorization inside gpdb.

## Backward Compatibility

This should be additive.

### Existing simple usage should keep working

This should remain valid:

```python
service = ToolService("math", [add, greet])
```

### Existing servers should keep working

These constructors should continue to work without new options:

```python
OpenAPIServer(path_prefix="/api", title="API")
SSEMCPServer("default")
CLIServer("mycli")
```

### Migration path

1. Introduce `InvocationContext`, principal resolvers, codecs, and renderers as opt-in features.
2. Add decorator sugar for new users.
3. Keep `ToolDefinition(func=..., name=...)` fully supported.
4. Internally, have all surfaces use the same invocation pipeline:
   - build invocation context
   - resolve principal
   - validate access policy
   - decode arguments
   - call tool
   - render result

## Internal Pipeline Recommendation

ToolAccess should centralize invocation flow rather than letting each server do its own ad hoc wrapping.

Suggested internal pipeline:

```python
async def invoke_tool(tool: ToolDefinition, raw_args: dict[str, Any], ctx: InvocationContext):
    ctx.principal = await resolve_principal(tool, ctx)
    await validate_access(tool.access, ctx)
    decoded_args = decode_args(tool.codecs, raw_args, ctx)
    result = await call_user_func(tool.func, decoded_args, ctx)
    return render_result(tool, result, ctx)
```

If ToolAccess implements this once, every surface inherits the same behavior.

## Nice-to-Have Extensions

These are useful, but lower priority than the items above.

### Error mapping

Let tools or servers register exception mappers so common exceptions can be presented consistently across REST, CLI, and MCP.

### Surface-specific visibility

Some tools should exist only on CLI or only on MCP without needing separate service objects.

### Shared docs metadata

Allow per-tool tags, summaries, and examples that feed OpenAPI and future CLI/MCP help output.

## Why This Is Broadly Useful

This is not a gpdb-specific ask.

Many multi-surface tools eventually need:

- authenticated REST calls
- authenticated MCP calls
- trusted-local CLI commands
- normalization of JSON-ish input
- nicer CLI output

Those are normal concerns for internal admin services, AI tool servers, and developer tooling.

## Bottom Line

ToolAccess already solves "mount the same callable in multiple places."

The next generically useful step is to solve "invoke the same tool coherently across multiple surfaces."

That means first-class support for:

- a unified invocation context
- pluggable principal resolution
- generic access requirements
- reusable argument codecs
- reusable result renderers
- declarative per-surface exposure

If ToolAccess grows in that direction, applications like gpdb can keep their business logic and authorization local while deleting a large amount of repetitive entrypoint code.
