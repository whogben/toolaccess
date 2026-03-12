from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, get_args, get_origin, get_type_hints

from .codecs import ArgumentCodec
from .context import (
    AccessPolicy,
    HttpMethod,
    InvocationContext,
    Surface,
    SurfaceSpec,
)
from .renderers import ResultRenderer


class InjectContext:
    """Marker class to indicate a parameter should receive the InvocationContext."""

    pass


def inject_context() -> InjectContext:
    """Factory function that returns an InjectContext instance.

    Provides a cleaner API for context injection:
        ctx: InvocationContext = inject_context()
    """
    return InjectContext()


def get_context_param(func: Callable) -> str | None:
    """Inspect function signature for parameters annotated with InjectContext.

    Handles both:
    - Annotated[InvocationContext, InjectContext()]
    - Plain InvocationContext (for backward compatibility)

    Returns the parameter name if found, None otherwise.
    """
    sig = inspect.signature(func)
    hints = get_type_hints(func, include_extras=True)

    for param_name in sig.parameters:
        hint = hints.get(param_name)
        if hint is None:
            continue

        # Check for Annotated[InvocationContext, InjectContext()]
        origin = get_origin(hint)
        if origin is Annotated:
            args = get_args(hint)
            if args and args[0] is InvocationContext:
                # Check if any metadata is an InjectContext instance
                metadata = args[1:]
                if any(isinstance(m, InjectContext) for m in metadata):
                    return param_name

        # Check for plain InvocationContext (backward compatibility)
        if hint is InvocationContext:
            return param_name

    return None


@dataclass
class ToolDefinition:
    """Metadata for a single tool function."""

    func: Callable
    name: str
    description: str | None = None
    surfaces: dict[Surface, SurfaceSpec] = field(default_factory=dict)
    access: AccessPolicy | None = None
    codecs: dict[str, ArgumentCodec] = field(default_factory=dict)
    renderer: ResultRenderer | None = None

    def __post_init__(self):
        if self.description is None and self.func.__doc__:
            self.description = inspect.cleandoc(self.func.__doc__)


def get_surface_spec(tool: ToolDefinition, surface: Surface) -> SurfaceSpec:
    """Returns the SurfaceSpec for a given surface, or a default one if not configured."""
    return tool.surfaces.get(surface, SurfaceSpec())
