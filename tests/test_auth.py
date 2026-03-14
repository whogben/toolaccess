"""Integration tests for auth: principal resolver + access policy on REST."""

import pytest
from fastapi.testclient import TestClient

from toolaccess import (
    AccessPolicy,
    InvocationContext,
    OpenAPIServer,
    Principal,
    ServerManager,
    ToolDefinition,
    ToolService,
)


def _rest_principal_resolver(ctx: InvocationContext) -> Principal | None:
    """Resolve principal from X-Test-User header (for tests)."""
    request = ctx.raw_request
    if request is None:
        return None
    user_id = request.headers.get("X-Test-User")
    if user_id is None:
        return None
    return Principal(
        kind="user",
        id=user_id,
        name=user_id,
        is_authenticated=True,
    )


class TestAuthIntegration:
    """Integration tests that principal resolver + access policy work on REST."""

    def test_rest_authenticated_request_succeeds(self):
        """With principal resolver and valid auth header, protected tool returns 200."""
        mgr = ServerManager("test")

        def admin_only():
            return "admin data"

        svc = ToolService(
            "admin",
            [
                ToolDefinition(
                    func=admin_only,
                    name="admin_only",
                    access=AccessPolicy(require_authenticated=True),
                )
            ],
        )

        api = OpenAPIServer("/api", "API", principal_resolver=_rest_principal_resolver)
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        response = client.post(
            "/api/admin_only",
            headers={"X-Test-User": "alice"},
        )
        assert response.status_code == 200
        assert response.json() == "admin data"

    def test_rest_unauthenticated_request_returns_403(self):
        """Without auth (no principal), protected tool returns 403."""
        mgr = ServerManager("test")

        def admin_only():
            return "admin data"

        svc = ToolService(
            "admin",
            [
                ToolDefinition(
                    func=admin_only,
                    name="admin_only",
                    access=AccessPolicy(require_authenticated=True),
                )
            ],
        )

        api = OpenAPIServer("/api", "API", principal_resolver=_rest_principal_resolver)
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        response = client.post("/api/admin_only")
        assert response.status_code == 403
        assert "Authentication required" in response.text
