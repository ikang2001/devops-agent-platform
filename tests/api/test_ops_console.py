import httpx

from devops_agent_platform.bootstrap.app import create_app


async def test_ops_console_serves_hardened_shell_without_credentials() -> None:
    app = create_app(runtime_enabled=False)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get("/console")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers[
        "content-security-policy"
    ]
    assert 'src="/console/assets/app.js"' in response.text
    assert 'id="remediation-form"' in response.text
    assert 'id="remediation-evidence-ids"' in response.text
    assert "候选下载仍需离线匿名化和人工策展" in response.text
    assert "仅保存在当前页面内存" in response.text
    assert "Bearer token_" not in response.text


async def test_ops_console_only_serves_allowlisted_assets() -> None:
    app = create_app(runtime_enabled=False)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        script = await client.get("/console/assets/app.js")
        stylesheet = await client.get("/console/assets/styles.css")
        unknown = await client.get("/console/assets/secrets.env")
        traversal = await client.get("/console/assets/..%2F..%2F.env")

    assert script.status_code == 200
    assert script.headers["content-type"].startswith(
        "application/javascript"
    )
    assert 'sessionStorage.setItem("ops.token"' not in script.text
    assert 'sessionStorage.getItem("ops.token"' not in script.text
    assert "/remediation-plans" in script.text
    assert "/evaluation-candidate" in script.text
    assert "downloadEvaluationCandidate" in script.text
    assert "review_required !== true" in script.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert unknown.status_code == 404
    assert traversal.status_code == 404
