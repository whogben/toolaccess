import inspect
import asyncio
import json
import logging
from functools import wraps
from typing import Any, Callable, Literal, Union, get_origin, get_args

import typer
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastmcp import FastMCP
from abc import ABC, abstractmethod
from starlette.types import Scope, Receive, Send
from starlette.responses import Response

# Context, definition, pipeline and renderer imports
from .context import Surface, InvocationContext, Principal, PrincipalResolver
from .definition import ToolDefinition, get_context_param, InjectContext, inject_context
from .pipeline import invoke_tool
from .renderers import ResultRenderer, noop_renderer, PydanticJsonRenderer

"""
Generic Tool Server Utility (Polymorphic Server Model)
Provides reusable components for exposing Python functions as tools via
CLI, OpenAPI (REST), and MCP (StreamableHTTP/Stdio).
"""

logger = logging.getLogger(__name__)

# --- 1. Tool Definition & Service ---


class ToolService:
    """A collection of tools to be exposed together.

    Use this to register Python callables as tools that can be exposed on
    multiple surfaces (REST, MCP, CLI) via the various server types.
    """

    def __init__(self, name: str, tools: list[Callable | ToolDefinition] | None = None):
        self.name = name
        self.tools: list[ToolDefinition] = []
        if tools:
            for t in tools:
                self.tools.append(self._normalize_tool(t))

    def _normalize_tool(self, tool: Callable | ToolDefinition) -> ToolDefinition:
        """Normalize a tool into a ToolDefinition instance."""
        if isinstance(tool, ToolDefinition):
            return tool
        return ToolDefinition(func=tool, name=tool.__name__)

    def tool(
        self,
        name: str | None = None,
        description: str | None = None,
        surfaces: dict | None = None,
        access=None,
        codecs: dict | None = None,
        renderer: ResultRenderer | None = None,
    ):
        """Decorator to register a tool with this service.

        Args:
            name: Optional explicit tool name. Defaults to the function name.
            description: Optional description. Defaults to the function docstring.
            surfaces: Optional per-surface configuration mapping.
            access: Optional access policy for this tool.
            codecs: Optional mapping of argument name to codec.
            renderer: Optional result renderer to use for this tool.
        """

        def decorator(func: Callable) -> Callable:
            tool_def = ToolDefinition(
                func=func,
                name=name or func.__name__,
                description=description,
                surfaces=surfaces or {},
                access=access,
                codecs=codecs or {},
                renderer=renderer,
            )
            self.tools.append(tool_def)
            return func

        return decorator


# --- 2. Abstract Base Server ---


class BaseServer(ABC):
    """Abstract base for any server interface."""

    def __init__(self, principal_resolver: PrincipalResolver | None = None):
        self.principal_resolver = principal_resolver

    @abstractmethod
    def mount(self, service: ToolService):
        """Attach a ToolService to this server.

        Implementations use this to register routes, commands or MCP tools
        for all tools in the service on their specific surface.
        """
        pass

    @abstractmethod
    def register_to(self, manager: "ServerManager"):
        """Hook this server into the runtime manager.

        Implementations typically use this to register themselves with the
        ServerManager's FastAPI app, CLI, or MCP registry.
        """
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

    def __init__(
        self,
        path_prefix: str = "",
        title: str = "API",
        principal_resolver: PrincipalResolver | None = None,
    ):
        super().__init__(principal_resolver)
        self.path_prefix = path_prefix
        self.app = FastAPI(title=title)

    def mount(self, service: ToolService):
        from .definition import get_surface_spec

        for tool in service.tools:
            surface_spec = get_surface_spec(tool, "rest")
            if not surface_spec.enabled:
                continue
            self._add_route(tool)

    def _add_route(self, tool: ToolDefinition):
        from .definition import get_surface_spec

        surface_spec = get_surface_spec(tool, "rest")
        http_method = surface_spec.http_method or "POST"
        router = METHOD_ROUTERS.get(http_method, FastAPI.post)

        context_param = get_context_param(tool.func)
        original_func = tool.func
        original_sig = inspect.signature(original_func)

        # Build new signature with request parameter for FastAPI injection
        request_param = inspect.Parameter(
            "request",
            inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty,
            annotation=Request,
        )
        new_params = list(original_sig.parameters.values()) + [request_param]
        new_sig = original_sig.replace(parameters=new_params)
        new_annotations = dict(getattr(original_func, "__annotations__", {}))
        new_annotations["request"] = Request

        if inspect.iscoroutinefunction(original_func):

            @wraps(original_func)
            async def route_handler(*args, request: Request, **kwargs):
                ctx = InvocationContext(
                    surface="rest",
                    principal=None,
                    raw_request=request,
                )

                try:
                    result = await invoke_tool(
                        tool=tool,
                        raw_args=kwargs,
                        ctx=ctx,
                        context_param_name=context_param,
                        surface_resolver=self.principal_resolver,
                    )
                except PermissionError as e:
                    raise HTTPException(status_code=403, detail=str(e))
                except (ValueError, KeyError, TypeError) as e:
                    raise HTTPException(status_code=400, detail=str(e))
                except Exception as e:
                    logger.exception(f"Unexpected error in tool {tool.name}: {e}")
                    raise HTTPException(status_code=500, detail=str(e))

                if isinstance(result, Response):
                    return result
                return result

        else:

            @wraps(original_func)
            def route_handler(*args, request: Request, **kwargs):
                ctx = InvocationContext(
                    surface="rest",
                    principal=None,
                    raw_request=request,
                )

                try:
                    result = asyncio.run(
                        invoke_tool(
                            tool=tool,
                            raw_args=kwargs,
                            ctx=ctx,
                            context_param_name=context_param,
                            surface_resolver=self.principal_resolver,
                        )
                    )
                except PermissionError as e:
                    raise HTTPException(status_code=403, detail=str(e))
                except (ValueError, KeyError, TypeError) as e:
                    raise HTTPException(status_code=400, detail=str(e))
                except Exception as e:
                    logger.exception(f"Unexpected error in tool {tool.name}: {e}")
                    raise HTTPException(status_code=500, detail=str(e))

                if isinstance(result, Response):
                    return result
                return result

        route_handler.__signature__ = new_sig
        route_handler.__annotations__ = new_annotations
        route_handler.__doc__ = tool.description
        route_handler.__name__ = tool.name

        router(self.app, f"/{tool.name}", name=tool.name, description=tool.description)(
            route_handler
        )

    def register_to(self, manager: "ServerManager"):
        pass


class StreamableHTTPMCPServer(BaseServer):
    """Exposes tools via FastMCP (StreamableHTTP & Stdio capability)."""

    def __init__(
        self, name: str = "default", principal_resolver: PrincipalResolver | None = None
    ):
        super().__init__(principal_resolver)
        self.name = name
        self.mcp = FastMCP(name)

    def mount(self, service: ToolService):
        from .definition import get_surface_spec

        for tool in service.tools:
            surface_spec = get_surface_spec(tool, "mcp")
            if not surface_spec.enabled:
                continue
            wrapped_func = self._wrap_for_mcp(tool)
            self.mcp.tool(wrapped_func, name=tool.name, description=tool.description)

    def _wrap_for_mcp(self, tool: ToolDefinition) -> Callable:
        original_func = tool.func
        sig = inspect.signature(original_func)
        context_param = get_context_param(original_func)

        def process_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
            new_kwargs = {}
            for k, v in kwargs.items():
                if not isinstance(v, str):
                    new_kwargs[k] = v
                    continue

                param = sig.parameters.get(k)
                should_skip = False

                if param:
                    annotation = param.annotation
                    if annotation is str:
                        should_skip = True
                    else:
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
                        new_kwargs[k] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        new_kwargs[k] = v

            return new_kwargs

        if inspect.iscoroutinefunction(original_func):

            @wraps(original_func)
            async def async_wrapper(*args, **kwargs):
                processed_kwargs = process_kwargs(kwargs)
                ctx = InvocationContext(surface="mcp", principal=None)
                return await invoke_tool(
                    tool=tool,
                    raw_args=processed_kwargs,
                    ctx=ctx,
                    context_param_name=context_param,
                    surface_resolver=self.principal_resolver,
                )

            async_wrapper.__signature__ = sig
            async_wrapper.__annotations__ = getattr(
                original_func, "__annotations__", {}
            )
            return async_wrapper
        else:

            @wraps(original_func)
            def sync_wrapper(*args, **kwargs):
                processed_kwargs = process_kwargs(kwargs)
                ctx = InvocationContext(surface="mcp", principal=None)
                return asyncio.run(
                    invoke_tool(
                        tool=tool,
                        raw_args=processed_kwargs,
                        ctx=ctx,
                        context_param_name=context_param,
                        surface_resolver=self.principal_resolver,
                    )
                )

            sync_wrapper.__signature__ = sig
            sync_wrapper.__annotations__ = getattr(original_func, "__annotations__", {})
            return sync_wrapper

    def register_to(self, manager: "ServerManager"):
        manager.mcp_servers[self.name] = self.mcp


class CLIServer(BaseServer):
    """Exposes tools via Typer CLI."""

    def __init__(
        self,
        name: str | None = None,
        default_renderer: ResultRenderer | None = None,
        principal_resolver: PrincipalResolver | None = None,
    ):
        super().__init__(principal_resolver)
        self.name = name
        self.default_renderer = default_renderer or PydanticJsonRenderer()
        self.typer_app = typer.Typer(name=name) if name else typer.Typer()
        self.manager: "ServerManager" | None = None

    def mount(self, service: ToolService):
        from .definition import get_surface_spec

        for tool in service.tools:
            surface_spec = get_surface_spec(tool, "cli")
            if not surface_spec.enabled:
                continue
            self._add_command(self.typer_app, tool)

    def _add_command(self, app: typer.Typer, tool: ToolDefinition):
        context_param = get_context_param(tool.func)
        original_func = tool.func
        sig = inspect.signature(original_func)

        async def _run_tool(kwargs: dict) -> Any:
            ctx = InvocationContext(
                surface="cli",
                principal=Principal(
                    kind="local",
                    is_authenticated=True,
                    is_trusted_local=True,
                ),
            )
            return await invoke_tool(
                tool=tool,
                raw_args=kwargs,
                ctx=ctx,
                context_param_name=context_param,
                surface_resolver=self.principal_resolver,
                surface_default_renderer=self.default_renderer,
            )

        @wraps(original_func)
        def cli_wrapper(**kwargs):
            async def runner():
                return await _run_tool(kwargs)

            async def runner_with_lifespan():
                if self.manager and self.manager.lifespan_ctx:
                    async with self.manager.lifespan_ctx(self.manager.app):
                        return await runner()
                return await runner()

            try:
                result = asyncio.run(runner_with_lifespan())
                if isinstance(result, str):
                    print(result)
                else:
                    rendered = self.default_renderer.render(
                        result, surface="cli", ctx=InvocationContext(surface="cli")
                    )
                    print(rendered)
            except KeyboardInterrupt:
                return None

        cli_wrapper.__signature__ = sig
        cli_wrapper.__annotations__ = getattr(original_func, "__annotations__", {})
        app.command(name=tool.name, help=tool.description)(cli_wrapper)

    def register_to(self, manager: "ServerManager"):
        self.manager = manager
        manager.cli.add_typer(self.typer_app, name=self.name)


class MountableApp(BaseServer):
    """Wraps an existing FastAPI/ASGI application."""

    def __init__(self, app: FastAPI, path_prefix: str = "", name: str = "app"):
        super().__init__(None)
        self.app = app
        self.path_prefix = path_prefix
        self.name = name

    def mount(self, service: ToolService):
        """Ignored for MountableApp.

        MountableApp wraps an existing ASGI application and does not register
        tools from ToolService; it is only routed by the ServerManager.
        """
        pass

    def register_to(self, manager: "ServerManager"):
        """No-op: MountableApp does not participate in CLI or MCP registration.

        The ServerManager routes HTTP traffic to this app based on path
        prefix, but does not add any CLI commands or MCP servers for it.
        """
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

            elif isinstance(server, StreamableHTTPMCPServer):
                prefix = f"/mcp/{server.name}"
                if path.startswith(prefix):
                    remaining = path[len(prefix) :]
                    if not remaining or remaining.startswith("/"):
                        matches.append((server, len(prefix)))

            elif isinstance(server, MountableApp):
                prefix = server.path_prefix.strip("/")
                check_prefix = f"/{prefix}" if prefix else "/"
                if path.startswith(check_prefix):
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

        matches.sort(key=lambda x: x[1], reverse=True)
        server, prefix_len = matches[0]

        if isinstance(server, OpenAPIServer):
            prefix = server.path_prefix.strip("/")
            check_prefix = f"/{prefix}"
            scope["root_path"] = scope.get("root_path", "") + check_prefix
            scope["path"] = path[prefix_len:] or "/"
            await server.app(scope, receive, send)

        elif isinstance(server, StreamableHTTPMCPServer):
            prefix = f"/mcp/{server.name}"
            scope["root_path"] = scope.get("root_path", "") + prefix
            remaining = path[prefix_len:]
            scope["path"] = remaining if remaining else "/"
            http_app = server.mcp.http_app(transport="streamable-http")
            http_app.redirect_slashes = False
            await http_app(scope, receive, send)

        elif isinstance(server, MountableApp):
            prefix = server.path_prefix.strip("/")
            check_prefix = f"/{prefix}" if prefix else ""
            if check_prefix == "/":
                check_prefix = ""

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
        """Register a polymorphic server instance.

        The server is added to the active routing table and given a chance
        to register itself with this manager (HTTP, CLI, MCP, etc.).
        """
        self.active_servers[str(id(server))] = server
        server.register_to(self)

    def remove_server(self, server: BaseServer):
        """Unregister a server instance.

        Removes the server from the active routing table and, if applicable,
        from the MCP registry so its endpoints are no longer reachable.
        """
        server_id = str(id(server))
        if server_id in self.active_servers:
            del self.active_servers[server_id]
            if (
                isinstance(server, StreamableHTTPMCPServer)
                and server.name in self.mcp_servers
            ):
                del self.mcp_servers[server.name]

    def _add_infrastructure(self):
        @self.app.get("/health")
        async def health():
            return {"mcp_servers": list(self.mcp_servers.keys())}

        @self.cli.command()
        def start(host: str = "127.0.0.1", port: int = 8000):
            """Start the server (REST + MCP StreamableHTTP)."""
            print(f"🚀 {self.name} Server Starting...")
            print(f"---------------------------------------------------")
            print(f"📋 OpenAPI:           http://{host}:{port}/docs")
            for mcp_name in self.mcp_servers:
                print(f"🤖 MCP Server:        http://{host}:{port}/mcp/{mcp_name}/mcp")

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
                logger.exception("Error running MCP stdio", exc_info=e)
                print(f"Error running MCP stdio: {e}")
                self.mcp_servers[name].run(transport="stdio")

    def run(self):
        """Entry point for the CLI.

        Delegates to the underlying Typer application that aggregates all
        registered CLIServer instances and infrastructure commands.
        """
        self.cli()
