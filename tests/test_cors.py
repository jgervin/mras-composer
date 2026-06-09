"""The ops-frontend authoring page calls POST /preview from the browser, so the composer must
allow POST in CORS (preflight). Without it the browser blocks the Preview request."""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main


def test_preview_cors_preflight_allows_post():
    with patch("main.create_pool", AsyncMock(return_value=AsyncMock())):
        with TestClient(main.app) as client:
            res = client.options(
                "/preview",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
    # Starlette returns 400 "Disallowed CORS method" when POST isn't in allow_methods.
    assert res.status_code == 200
    assert "POST" in res.headers.get("access-control-allow-methods", "")
