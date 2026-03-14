"""Tests for MCP tools that take a single Pydantic param model (forward-ref resolution).

When a tool is defined in a consumer module with from __future__ import annotations,
its param type is a string at runtime. ToolAccess wraps the tool and passes the
wrapper to FastMCP; Pydantic resolves the wrapper's type hints using the wrapper's
__module__ (via get_module_ns_of). The wrapper must have __module__ set to the
original function's module so the consumer's model (e.g. MyParams) is found.

See: temp_refs/toolaccess-pydantic-param-mcp-forward-ref.md
"""

from toolaccess import StreamableHTTPMCPServer


def test_mcp_tool_schema_build_succeeds_for_pydantic_param_from_consumer_module():
    """Building the MCP tool schema from the wrapper must not raise NameError.

    Tool and MyParams live in a separate consumer module (with from __future__
    import annotations). Pydantic builds the input schema via TypeAdapter(wrapper)
    using get_module_ns_of(wrapper); the wrapper must have __module__ set to the
    original's so forward refs resolve in the consumer module.
    """
    from pydantic import TypeAdapter

    from tests.mcp_consumer.pydantic_param_tools import make_service

    server = StreamableHTTPMCPServer("test", principal_resolver=None)
    svc = make_service()
    tool = svc.tools[0]
    wrapped = server._wrap_for_mcp(tool)
    TypeAdapter(wrapped)  # Would raise NameError if wrapper.__module__ were wrong
