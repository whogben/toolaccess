import inspect
import asyncio
import json
import logging
from functools import wraps
from typing import Any, Callable, Literal, Union, get_origin, get_args

import typer
import uvicorn
from fastapi import FastAPI
from fastmcp import FastMCP
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from starlette.types import Scope, Receive, Send
from starlette.responses import Response

"""
Generic Tool Server Utility (Polymorphic Server Model)
Provides reusable components for exposing Python functions as tools via
CLI, OpenAPI (REST), and MCP (SSE/Stdio).
"""

logger = logging.getLogger(__name__)

# --- 1. Tool Definition & Service ---

HttpMethod = Literal["GET", "POST", "PUT", "DELETE", "PATCH"]


@dataclass
class ToolDefinition:
    """Metadata for a single tool function."""

    func: Callable
    name: str
    http_method: HttpMethod = "POST"
    description: str | None = None

    def __post_init__(self):
        if self.description is None and self.func.__doc__:
            self.description = inspect.cleandoc(self.func.__doc__)


class ToolService:
    """A collection of tools to be exposed together."""

    def __init__(self, name: str, tools: list[Callable | ToolDefinition]):
        self.name = name
        self.tools: list[ToolDefinition] = []
        for t in tools:
            if isinstance(t, ToolDefinition):
                self.tools.append(t)
            else:
                self.tools.append(ToolDefinition(func=t, name=t.__name__))


# --- 2. Abstract Base Server ---


class BaseServer(ABC):
    """Abstract base for any server interface."""

    @abstractmethod
    def mount(self, service: ToolService):
        """Add a service's tools to this server."""
        pass

    @abstractmethod
    def register_to(self, manager: "ServerManager"):
        """Hook this server into the runtime manager."""
        pass


# --- 3. Concrete Servers ---

METHOD_ROUTERS: dict[str, Callable] = {
    "GET": FastAPI.get,
    "POST": FastAPI.post,
    "PUT": FastAPI.put,
    "DELETE": FastAPI.delete,
    "PATCH": FastAPI.patch,
}


class OpenAPIServer(BaseServer):
    """Exposes tools via FastAPI sub-application."""

    def __init__(self, path_prefix: str = "", title: str = "API"):
        self.path_prefix = path_prefix
        self.app = FastAPI(title=title)

    def mount(self, service: ToolService):
        for tool in service.tools:
            self._add_route(tool)

    def _add_route(self, tool: ToolDefinition):
        router = METHOD_ROUTERS.get(tool.http_method, FastAPI.post)
        router(self.app, f"/{tool.name}", name=tool.name, description=tool.description)(
            tool.func
        )

    def register_to(self, manager: "ServerManager"):
        pass


class SSEMCPServer(BaseServer):
    """Exposes tools via FastMCP (SSE & Stdio capability)."""

    def __init__(self, name: str = "default"):
        self.name = name
        self.mcp = FastMCP(name)

    def mount(self, service: ToolService):
        for tool in service.tools:
            # Wrap function to handle JSON string arguments from MCP clients
            wrapped_func = _wrap_for_mcp(tool.func)
            self.mcp.tool(wrapped_func, name=tool.name, description=tool.description)

    def register_to(self, manager: "ServerManager"):
        manager.mcp_servers[self.name] = self.mcp


class CLIServer(BaseServer):
    """Exposes tools via Typer CLI."""

    def __init__(self, name: str | None = None):
        self.name = name
        self.typer_app = typer.Typer(name=name) if name else typer.Typer()
        self.manager: "ServerManager" | None = None

    def mount(self, service: ToolService):
        for tool in service.tools:
            self._add_command(self.typer_app, tool)

    def _add_command(self, app: typer.Typer, tool: ToolDefinition):
        func = tool.func
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            def wrapper(*args, **kwargs):
                async def runner():
                    if self.manager and self.manager.lifespan_ctx:
                        async with self.manager.lifespan_ctx(self.manager.app):
                            return await func(*args, **kwargs)
                    else:
                        return await func(*args, **kwargs)

                try:
                    return asyncio.run(runner())
                except KeyboardInterrupt:
                    return None

            wrapper.__signature__ = inspect.signature(func)
            cli_func = wrapper
        else:
            cli_func = func
        app.command(name=tool.name, help=tool.description)(cli_func)

    def register_to(self, manager: "ServerManager"):
        self.manager = manager
        manager.cli.add_typer(self.typer_app, name=self.name)


class MountableApp(BaseServer):
    """
    Wraps an existing FastAPI/ASGI application to be mounted by the ServerManager.
    Useful for custom web interfaces, separate API sub-apps, or static file servers.
    """

    def __init__(self, app: FastAPI, path_prefix: str = "", name: str = "app"):
        self.app = app
        self.path_prefix = path_prefix
        self.name = name

    def mount(self, service: ToolService):
        """
        MountableApp generally doesn't accept ToolServices, as its routes
        are defined internally. We can leave this empty or log a warning.
        """
        pass

    def register_to(self, manager: "ServerManager"):
        # No special registration needed, the Dispatcher handles it
        pass


# --- 4. Server Manager (Runtime) ---


class DynamicDispatcher:
    """ASGI Application that dynamically dispatches requests to sub-apps."""

    def __init__(self, manager: "ServerManager"):
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await Response("Not Found", status_code=404)(scope, receive, send)
            return

        path = scope["path"]
        logger.debug(f"Dispatching path={path}")

        # Collect matches (server, prefix_length)
        matches = []
        for server in self.manager.active_servers.values():
            if isinstance(server, OpenAPIServer):
                prefix = server.path_prefix.strip("/")
                if not prefix:
                    continue
                check_prefix = f"/{prefix}"
                if path.startswith(check_prefix):
                    remaining = path[len(check_prefix) :]
                    if not remaining or remaining.startswith("/"):
                        matches.append((server, len(check_prefix)))

            elif isinstance(server, SSEMCPServer):
                prefix = f"/mcp/{server.name}"
                if path.startswith(prefix):
                    remaining = path[len(prefix) :]
                    if not remaining or remaining.startswith("/"):
                        matches.append((server, len(prefix)))

            elif isinstance(server, MountableApp):
                prefix = server.path_prefix.strip("/")
                check_prefix = f"/{prefix}" if prefix else "/"
                if path.startswith(check_prefix):
                    # Special handling for root
                    if check_prefix == "/":
                        matches.append((server, 1))
                    else:
                        remaining = path[len(check_prefix) :]
                        if not remaining or remaining.startswith("/"):
                            matches.append((server, len(check_prefix)))

        if not matches:
            logger.debug("No match found")
            await Response("Not Found", status_code=404)(scope, receive, send)
            return

        # Sort by specificity (longest prefix wins)
        matches.sort(key=lambda x: x[1], reverse=True)
        server, prefix_len = matches[0]

        # Dispatch
        if isinstance(server, OpenAPIServer):
            prefix = server.path_prefix.strip("/")
            check_prefix = f"/{prefix}"
            scope["root_path"] = scope.get("root_path", "") + check_prefix
            scope["path"] = path[prefix_len:] or "/"
            await server.app(scope, receive, send)

        elif isinstance(server, SSEMCPServer):
            prefix = f"/mcp/{server.name}"
            scope["root_path"] = scope.get("root_path", "") + prefix
            scope["path"] = path[prefix_len:] or "/"
            await server.mcp.http_app(transport="sse")(scope, receive, send)

        elif isinstance(server, MountableApp):
            prefix = server.path_prefix.strip("/")
            check_prefix = f"/{prefix}" if prefix else ""
            if check_prefix == "/":
                check_prefix = ""  # Don't add trailing slash to root path

            scope["root_path"] = scope.get("root_path", "") + check_prefix
            scope["path"] = path[prefix_len:] if prefix_len > 1 else path
            if not scope["path"]:
                scope["path"] = "/"
            await server.app(scope, receive, send)


class ServerManager:
    """The runtime host that manages all servers."""

    def __init__(self, name: str = "service", lifespan: Callable | None = None):
        self.name = name
        self.lifespan_ctx = lifespan
        self.app = FastAPI(title=name, lifespan=lifespan)
        self.cli = typer.Typer(name=name)
        self.mcp_servers: dict[str, FastMCP] = {}
        self.active_servers: dict[str, BaseServer] = {}
        self._add_infrastructure()
        self.app.mount("/", DynamicDispatcher(self))

    def add_server(self, server: BaseServer):
        """Register a polymorphic server instance."""
        self.active_servers[str(id(server))] = server
        server.register_to(self)

    def remove_server(self, server: BaseServer):
        """Unregister a server instance."""
        server_id = str(id(server))
        if server_id in self.active_servers:
            del self.active_servers[server_id]
            if isinstance(server, SSEMCPServer) and server.name in self.mcp_servers:
                del self.mcp_servers[server.name]

    def _add_infrastructure(self):
        @self.app.get("/health")
        async def health():
            """Health check endpoint listing all MCP servers."""
            return {"mcp_servers": list(self.mcp_servers.keys())}

        @self.cli.command()
        def start(host: str = "127.0.0.1", port: int = 8000):
            """Start the server (REST + MCP SSE)."""
            print(f"🚀 {self.name} Server Starting...")
            print(f"---------------------------------------------------")
            print(f"📋 OpenAPI:           http://{host}:{port}/docs")
            for mcp_name in self.mcp_servers:
                print(f"🤖 MCP Server:        http://{host}:{port}/mcp/{mcp_name}/sse")

            # Print URLs for MountableApps
            for server in self.active_servers.values():
                if isinstance(server, MountableApp):
                    prefix = server.path_prefix if server.path_prefix else "/"
                    print(f"🌐 Web App ({server.name}): http://{host}:{port}{prefix}")

            print(f"---------------------------------------------------")
            uvicorn.run(self.app, host=host, port=port)

        @self.cli.command()
        def mcp_run(name: str = "default"):
            """Run an MCP server via Stdio."""
            if name not in self.mcp_servers:
                print(
                    f"❌ MCP Server '{name}' not found. Available: {list(self.mcp_servers.keys())}"
                )
                return

            async def run_stdio():
                if self.lifespan_ctx:
                    async with self.lifespan_ctx(self.app):
                        await self.mcp_servers[name].run_stdio_async()
                else:
                    await self.mcp_servers[name].run_stdio_async()

            try:
                asyncio.run(run_stdio())
            except KeyboardInterrupt:
                return
            except Exception as e:
                print(f"Error running MCP stdio: {e}")
                self.mcp_servers[name].run(transport="stdio")

    def run(self):
        """Entry point for the CLI."""
        self.cli()


# --- MISC UTILS ---


def _wrap_for_mcp(func: Callable) -> Callable:
    """
    Wrap a function to pre-process arguments for MCP compatibility.

    This ensures JSON strings are parsed into proper dicts before
    the function is called, handling clients that serialize nested
    objects as strings. It inspects the function signature to avoid
    parsing arguments that are explicitly typed as strings.
    """
    sig = inspect.signature(func)

    def process_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        new_kwargs = {}
        for k, v in kwargs.items():
            # If the value is NOT a string, keep it as is
            if not isinstance(v, str):
                new_kwargs[k] = v
                continue

            # Check parameter type hint
            param = sig.parameters.get(k)
            should_skip = False

            if param:
                annotation = param.annotation
                if annotation is str:
                    should_skip = True
                else:
                    # Handle Optional[str] -> Union[str, None]
                    origin = get_origin(annotation)
                    if origin is Union:
                        args = get_args(annotation)
                        non_none = [a for a in args if a is not type(None)]
                        if len(non_none) == 1 and non_none[0] is str:
                            should_skip = True

            if should_skip:
                new_kwargs[k] = v
            else:
                try:
                    # Only parse top-level string arguments
                    new_kwargs[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    new_kwargs[k] = v

        return new_kwargs

    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await func(*args, **process_kwargs(kwargs))

        # Preserve signature for FastMCP schema generation
        async_wrapper.__signature__ = sig
        return async_wrapper
    else:

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            return func(*args, **process_kwargs(kwargs))

        sync_wrapper.__signature__ = sig
        return sync_wrapper
