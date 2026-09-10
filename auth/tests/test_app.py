from cryptography.fernet import Fernet

from app import create_app


def configured_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": tmp_path / "test.db",
            "VAULT_KEY": Fernet.generate_key(),
        }
    )


def test_health(tmp_path):
    app = configured_app(tmp_path)
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.json == {
        "origin": "http://localhost",
        "rp_id": "localhost",
        "status": "ok",
    }


def test_webauthn_scope_defaults_to_public_request_origin(tmp_path):
    app = configured_app(tmp_path)
    client = app.test_client()

    health = client.get(
        "/health",
        base_url="https://api.plsreload.de",
        headers={"Origin": "https://api.plsreload.de"},
    )
    options = client.post(
        "/api/register/options",
        base_url="https://api.plsreload.de",
        headers={"Origin": "https://api.plsreload.de"},
        json={"username": "felix", "password": "secret", "type": "fingerprint"},
    )

    assert health.json["rp_id"] == "api.plsreload.de"
    assert health.json["origin"] == "https://api.plsreload.de"
    assert options.status_code == 200
    assert options.json["rp"]["id"] == "api.plsreload.de"


def test_stale_config_cannot_emit_an_invalid_rp_id(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": tmp_path / "test.db",
            "VAULT_KEY": Fernet.generate_key(),
            "RP_ID": "old-api.example.net",
            "ORIGIN": "https://old-api.example.net",
            "SESSION_COOKIE_SECURE": False,
        }
    )
    client = app.test_client()

    response = client.post(
        "/api/register/options",
        base_url="https://api.plsreload.de",
        headers={"Origin": "https://api.plsreload.de"},
        json={"username": "felix", "password": "secret", "type": "fingerprint"},
    )

    assert response.status_code == 200
    assert response.json["rp"]["id"] == "api.plsreload.de"
    with client.session_transaction(base_url="https://api.plsreload.de") as browser_session:
        assert browser_session["registration"]["rp_id"] == "api.plsreload.de"
        assert browser_session["registration"]["origin"] == "https://api.plsreload.de"


def test_related_origins_well_known_document(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": tmp_path / "test.db",
            "VAULT_KEY": Fernet.generate_key(),
            "ORIGIN": "https://api.plsreload.de",
            "RP_ID": "api.plsreload.de",
            "WEBAUTHN_RELATED_ORIGINS": (
                "https://api.plsreload.de, https://app.cube-kingdom.de,"
                "javascript:alert(1),https://invalid.example/path"
            ),
        }
    )

    response = app.test_client().get("/.well-known/webauthn")

    assert response.status_code == 200
    assert response.content_type == "application/json"
    assert response.json == {
        "origins": [
            "https://api.plsreload.de",
            "https://app.cube-kingdom.de",
        ]
    }


def test_browser_routes_work_without_query_parameters(tmp_path):
    client = configured_app(tmp_path).test_client()

    register = client.get("/register")
    authenticate = client.get("/get")

    assert register.status_code == 200
    assert b"Passkey erstellen" in register.data
    assert b'id="password"' in register.data
    assert authenticate.status_code == 200
    assert b"Zugangsdaten abrufen" in authenticate.data
    assert b'id="username"' in authenticate.data


def test_https_origin_enables_secure_session_cookie(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": tmp_path / "test.db",
            "VAULT_KEY": Fernet.generate_key(),
            "RP_ID": "api.plsreload.de",
            "ORIGIN": "https://api.plsreload.de",
        }
    )

    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_registration_rejects_invalid_type(tmp_path):
    app = configured_app(tmp_path)
    response = app.test_client().post(
        "/api/register/options",
        json={"username": "max", "password": "secret", "type": "magic"},
    )
    assert response.status_code == 400
    assert "type=fido|fingerprint" in response.json["error"]


def test_authentication_unknown_user(tmp_path):
    app = configured_app(tmp_path)
    response = app.test_client().post("/api/authenticate/options", json={"username": "nobody"})
    assert response.status_code == 400
    assert response.json == {"error": "Unbekannter Benutzer"}


def test_android_asset_links_requires_certificate(tmp_path):
    response = configured_app(tmp_path).test_client().get("/.well-known/assetlinks.json")
    assert response.status_code == 503
    assert response.json == {"error": "ANDROID_CERT_SHA256 ist nicht gesetzt"}


def test_android_asset_links_document(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": tmp_path / "test.db",
            "VAULT_KEY": Fernet.generate_key(),
            "ANDROID_APP_PACKAGE": "de.plsreload.passkey_vault",
            "ANDROID_CERT_SHA256": "AA:BB, cc:dd",
        }
    )
    response = app.test_client().get("/.well-known/assetlinks.json")
    assert response.status_code == 200
    assert response.json == [
        {
            "relation": ["delegate_permission/common.get_login_creds"],
            "target": {
                "namespace": "android_app",
                "package_name": "de.plsreload.passkey_vault",
                "sha256_cert_fingerprints": ["AA:BB", "CC:DD"],
            },
        }
    ]
