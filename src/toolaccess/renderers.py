from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from .context import InvocationContext, Surface


@runtime_checkable
class ResultRenderer(Protocol):
    """Protocol for result renderers used by different surfaces."""

    def render(
        self, value: Any, *, surface: Surface, ctx: InvocationContext
    ) -> Any: ...


class NoOpRenderer:
    """Renderer that returns the value unchanged."""

    def render(self, value: Any, *, surface: Surface, ctx: InvocationContext) -> Any:
        return value


class JsonRenderer:
    """Renderer that serializes values to JSON strings."""

    def __init__(self, indent: int | None = None, sort_keys: bool = False):
        self.indent = indent
        self.sort_keys = sort_keys

    def render(self, value: Any, *, surface: Surface, ctx: InvocationContext) -> Any:
        return json.dumps(
            value, indent=self.indent, sort_keys=self.sort_keys, default=str
        )


class PydanticJsonRenderer:
    """Renderer that understands Pydantic models and falls back to JSON."""

    def __init__(
        self, by_alias: bool = False, indent: int | None = None, sort_keys: bool = False
    ):
        self.by_alias = by_alias
        self.indent = indent
        self.sort_keys = sort_keys

    def render(self, value: Any, *, surface: Surface, ctx: InvocationContext) -> Any:
        # Handle Pydantic v2 models via duck typing
        if hasattr(value, "model_dump"):
            data = value.model_dump(by_alias=self.by_alias)
            return json.dumps(
                data, indent=self.indent, sort_keys=self.sort_keys, default=str
            )

        return json.dumps(
            value, indent=self.indent, sort_keys=self.sort_keys, default=str
        )


# Singleton instances for convenience
noop_renderer = NoOpRenderer()
json_renderer = JsonRenderer()
pydantic_json_renderer = PydanticJsonRenderer()
