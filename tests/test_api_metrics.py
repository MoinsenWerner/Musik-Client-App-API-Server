import json

import api_metrics
import servus


def test_metrics_summary_contains_endpoint_user_counts_and_timelines(tmp_path):
    database = tmp_path / "metrics.db"
    api_metrics.initialize_metrics_database(database)
    now = 2_000_000_000
    api_metrics.record_request(database, "/player", "GET", "felix", 200, now - 60)
    api_metrics.record_request(database, "/player", "GET", "felix", 500, now - 30)
    api_metrics.record_request(database, "/queue", "POST", "tasker", 201, now - 10)

    summary = api_metrics.build_metrics_summary(database, "1h", now=now)

    assert summary["total_calls"] == 3
    assert summary["unique_user_count"] == 2
    assert [(user["username"], user["total_calls"]) for user in summary["users"]] == [
        ("felix", 2),
        ("tasker", 1),
    ]
    assert sum(point["count"] for point in summary["users"][0]["timeline"]) == 2
    assert summary["endpoints"][0]["endpoint"] == "/player"
    assert summary["endpoints"][0]["total_calls"] == 2
    assert summary["endpoints"][0]["successful_calls"] == 1
    assert sum(point["count"] for point in summary["endpoints"][0]["timeline"]) == 2
    assert sum(point["count"] for point in summary["endpoints"][0]["successful_timeline"]) == 1
    assert max(point["count"] for point in summary["unique_users_timeline"]) == 2
    assert sum(
        point["count"] for point in summary["endpoints"][0]["users"][0]["timeline"]
    ) == 2


def test_metrics_routes_and_client_mount(monkeypatch, tmp_path):
    database = tmp_path / "metrics.db"
    api_metrics.initialize_metrics_database(database)
    monkeypatch.setattr(servus, "METRICS_DATABASE_FILE", str(database))
    client = servus.app.test_client()

    client_response = client.get("/client/health")
    assert client_response.status_code == 200
    assert client_response.get_json() == {"service": "client", "status": "ok"}

    json_response = client.get("/api-metrics?version=cli&period=1h")
    assert json_response.status_code == 200
    assert json_response.get_json()["total_calls"] == 1
    assert json_response.get_json()["endpoints"][0]["endpoint"] == "/client/health"

    html_response = client.get("/api-metrics")
    assert html_response.status_code == 200
    assert b"API Metrics" in html_response.data
    assert b"Individueller Vergleich" in html_response.data
    assert b"contextmenu" in html_response.data
    assert b"chart-tooltip" in html_response.data

    bad_period = client.get("/api-metrics?version=cli&period=nope")
    assert bad_period.status_code == 400


def test_route_catalog_includes_every_application():
    routes = servus.collect_routes()
    sources = {route["source"] for route in routes}
    rules = {route["rule"] for route in routes}

    assert {"servus.py", "chat.py", "auth/app.py", "client/ok.py"} <= sources
    assert "/client/health" in rules
    assert "/auth/api/register/options" in rules
    assert not any(route["source"] == "client/ok.py" and not route["rule"].startswith("/client") for route in routes)


def test_cli_response_is_json_serializable(tmp_path):
    database = tmp_path / "metrics.db"
    api_metrics.initialize_metrics_database(database)
    summary = api_metrics.build_metrics_summary(database, "24h", now=2_000_000_000)
    json.dumps(summary)
