from __future__ import annotations

import inspect
import types
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Callable, Union, get_args, get_origin, get_type_hints

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


def get_public_signature(
    func: Callable,
) -> tuple[inspect.Signature, dict[str, Any], str | None]:
    """Return the externally visible signature for a tool function.

    The public signature omits any injected InvocationContext parameter so
    framework-level registration uses only user-supplied arguments.
    """

    sig = inspect.signature(func)
    context_param_name = get_context_param(func)
    public_params = [
        param
        for param in sig.parameters.values()
        if param.name != context_param_name
    ]
    public_sig = sig.replace(parameters=public_params)
    public_annotations = {
        key: value
        for key, value in getattr(func, "__annotations__", {}).items()
        if key != context_param_name
    }
    return public_sig, public_annotations, context_param_name


def get_cli_signature(
    func: Callable,
) -> tuple[inspect.Signature, dict[str, Any], str | None]:
    """Return a Typer-safe signature for CLI registration."""

    public_sig, public_annotations, context_param_name = get_public_signature(func)
    cli_params = []
    cli_annotations: dict[str, Any] = {}

    for param in public_sig.parameters.values():
        cli_annotation = _to_cli_safe_annotation(param.annotation)
        cli_params.append(param.replace(annotation=cli_annotation))
        if cli_annotation is not inspect.Parameter.empty:
            cli_annotations[param.name] = cli_annotation

    if "return" in public_annotations:
        cli_annotations["return"] = public_annotations["return"]

    return public_sig.replace(parameters=cli_params), cli_annotations, context_param_name


def _to_cli_safe_annotation(annotation: Any) -> Any:
    annotation = _strip_annotated(annotation)
    if annotation is inspect.Parameter.empty:
        return annotation
    if _is_typer_safe_annotation(annotation):
        return annotation
    if _is_optional_annotation(annotation):
        return str | None
    return str


def _is_typer_safe_annotation(annotation: Any) -> bool:
    base_annotation = _strip_annotated(annotation)
    if _is_optional_annotation(base_annotation):
        base_annotation = _get_optional_inner_annotation(base_annotation)

    if base_annotation in {str, int, float, bool, Path}:
        return True

    return inspect.isclass(base_annotation) and issubclass(base_annotation, Enum)


def _is_optional_annotation(annotation: Any) -> bool:
    annotation = _strip_annotated(annotation)
    origin = get_origin(annotation)
    if origin not in (Union, types.UnionType):
        return False
    args = get_args(annotation)
    return len(args) == 2 and type(None) in args


def _get_optional_inner_annotation(annotation: Any) -> Any:
    annotation = _strip_annotated(annotation)
    return next(arg for arg in get_args(annotation) if arg is not type(None))


def _strip_annotated(annotation: Any) -> Any:
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


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
