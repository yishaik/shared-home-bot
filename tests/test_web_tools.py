import json

import httpx
import pytest

from app.config import Settings
from app.web_tools import FastCrwClient, HereNowClient, WebToolError, safe_site_path, validate_public_http_url, web_tool_specs


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_url_safety_blocks_private_networks() -> None:
    for url in (
        "http://localhost/admin",
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://169.254.169.254/latest/meta-data",
        "http://service.internal/path",
        "https://user:pass@example.com",
    ):
        with pytest.raises(WebToolError):
            validate_public_http_url(url)

    assert validate_public_http_url("https://example.com/path") == "https://example.com/path"


def test_site_paths_reject_traversal_and_reserved_manifest() -> None:
    assert safe_site_path("assets/app.js") == "assets/app.js"
    for path in ("/index.html", "../secret", "assets/../secret", ".herenow/proxy.json", "a//b"):
        with pytest.raises(WebToolError):
            safe_site_path(path)


def test_integrations_are_fail_closed() -> None:
    disabled = settings()
    assert not disabled.fastcrw_enabled
    assert not disabled.herenow_enabled
    assert web_tool_specs(disabled) == []

    self_hosted = settings(CRW_API_URL="http://localhost:3000")
    assert self_hosted.fastcrw_enabled

    enabled = settings(CRW_API_KEY="crw_test", HERENOW_API_KEY="hnk_test")
    names = {spec["function"]["name"] for spec in web_tool_specs(enabled)}
    assert names == {"web_search", "web_read", "web_map", "site_publish", "site_list"}


@pytest.mark.asyncio
async def test_fastcrw_search_normalizes_results_and_freshness() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.fastcrw.com/v1/search")
        assert request.headers["Authorization"] == "Bearer crw_test"
        payload = json.loads(request.content)
        assert payload == {"query": "latest household automation", "limit": 3, "tbs": "qdr:w", "lang": "en"}
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {
                        "title": "Result",
                        "url": "https://example.com/result",
                        "description": "Useful summary",
                        "position": 1,
                        "score": 9.2,
                    }
                ],
            },
        )

    client = FastCrwClient(settings(CRW_API_KEY="crw_test"), transport=httpx.MockTransport(handler))
    result = await client.search(query="latest household automation", limit=3, freshness="week", lang="en")

    assert result["ok"] is True
    assert result["results"] == [
        {
            "title": "Result",
            "url": "https://example.com/result",
            "snippet": "Useful summary",
            "position": 1,
            "score": 9.2,
            "category": None,
        }
    ]


@pytest.mark.asyncio
async def test_herenow_publish_uses_three_step_flow() -> None:
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        if request.method == "POST" and request.url.path == "/api/v1/publish":
            payload = json.loads(request.content)
            assert payload["displayName"] == "Family page"
            assert payload["files"][0]["path"] == "index.html"
            assert len(payload["files"][0]["hash"]) == 64
            return httpx.Response(
                200,
                json={
                    "slug": "family-page-a1b2",
                    "siteUrl": "https://family-page-a1b2.here.now/",
                    "upload": {
                        "versionId": "v1",
                        "uploads": [
                            {
                                "path": "index.html",
                                "url": "https://uploads.here.now/site/index.html",
                                "headers": {"Content-Type": "text/html"},
                            }
                        ],
                        "skipped": [],
                        "finalizeUrl": "https://here.now/api/v1/publish/family-page-a1b2/finalize",
                    },
                },
            )
        if request.method == "PUT" and request.url.host == "uploads.here.now":
            assert request.content == b"<h1>Hello</h1>"
            return httpx.Response(200)
        if request.method == "POST" and request.url.path.endswith("/finalize"):
            assert json.loads(request.content) == {"versionId": "v1"}
            return httpx.Response(
                200,
                json={"success": True, "slug": "family-page-a1b2", "siteUrl": "https://family-page-a1b2.here.now/"},
            )
        return httpx.Response(404, text="unexpected request")

    client = HereNowClient(settings(HERENOW_API_KEY="hnk_test"), transport=httpx.MockTransport(handler))
    result = await client.publish(
        display_name="Family page",
        files=[{"path": "index.html", "content": "<h1>Hello</h1>", "content_type": "text/html"}],
    )

    assert result == {
        "ok": True,
        "slug": "family-page-a1b2",
        "site_url": "https://family-page-a1b2.here.now/",
        "files_count": 1,
        "updated": False,
        "password_protected": False,
    }
    assert requests == [
        ("POST", "https://here.now/api/v1/publish"),
        ("PUT", "https://uploads.here.now/site/index.html"),
        ("POST", "https://here.now/api/v1/publish/family-page-a1b2/finalize"),
    ]
