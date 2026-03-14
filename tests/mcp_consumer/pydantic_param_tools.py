"""Consumer module: tool with single Pydantic param model and from __future__ annotations.

Simulates downstream (e.g. gpdb-admin) where the tool and its param model live in
a separate module. The wrapper created by ToolAccess is defined in ToolAccess's module,
so type-hint resolution uses the wrong namespace and raises NameError for the model.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from toolaccess import InvocationContext, ToolService, inject_context


class MyParams(BaseModel):
    """Minimal Pydantic param model for MCP tool (replicates GraphSchemaCreateParams pattern)."""

    a: str = Field(..., description="A.")
    b: int = Field(0, description="B.")


def make_service() -> ToolService:
    """Return a ToolService with one MCP tool that takes a single Pydantic param model."""
    svc = ToolService("test")
    # surfaces={} uses default SurfaceSpec(enabled=True) so the tool is mounted on MCP
    @svc.tool(name="my_tool", access=None, surfaces={})
    async def my_tool(
        params: MyParams,
        ctx: InvocationContext = inject_context(),
    ) -> str:
        return f"{params.a}:{params.b}"
    return svc
