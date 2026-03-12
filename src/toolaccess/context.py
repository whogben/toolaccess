from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Callable, Awaitable

if TYPE_CHECKING:
    from .renderers import ResultRenderer


Surface = Literal["rest", "mcp", "cli"]

HttpMethod = Literal["GET", "POST", "PUT", "DELETE", "PATCH"]

PrincipalResolver = Callable[
    ["InvocationContext"], "Principal | None | Awaitable[Principal | None]"
]


@dataclass
class Principal:
    kind: str
    id: str | None = None
    name: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)
    is_authenticated: bool = False
    is_trusted_local: bool = False


@dataclass
class InvocationContext:
    surface: Surface
    principal: Principal | None = None
    raw_request: Any | None = None
    raw_mcp_context: Any | None = None
    raw_cli_context: Any | None = None
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessPolicy:
    require_authenticated: bool = False
    allow_anonymous: bool = True
    allow_trusted_local: bool | None = None
    required_claims: dict[str, Any] = field(default_factory=dict)


@dataclass
class SurfaceSpec:
    enabled: bool = True
    http_method: HttpMethod | None = None
    renderer: ResultRenderer | None = None
    principal_resolver: PrincipalResolver | None = None
