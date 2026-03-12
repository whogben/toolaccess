# toolaccess

Define your Python functions once, expose them as **REST APIs**, **MCP servers**, and **CLI commands** — simultaneously, with zero boilerplate duplication.

## When to use this

You have Python functions that need to be callable from more than one interface. Common scenarios:

- **AI/LLM tool servers** — you want the same tools available over MCP (for agents) and REST (for web apps) and CLI (for local testing).
- **Internal tooling** — a set of utility functions your team invokes from scripts, HTTP clients, and AI assistants.
- **Rapid prototyping** — skip the plumbing and get a working API + MCP server + CLI in minutes.

Without `toolaccess` you'd write separate FastAPI routes, a FastMCP server, and Typer commands that all call the same underlying code. This library removes that duplication.

## Install

```bash
pip install toolaccess
```

Or from source:

```bash
pip install -e .
```

## Quick start

```python
from toolaccess import (
    ServerManager,
    ToolService,
    ToolDefinition,
    OpenAPIServer,
    StreamableHTTPMCPServer,
    CLIServer,
)

# 1. Write plain functions (sync or async)
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

async def greet(name: str) -> str:
    """Return a greeting."""
    return f"Hello, {name}!"

# 2. Group them into a service
service = ToolService("math", [add, greet])

# 3. Create servers and mount the service
rest = OpenAPIServer(path_prefix="/api", title="Math API")
rest.mount(service)

mcp = StreamableHTTPMCPServer("math")
mcp.mount(service)

cli = CLIServer("math")
cli.mount(service)

# 4. Wire everything into the manager
manager = ServerManager(name="my-tools")
manager.add_server(rest)
manager.add_server(mcp)
manager.add_server(cli)

# 5. Run
manager.run()
```

That single file gives you:

| Interface | Access |
|---|---|
| REST API | `POST /api/add`, `POST /api/greet` |
| OpenAPI docs | `GET /api/docs` |
| MCP (StreamableHTTP) | `http://localhost:8000/mcp/math/mcp` |
| MCP (stdio) | `python app.py mcp-run --name math` |
| CLI | `python app.py math add 1 2` |
| Health check | `GET /health` |

### Starting the HTTP server

```bash
python app.py start                        # default 127.0.0.1:8000
python app.py start --host 0.0.0.0 --port 9000
```

### Running MCP over stdio

```bash
python app.py mcp-run --name math
```

Use this when connecting from Claude Desktop, Cursor, or any MCP client that expects a stdio transport.

## Core concepts

### ToolDefinition

Wraps a callable with metadata. If you pass a bare function to `ToolService`, one is created automatically using the function name and docstring. Use it explicitly when you need control over the name, description, or per-surface behavior:

```python
from toolaccess import ToolDefinition, SurfaceSpec

ToolDefinition(
    func=add,
    name="add_numbers",
    description="Sum two ints",
    surfaces={"rest": SurfaceSpec(http_method="POST")},
)
```

### ToolService

A named group of tools. Mount the same service onto multiple servers to keep them in sync:

```python
service = ToolService("admin", [check_health, restart_worker])
```

### Servers

| Class | Protocol | Notes |
|---|---|---|
| `OpenAPIServer` | HTTP / REST | Backed by FastAPI. Set `path_prefix` to namespace routes. |
| `StreamableHTTPMCPServer` | MCP (StreamableHTTP + stdio) | Backed by FastMCP. Mounted at `/mcp/{name}/mcp`. |
| `CLIServer` | CLI | Backed by Typer. Async functions are handled automatically. |

### ServerManager

The runtime host. It owns a FastAPI app, a Typer CLI, and a dynamic ASGI dispatcher that routes requests to the correct sub-app by path prefix.

Servers can be added and removed at runtime:

```python
manager.add_server(new_api)    # immediately routable
manager.remove_server(new_api) # immediately gone
```

## Multiple isolated groups

You can create separate servers for different audiences and mount different services onto each:

```python
public_api = OpenAPIServer("/public", "Public API")
public_api.mount(public_service)

admin_api = OpenAPIServer("/admin", "Admin API")
admin_api.mount(admin_service)

manager.add_server(public_api)
manager.add_server(admin_api)
```

The same pattern works for MCP — create multiple `StreamableHTTPMCPServer` instances with different names.

## Lifespan support

Pass an async context manager to `ServerManager` to run setup/teardown logic (database connections, model loading, etc.). The lifespan is entered for both the HTTP server and CLI command execution:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    db = await connect_db()
    yield
    await db.close()

manager = ServerManager(name="my-service", lifespan=lifespan)
```

## Mounting custom ASGI apps

Use `MountableApp` to add an existing FastAPI or ASGI application alongside your tool servers:

```python
from toolaccess.toolaccess import MountableApp
from fastapi import FastAPI

dashboard = FastAPI()

@dashboard.get("/")
async def index():
    return {"page": "dashboard"}

manager.add_server(MountableApp(dashboard, path_prefix="/dashboard", name="dashboard"))
```

## Requirements

- Python >= 3.10
- fastapi
- fastmcp
- pydantic
- typer
- uvicorn

## Advanced Features

ToolAccess introduces powerful capabilities for building sophisticated multi-interface tools with fine-grained control over behavior per surface.

### Decorator API

Register tools using the `@service.tool()` decorator instead of passing functions to `ToolService`:

```python
from toolaccess import ToolService, OpenAPIServer, ServerManager

service = ToolService("math")

@service.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@service.tool(name="multiply", description="Product of two numbers")
def mul(x: int, y: int) -> int:
    return x * y

# Create servers and run as before
api = OpenAPIServer("/api", "Math API")
api.mount(service)

manager = ServerManager("math-server")
manager.add_server(api)
manager.run()
```

### InvocationContext and Principal

Access transport-neutral context about the current invocation:

```python
from typing import Annotated
from toolaccess import InvocationContext, inject_context

@service.tool()
def whoami(ctx: Annotated[InvocationContext, inject_context()]) -> dict:
    """Return information about the current invocation."""
    return {
        "surface": ctx.surface,  # "rest", "mcp", or "cli"
        "principal_kind": ctx.principal.kind if ctx.principal else None,
        "principal_id": ctx.principal.id if ctx.principal else None,
    }
```

The `InvocationContext` provides:

| Field | Description |
|-------|-------------|
| `surface` | Which surface invoked the tool: `"rest"`, `"mcp"`, or `"cli"` |
| `principal` | The authenticated [`Principal`](src/toolaccess/context.py:19) (or None) |
| `raw_request` | Original HTTP request object (REST only) |
| `raw_mcp_context` | Original MCP context (MCP only) |
| `state` | Mutable dict for sharing data across the request lifecycle |

The `Principal` object contains:

| Field | Description |
|-------|-------------|
| `kind` | Type of principal (e.g., `"user"`, `"service"`, `"anonymous"`) |
| `id` | Unique identifier |
| `name` | Human-readable name |
| `claims` | Dictionary of authorization claims |
| `is_authenticated` | Whether the principal is authenticated |
| `is_trusted_local` | Whether this is a trusted local call |

### Access Control

Require authentication or specific claims using [`AccessPolicy`](src/toolaccess/context.py:39):

```python
from toolaccess import AccessPolicy

# Require authentication
@service.tool(access=AccessPolicy(require_authenticated=True))
def admin_only() -> str:
    return "sensitive data"

# Require specific claims
@service.tool(
    access=AccessPolicy(
        require_authenticated=True,
        required_claims={"role": "admin"}
    )
)
def super_admin() -> str:
    return "super secret"

# Disallow anonymous access but allow trusted local
@service.tool(
    access=AccessPolicy(
        allow_anonymous=False,
        allow_trusted_local=True,
    )
)
def local_only() -> str:
    return "local development data"
```

### Security and Principal resolvers

For secure deployments you should configure a `PrincipalResolver` for each surface so that `AccessPolicy` checks have the right principal information to work with. For example, resolving a user from an HTTP header:

```python
from toolaccess import Principal, PrincipalResolver, InvocationContext

def rest_principal_resolver(ctx: InvocationContext) -> Principal | None:
    request = ctx.raw_request
    if request is None:
        return None
    user_id = request.headers.get("X-User-Id")
    if not user_id:
        return None
    return Principal(kind="user", id=user_id, is_authenticated=True)
```

You can then pass this resolver into `OpenAPIServer` and other servers so that tools using `AccessPolicy` are correctly protected.

### Argument Codecs

Control how arguments are decoded from wire format using codecs:

```python
from toolaccess.codecs import JsonObjectCodec, CsvListCodec, Base64BytesCodec

@service.tool(
    codecs={
        "config": JsonObjectCodec(),      # Parse JSON string to dict
        "tags": CsvListCodec(),            # Parse "a,b,c" to ["a", "b", "c"]
        "data": Base64BytesCodec(),        # Decode base64 to bytes
    }
)
def process(config: dict, tags: list, data: bytes) -> dict:
    return {"config": config, "tags": tags, "data_size": len(data)}
```

Available codecs:

| Codec | Purpose |
|-------|---------|
| `IdentityCodec` | Pass value through unchanged (default) |
| `JsonObjectCodec` | Parse JSON string to Python dict |
| `JsonValueCodec` | Parse any JSON value (int, str, list, etc.) |
| `CsvListCodec` | Parse comma-separated values to list |
| `Base64BytesCodec` | Decode base64 strings to bytes |

Use the singleton instances for convenience: `json_object_codec`, `csv_list_codec`, etc.

### Result Renderers

Customize CLI output format using renderers:

```python
from toolaccess import JsonRenderer, PydanticJsonRenderer
from pydantic import BaseModel

class User(BaseModel):
    name: str
    email: str

@service.tool(renderer=PydanticJsonRenderer(indent=2))
def get_user() -> User:
    return User(name="Alice", email="alice@example.com")

# CLI output will be pretty-printed JSON
```

Available renderers:

| Renderer | Purpose |
|----------|---------|
| `NoOpRenderer` | Return value unchanged (default) |
| `JsonRenderer` | Serialize to JSON with optional indentation |
| `PydanticJsonRenderer` | Serialize Pydantic models with `model_dump()` |

### Per-Surface Configuration

Configure different behavior for each surface using [`SurfaceSpec`](src/toolaccess/context.py:47):

```python
from toolaccess import SurfaceSpec, HttpMethod, JsonRenderer

@service.tool(
    surfaces={
        "rest": SurfaceSpec(
            http_method="GET",           # Expose as GET endpoint
        ),
        "cli": SurfaceSpec(
            renderer=JsonRenderer(indent=2),  # Pretty-print CLI output
        ),
        "mcp": SurfaceSpec(
            enabled=True,                # Available via MCP (default)
        ),
    }
)
def list_items() -> list:
    return ["item1", "item2", "item3"]

# Disable a tool on specific surfaces
@service.tool(
    surfaces={
        "rest": SurfaceSpec(enabled=False),  # Not available via REST
    }
)
def internal_tool() -> str:
    return "internal"
```

### Complete Example

Here's a comprehensive example combining all features:

```python
from typing import Annotated
from toolaccess import (
    ServerManager,
    ToolService,
    OpenAPIServer,
    StreamableHTTPMCPServer,
    CLIServer,
    InvocationContext,
    AccessPolicy,
    SurfaceSpec,
    JsonRenderer,
    inject_context,
)
from toolaccess.codecs import JsonObjectCodec, CsvListCodec
from pydantic import BaseModel

# Define models
class Task(BaseModel):
    id: int
    title: str
    tags: list[str]

# Create service
service = ToolService("tasks")

# Public read-only endpoint
@service.tool(
    surfaces={
        "rest": SurfaceSpec(http_method="GET"),
        "cli": SurfaceSpec(renderer=JsonRenderer(indent=2)),
    }
)
def list_tasks(
    ctx: Annotated[InvocationContext, inject_context()]
) -> list[Task]:
    """List all tasks."""
    print(f"Called from {ctx.surface}")
    return [
        Task(id=1, title="Buy milk", tags=["shopping"]),
        Task(id=2, title="Write code", tags=["work", "coding"]),
    ]

# Protected endpoint with custom codecs
@service.tool(
    access=AccessPolicy(require_authenticated=True),
    codecs={
        "metadata": JsonObjectCodec(),
        "tags": CsvListCodec(),
    },
)
def create_task(
    title: str,
    metadata: dict,
    tags: list[str],
    ctx: Annotated[InvocationContext, inject_context()]
) -> Task:
    """Create a new task (requires authentication)."""
    if ctx.principal:
        print(f"Created by {ctx.principal.name}")
    return Task(id=3, title=title, tags=tags)

# Admin-only endpoint
@service.tool(
    access=AccessPolicy(
        require_authenticated=True,
        required_claims={"role": "admin"}
    ),
    surfaces={
        "rest": SurfaceSpec(enabled=False),  # CLI/MCP only
    }
)
def delete_all_tasks() -> str:
    """Delete all tasks (admin only, not available via REST)."""
    return "All tasks deleted"

# Set up servers
api = OpenAPIServer("/api", "Task API")
api.mount(service)

mcp = StreamableHTTPMCPServer("tasks")
mcp.mount(service)

cli = CLIServer("tasks")
cli.mount(service)

manager = ServerManager("task-server")
manager.add_server(api)
manager.add_server(mcp)
manager.add_server(cli)

if __name__ == "__main__":
    manager.run()
```

## Development

```bash
pip install -e ".[dev]"
pytest
```
