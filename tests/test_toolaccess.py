import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from toolaccess import (
    CLIServer,
    MountableApp,
    OpenAPIServer,
    ServerManager,
    StreamableHTTPMCPServer,
    SurfaceSpec,
    ToolDefinition,
    ToolService,
)
from contextlib import asynccontextmanager


def dummy_tool(a: int, b: int) -> int:
    """A dummy tool that adds two numbers."""
    return a + b


def dummy_admin():
    """A dummy admin function."""
    return {"status": "admin_ok"}


@pytest.fixture
def manager():
    mgr = ServerManager(name="test_service")

    # Services - using decorator API with surfaces dict
    tool_svc = ToolService(
        "tools",
        [
            ToolDefinition(
                dummy_tool,
                "add_dummy",
                surfaces={"rest": SurfaceSpec(http_method="POST")},
            )
        ],
    )
    admin_svc = ToolService(
        "admin",
        [
            ToolDefinition(
                dummy_admin,
                "check_admin",
                surfaces={"rest": SurfaceSpec(http_method="GET")},
            )
        ],
    )

    # 1. API v1
    api_v1 = OpenAPIServer("/tools", "Tools API")
    api_v1.mount(tool_svc)
    mgr.add_server(api_v1)

    # 2. Admin API
    api_admin = OpenAPIServer("/admin", "Admin API")
    api_admin.mount(admin_svc)
    mgr.add_server(api_admin)

    # 3. Default MCP
    mcp = StreamableHTTPMCPServer("default")
    mcp.mount(tool_svc)
    mgr.add_server(mcp)

    # 4. Admin MCP
    mcp_admin = StreamableHTTPMCPServer("admin")
    mcp_admin.mount(admin_svc)
    mgr.add_server(mcp_admin)

    # 5. CLI
    cli_tools = CLIServer("tools")
    cli_tools.mount(tool_svc)
    mgr.add_server(cli_tools)

    cli_admin = CLIServer("admin")
    cli_admin.mount(admin_svc)
    mgr.add_server(cli_admin)

    return mgr


@pytest.fixture
def client(manager):
    return TestClient(manager.app)


def test_server_health(client):
    """Test that the server health endpoint works."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "mcp_servers" in data


def test_tool_rest(client):
    """Test accessing a tool via the mounted HTTP endpoint."""
    response = client.post("/tools/add_dummy", params={"a": 1, "b": 2})
    assert response.status_code == 200
    assert response.json() == 3


def test_admin_rest(client):
    """Test accessing an admin function via the mounted HTTP endpoint."""
    response = client.get("/admin/check_admin")
    assert response.status_code == 200
    assert response.json() == {"status": "admin_ok"}


def test_mcp_endpoints(manager):
    """Test that MCP StreamableHTTP endpoints are mounted."""
    with TestClient(manager.app) as managed_client:
        default_post = managed_client.post("/mcp/default/mcp", follow_redirects=False)
        default_get = managed_client.get("/mcp/default/mcp", follow_redirects=False)
        admin_post = managed_client.post("/mcp/admin/mcp", follow_redirects=False)

    assert default_post.status_code < 500
    assert default_post.status_code != 404
    assert default_get.status_code < 500
    assert default_get.status_code != 404
    assert admin_post.status_code < 500
    assert admin_post.status_code != 404


def test_mcp_http_app_is_cached_and_reused():
    """Test that the FastMCP HTTP app is created once and reused."""
    mgr = ServerManager("mcp_cache_test")
    svc = ToolService(
        "tools",
        [
            ToolDefinition(
                dummy_tool,
                "add_dummy",
                surfaces={"rest": SurfaceSpec(http_method="POST")},
            )
        ],
    )
    mcp = StreamableHTTPMCPServer("default")
    mcp.mount(svc)

    call_count = 0
    original_http_app = mcp.mcp.http_app

    def counting_http_app(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_http_app(*args, **kwargs)

    mcp.mcp.http_app = counting_http_app
    mgr.add_server(mcp)

    first_app = mcp.get_http_app()
    second_app = mcp.get_http_app()
    assert first_app is second_app

    with TestClient(mgr.app) as managed_client:
        response_one = managed_client.post("/mcp/default/mcp", follow_redirects=False)
        response_two = managed_client.get("/mcp/default/mcp", follow_redirects=False)

    assert response_one.status_code < 500
    assert response_two.status_code < 500
    assert call_count == 1


def test_openapi_specs(client):
    """Test that OpenAPI specs are generated for mounted sub-apps."""
    # Check public tools spec - FastAPIMounts sub-apps don't always expose
    # sub-openapi.json at the mount point automatically unless configured.
    # However, our OpenAPIServer creates a full FastAPI app.
    # When mounted, FastAPI serves the sub-app documentation at /tools/docs usually.
    # The openapi.json is at /tools/openapi.json
    response = client.get("/tools/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert "paths" in spec
    assert "/add_dummy" in spec["paths"]

    # Check admin spec
    response = client.get("/admin/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert "/check_admin" in spec["paths"]


def test_cli_integration(manager, runner):
    """Test that registered tools appear in the CLI help."""
    result = runner.invoke(manager.cli, ["tools", "--help"])
    assert result.exit_code == 0
    assert "add_dummy" in result.stdout

    result = runner.invoke(manager.cli, ["admin", "--help"])
    assert result.exit_code == 0
    assert "check_admin" in result.stdout


def test_async_cli_execution(runner):
    """Test that async tools are correctly wrapped and executed in CLI."""
    mgr = ServerManager("async_service")

    async def async_tool(x: int) -> int:
        return x * 2

    # Service
    svc = ToolService(
        "svc",
        [
            ToolDefinition(
                async_tool, "double", surfaces={"rest": SurfaceSpec(http_method="POST")}
            )
        ],
    )

    # Server
    cli = CLIServer("math")
    cli.mount(svc)
    mgr.add_server(cli)

    result = runner.invoke(mgr.cli, ["math", "double", "21"])
    assert result.exit_code == 0


def test_lifespan_integration(runner):
    """Test that lifespan context is entered during CLI execution."""
    events = []

    @asynccontextmanager
    async def mock_lifespan(app):
        events.append("startup")
        yield
        events.append("shutdown")

    mgr = ServerManager(name="lifespan_service", lifespan=mock_lifespan)

    async def simple_tool():
        return "ok"

    svc = ToolService(
        "svc",
        [
            ToolDefinition(
                simple_tool,
                "simple_tool",
                surfaces={"rest": SurfaceSpec(http_method="POST")},
            )
        ],
    )
    cli = CLIServer("tools")
    cli.mount(svc)
    mgr.add_server(cli)

    result = runner.invoke(mgr.cli, ["tools", "simple_tool"])
    assert result.exit_code == 0
    assert "startup" in events
    assert "shutdown" in events


def test_mcp_run_cli(manager, runner):
    """Minimal test to ensure mcp-run command exists and doesn't crash on invocation."""
    result = runner.invoke(manager.cli, ["mcp-run", "--name", "non_existent"])
    assert result.exit_code == 0
    assert "not found" in result.stdout


def test_dynamic_server_lifecycle_explicit():
    """Explicit test of dynamic add/remove."""
    mgr = ServerManager("dynamic_test")
    client = TestClient(mgr.app)

    # 1. Verify 404 for non-existent path
    response = client.get("/dynamic/openapi.json")
    assert response.status_code == 404

    # 2. Add Server
    dynamic_api = OpenAPIServer("/dynamic", "Dynamic API")
    dynamic_svc = ToolService(
        "dynamic",
        [
            ToolDefinition(
                dummy_tool,
                "add_dynamic",
                surfaces={"rest": SurfaceSpec(http_method="POST")},
            )
        ],
    )
    dynamic_api.mount(dynamic_svc)

    mgr.add_server(dynamic_api)

    # 3. Verify 200 (It works!)
    response = client.get("/dynamic/openapi.json")
    assert response.status_code == 200

    # Verify tool execution
    response = client.post("/dynamic/add_dynamic", params={"a": 10, "b": 20})
    assert response.status_code == 200
    assert response.json() == 30

    # 4. Remove Server
    mgr.remove_server(dynamic_api)

    # 5. Verify 404 again
    response = client.get("/dynamic/openapi.json")
    assert response.status_code == 404


def test_mountable_app_routing():
    """Verify that MountableApp receives requests via DynamicDispatcher."""
    mgr = ServerManager("mountable_test")

    dashboard = FastAPI()

    @dashboard.get("/")
    async def index():
        return {"page": "dashboard"}

    mgr.add_server(MountableApp(dashboard, path_prefix="/dashboard", name="dashboard"))
    client = TestClient(mgr.app)

    # Requests under the prefix should be routed to the mounted app
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert response.json() == {"page": "dashboard"}

    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert response.json() == {"page": "dashboard"}
