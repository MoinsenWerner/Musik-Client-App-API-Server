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
    assert b"Tasker-WebView" in authenticate.data


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
    app = configured_app(tmp_path)
    app.config["ANDROID_CERT_SHA256_FILE"] = tmp_path / "missing-fingerprint.txt"
    response = app.test_client().get("/.well-known/assetlinks.json")
    assert response.status_code == 503
    assert "ANDROID_CERT_SHA256 ist nicht gesetzt" in response.json["error"]


def test_android_asset_links_document(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": tmp_path / "test.db",
            "VAULT_KEY": Fernet.generate_key(),
            "ANDROID_APP_PACKAGE": "de.plsreload.passkey_vault",
            "ANDROID_CERT_SHA256": (
                "F7:0D:DB:43:03:65:B0:C2:D9:BD:13:B9:4A:56:DD:68:"
                "2B:D0:0A:A1:E1:AD:8E:84:D8:59:B0:25:EA:06:18:8F"
            ),
        }
    )
    response = app.test_client().get("/.well-known/assetlinks.json")
    assert response.status_code == 200
    assert response.json == [
        {
            "relation": [
                "delegate_permission/common.handle_all_urls",
                "delegate_permission/common.get_login_creds",
            ],
            "target": {
                "namespace": "android_app",
                "package_name": "de.plsreload.passkey_vault",
                "sha256_cert_fingerprints": [
                    "F7:0D:DB:43:03:65:B0:C2:D9:BD:13:B9:4A:56:DD:68:"
                    "2B:D0:0A:A1:E1:AD:8E:84:D8:59:B0:25:EA:06:18:8F"
                ],
            },
        }
    ]


def test_android_asset_links_reads_and_normalizes_fingerprint_file(tmp_path):
    fingerprint_file = tmp_path / "cert-sha256.txt"
    fingerprint_file.write_text("aabbccddeeff00112233445566778899" * 2)
    app = configured_app(tmp_path)
    app.config["ANDROID_CERT_SHA256_FILE"] = fingerprint_file

    response = app.test_client().get("/.well-known/assetlinks.json")

    assert response.status_code == 200
    assert response.json[0]["target"]["sha256_cert_fingerprints"] == [
        "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:"
        "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"
    ]


def test_android_asset_links_rejects_malformed_fingerprint(tmp_path):
    app = configured_app(tmp_path)
    app.config["ANDROID_CERT_SHA256"] = "F7D41C8EFB7DBFD5B573F566FD82B9250A161581BC"

    response = app.test_client().get("/.well-known/assetlinks.json")

    assert response.status_code == 503
    assert response.json["received_hex_characters"] == 42
    assert "64 Hex-Zeichen" in response.json["error"]
