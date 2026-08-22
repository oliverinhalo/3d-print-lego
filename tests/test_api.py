"""HTTP API behaviour, including security handling (spec sections 19, 20, 26)."""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import AppContext
from app.main import create_app


@pytest.fixture
def client(settings, monkeypatch, registry):
    """A TestClient wired to the fake providers and a temporary database."""
    import app.api.routes as routes
    import app.config as config

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "_limiter", None)

    context = AppContext.create(settings)
    context.registry = registry
    context.worker.registry = registry

    application = create_app()
    application.dependency_overrides = {}
    with TestClient(application) as test_client:
        test_client.app.state.context = context
        yield test_client


def wait_for_completion(client, job_id, attempts=200):
    import time
    for _ in range(attempts):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("complete", "partial", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


class TestGenerateEndpoint:
    @pytest.mark.parametrize("value", ["77263", "#77263", "LEGO 77263"])
    def test_accepted_input_forms(self, client, value):
        response = client.post("/api/generate", json={"set_number": value})
        assert response.status_code == 202
        body = response.json()
        assert body["set_num"] == "77263-1"
        assert body["job_id"]

    def test_it_returns_immediately_with_a_job_id(self, client):
        """The request must not block for the whole generation."""
        response = client.post("/api/generate", json={"set_number": "77263"})
        assert response.status_code == 202
        assert response.json()["job_id"]

    @pytest.mark.parametrize("value", ["", "   ", "abc", "!!!", "x" * 200])
    def test_invalid_input_is_rejected(self, client, value):
        assert client.post("/api/generate", json={"set_number": value}).status_code in (400, 422)

    def test_a_missing_field_is_rejected(self, client):
        assert client.post("/api/generate", json={}).status_code == 422

    def test_a_full_run_finishes_and_can_be_downloaded(self, client):
        job_id = client.post("/api/generate", json={"set_number": "77263"}).json()["job_id"]
        body = wait_for_completion(client, job_id)

        assert body["status"] == "complete"
        download = client.get(f"/api/jobs/{job_id}/download")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        assert "attachment" in download.headers["content-disposition"]
        assert download.content[:2] == b"PK"


class TestJobEndpoints:
    def test_unknown_job_returns_404(self, client):
        assert client.get("/api/jobs/doesnotexist").status_code == 404
        assert client.get("/api/jobs/doesnotexist/download").status_code == 404

    def test_parts_are_listed(self, client):
        job_id = client.post("/api/generate", json={"set_number": "77263"}).json()["job_id"]
        wait_for_completion(client, job_id)

        parts = client.get(f"/api/jobs/{job_id}/parts").json()["parts"]
        assert parts and all("part_num" in p and "quantity" in p for p in parts)

    def test_the_event_stream_is_server_sent_events(self, client):
        job_id = client.post("/api/generate", json={"set_number": "77263"}).json()["job_id"]
        with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            payload = ""
            for chunk in response.iter_text():
                payload += chunk
                if "job_complete" in payload or len(payload) > 60_000:
                    break
        assert "data:" in payload
        assert "set_found" in payload

    def test_retry_requires_failed_parts(self, client):
        job_id = client.post("/api/generate", json={"set_number": "77263"}).json()["job_id"]
        wait_for_completion(client, job_id)
        assert client.post(f"/api/jobs/{job_id}/retry").status_code == 400


class TestSetLookup:
    def test_preview_returns_the_set(self, client):
        body = client.get("/api/sets/77263").json()
        assert body["set"]["set_num"] == "77263-1"
        assert body["set"]["display_number"] == "77263"

    def test_unknown_set_is_a_404(self, client):
        assert client.get("/api/sets/99999").status_code == 404

    def test_search_returns_results(self, client):
        assert "results" in client.get("/api/sets?q=test").json()

    def test_a_very_short_query_returns_nothing(self, client):
        assert client.get("/api/sets?q=a").json()["results"] == []


class TestSecurity:
    @pytest.mark.parametrize("path", [
        "/api/jobs/..%2F..%2Fetc%2Fpasswd/download",
        "/api/jobs/....//....//etc/passwd/download",
    ])
    def test_path_traversal_in_a_job_id_is_refused(self, client, path):
        assert client.get(path).status_code in (404, 400)

    def test_a_user_cannot_ask_the_server_to_fetch_a_url(self, client):
        """No endpoint accepts a URL: SSRF has no entry point."""
        response = client.post("/api/generate",
                               json={"set_number": "http://169.254.169.254/latest/meta-data/"})
        assert response.status_code in (400, 422)

    def test_sql_injection_in_the_set_number_is_rejected(self, client):
        response = client.post("/api/generate",
                               json={"set_number": "77263'; DROP TABLE sets;--"})
        assert response.status_code in (400, 422)

    def test_security_headers_are_present(self, client):
        response = client.get("/api/health")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"

    def test_rate_limiting_kicks_in(self, client, settings):
        settings.rate_limit_requests = 3
        import app.api.routes as routes
        routes._limiter = None

        codes = [client.post("/api/generate", json={"set_number": "77263"}).status_code
                 for _ in range(6)]
        assert 429 in codes, "expected the rate limiter to reject a burst"


class TestHealth:
    def test_health_reports_providers_and_cache(self, client):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert "providers" in body and "cache" in body and "jobs" in body
