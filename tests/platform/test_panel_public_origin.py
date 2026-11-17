from fastapi.testclient import TestClient

from coverage_qa.control_panel import Manager, create_app


def test_panel_assets_use_relative_paths(tmp_path):
    manager = Manager(tmp_path / "reports")
    with TestClient(create_app(manager)) as client:
        html = client.get("/").text
        assert 'href="assets/style.css"' in html
        assert 'src="assets/app.js"' in html
        assert 'class="brand" href="./"' in html
        script = client.get("/assets/app.js").text
        assert "const PANEL_BASE = new URL('../', document.currentScript.src).pathname;" in script
        assert "PANEL_BASE + path.replace" in script
        assert "PANEL_BASE + `reports/runs/" in script


def test_configured_public_origin_allows_post_but_rejects_foreign(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_PANEL_PUBLIC_ORIGIN", "https://graphql.rebyte.fun/")
    manager = Manager(tmp_path / "reports")
    manager._catalog = []
    payload = {"node_ids": ["missing-test"]}
    headers = {"X-Panel-Token": manager.token}
    with TestClient(create_app(manager)) as client:
        assert client.post("/api/runs", json=payload, headers={
            **headers, "Origin": "https://graphql.rebyte.fun"
        }).status_code == 400
        assert client.post("/api/runs", json=payload, headers={
            **headers, "Origin": "https://graphql.rebyte.fun.attacker.invalid"
        }).status_code == 403
        assert client.post("/api/runs", json=payload, headers={
            "Origin": "https://graphql.rebyte.fun"
        }).status_code == 403
