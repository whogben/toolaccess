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

Wraps a callable with metadata. If you pass a bare function to `ToolService`, one is created automatically using the function name and docstring. Use it explicitly when you need control over the HTTP method or name:

```python
ToolDefinition(func=add, name="add_numbers", http_method="POST", description="Sum two ints")
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

## Development

```bash
pip install -e ".[dev]"
pytest
```
