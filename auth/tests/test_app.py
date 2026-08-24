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
        "origin": "http://localhost:4099",
        "rp_id": "localhost",
        "status": "ok",
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
            "RP_ID": "auth.extrahelden.de",
            "ORIGIN": "https://auth.extrahelden.de",
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
