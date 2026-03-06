import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from toolaccess import (
    ServerManager,
    ToolService,
    ToolDefinition,
    OpenAPIServer,
    SSEMCPServer,
    CLIServer,
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

    # Services
    tool_svc = ToolService("tools", [ToolDefinition(dummy_tool, "add_dummy", "POST")])
    admin_svc = ToolService(
        "admin", [ToolDefinition(dummy_admin, "check_admin", "GET")]
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
    mcp = SSEMCPServer("default")
    mcp.mount(tool_svc)
    mgr.add_server(mcp)

    # 4. Admin MCP
    mcp_admin = SSEMCPServer("admin")
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


@pytest.fixture
def runner():
    return CliRunner()


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


def test_mcp_endpoints(client):
    """Test that MCP SSE endpoints are mounted."""
    # Check default MCP server via messages endpoint (POST)
    # FastMCP might redirect if slashes mismatch. Allow redirects.
    # Note: If it redirects to a path without the prefix, that's a bug in the Dispatcher/SubApp interaction.
    # We check for 404 specifically.

    # Try with trailing slash to avoid 307 Redirect stripping the prefix
    response = client.post("/mcp/default/messages/")
    assert (
        response.status_code != 404
    ), f"Got {response.status_code}. History: {response.history}"

    # Check admin MCP server
    response = client.post("/mcp/admin/messages/")
    assert response.status_code != 404


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
    svc = ToolService("svc", [ToolDefinition(async_tool, "double", "POST")])

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

    svc = ToolService("svc", [ToolDefinition(simple_tool, "simple_tool", "POST")])
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
        "dynamic", [ToolDefinition(dummy_tool, "add_dynamic", "POST")]
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
