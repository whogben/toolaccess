"""Tests for context, principal, access policy, and surface spec."""

from toolaccess import (
    AccessPolicy,
    InvocationContext,
    Principal,
    SurfaceSpec,
)


class TestInvocationContext:
    def test_create_with_all_fields(self):
        principal = Principal(kind="user", id="123", name="test_user")
        ctx = InvocationContext(
            surface="rest",
            principal=principal,
            raw_request={"headers": {}},
            raw_mcp_context=None,
            raw_cli_context=None,
            state={"key": "value"},
        )
        assert ctx.surface == "rest"
        assert ctx.principal == principal
        assert ctx.raw_request == {"headers": {}}
        assert ctx.state == {"key": "value"}

    def test_create_with_defaults(self):
        ctx = InvocationContext(surface="cli")
        assert ctx.surface == "cli"
        assert ctx.principal is None
        assert ctx.raw_request is None
        assert ctx.state == {}

    def test_all_surface_types(self):
        for surface in ["rest", "mcp", "cli"]:
            ctx = InvocationContext(surface=surface)
            assert ctx.surface == surface


class TestPrincipal:
    def test_principal_minimal(self):
        p = Principal(kind="anonymous")
        assert p.kind == "anonymous"
        assert p.id is None
        assert p.name is None
        assert p.claims == {}
        assert p.is_authenticated is False
        assert p.is_trusted_local is False

    def test_principal_full(self):
        p = Principal(
            kind="user",
            id="user-123",
            name="John Doe",
            claims={"role": "admin", "org": "acme"},
            is_authenticated=True,
            is_trusted_local=True,
        )
        assert p.kind == "user"
        assert p.id == "user-123"
        assert p.name == "John Doe"
        assert p.claims == {"role": "admin", "org": "acme"}
        assert p.is_authenticated is True
        assert p.is_trusted_local is True

    def test_principal_various_claims(self):
        # Empty claims
        p1 = Principal(kind="service", claims={})
        assert p1.claims == {}

        # Complex claims
        p2 = Principal(
            kind="user",
            claims={
                "scopes": ["read", "write"],
                "metadata": {"department": "engineering"},
            },
        )
        assert p2.claims["scopes"] == ["read", "write"]
        assert p2.claims["metadata"]["department"] == "engineering"


class TestAccessPolicy:
    def test_default_policy(self):
        policy = AccessPolicy()
        assert policy.require_authenticated is False
        assert policy.allow_anonymous is True
        assert policy.allow_trusted_local is None
        assert policy.required_claims == {}

    def test_authenticated_required(self):
        policy = AccessPolicy(require_authenticated=True)
        assert policy.require_authenticated is True

    def test_no_anonymous(self):
        policy = AccessPolicy(allow_anonymous=False)
        assert policy.allow_anonymous is False

    def test_trusted_local_required(self):
        policy = AccessPolicy(allow_trusted_local=True)
        assert policy.allow_trusted_local is True

    def test_trusted_local_denied(self):
        policy = AccessPolicy(allow_trusted_local=False)
        assert policy.allow_trusted_local is False

    def test_required_claims(self):
        policy = AccessPolicy(required_claims={"role": "admin", "tenant": "prod"})
        assert policy.required_claims == {"role": "admin", "tenant": "prod"}

    def test_combined_policy(self):
        policy = AccessPolicy(
            require_authenticated=True,
            allow_anonymous=False,
            allow_trusted_local=True,
            required_claims={"scope": "tools:execute"},
        )
        assert policy.require_authenticated is True
        assert policy.allow_anonymous is False
        assert policy.allow_trusted_local is True
        assert policy.required_claims == {"scope": "tools:execute"}


class TestSurfaceSpec:
    def test_default_spec(self):
        spec = SurfaceSpec()
        assert spec.enabled is True
        assert spec.http_method is None
        assert spec.renderer is None
        assert spec.principal_resolver is None

    def test_spec_with_method(self):
        spec = SurfaceSpec(http_method="GET")
        assert spec.http_method == "GET"

    def test_spec_disabled(self):
        spec = SurfaceSpec(enabled=False)
        assert spec.enabled is False
