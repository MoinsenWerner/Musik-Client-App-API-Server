from __future__ import annotations

import base64
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, jsonify, render_template, request, session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "development-only-change-me"),
        DATABASE=os.getenv("DATABASE", str(Path(__file__).with_name("vault.db"))),
        RP_ID=os.getenv("RP_ID"),
        RP_NAME=os.getenv("RP_NAME", "Tasker Passkey Vault"),
        ORIGIN=os.getenv("ORIGIN"),
        WEBAUTHN_RELATED_ORIGINS=os.getenv(
            "WEBAUTHN_RELATED_ORIGINS", "https://api.plsreload.de"
        ),
        VAULT_KEY=os.getenv("VAULT_KEY"),
        CHALLENGE_TTL=300,
    )
    if config:
        app.config.update(config)
    if not config or "SESSION_COOKIE_SECURE" not in config:
        app.config["SESSION_COOKIE_SECURE"] = (
            urlparse(app.config.get("ORIGIN") or "").scheme == "https"
        )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    def database() -> sqlite3.Connection:
        connection = sqlite3.connect(app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        connection.execute(
            """CREATE TABLE IF NOT EXISTS credentials (
                username TEXT PRIMARY KEY,
                encrypted_password BLOB NOT NULL,
                credential_id BLOB NOT NULL UNIQUE,
                public_key BLOB NOT NULL,
                sign_count INTEGER NOT NULL DEFAULT 0,
                passkey_type TEXT NOT NULL
            )"""
        )
        return connection

    def cipher() -> Fernet:
        key = app.config.get("VAULT_KEY")
        if not key:
            raise RuntimeError("VAULT_KEY ist nicht gesetzt")
        return Fernet(key.encode() if isinstance(key, str) else key)

    def payload() -> dict[str, Any]:
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("JSON-Objekt erwartet")
        return value

    def webauthn_context() -> tuple[str, str]:
        """Return the configured WebAuthn scope or derive it from this request."""
        configured_rp_id = app.config.get("RP_ID")
        configured_origin = app.config.get("ORIGIN")
        if configured_rp_id and configured_origin:
            return str(configured_rp_id), str(configured_origin).rstrip("/")

        request_host = urlparse(request.host_url).hostname
        if not request_host:
            raise RuntimeError("Der öffentliche Hostname konnte nicht bestimmt werden")
        request_host = request_host.lower()
        request_origin = request.headers.get("Origin", "").rstrip("/")
        parsed_origin = urlparse(request_origin)
        if (
            parsed_origin.scheme in {"http", "https"}
            and parsed_origin.hostname
            and parsed_origin.hostname.lower() == request_host
        ):
            return request_host, request_origin

        return request_host, request.host_url.rstrip("/")

    def related_origins() -> list[str]:
        """Build the explicit HTTPS origin allow-list for Related Origin Requests."""
        configured = app.config.get("WEBAUTHN_RELATED_ORIGINS", "")
        values = (
            configured
            if isinstance(configured, (list, tuple, set))
            else str(configured).split(",")
        )
        candidates = [app.config.get("ORIGIN"), *values]
        origins: list[str] = []
        for candidate in candidates:
            origin = str(candidate or "").strip().rstrip("/")
            parsed = urlparse(origin)
            if (
                not origin
                or parsed.scheme != "https"
                or not parsed.hostname
                or parsed.path not in {"", "/"}
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                continue
            if origin not in origins:
                origins.append(origin)
        return origins

    def remember(kind: str, username: str, challenge: bytes, **extra: Any) -> None:
        session[kind] = {
            "username": username,
            "challenge": _b64(challenge),
            "created": int(time.time()),
            **extra,
        }

    def recalled(kind: str) -> dict[str, Any]:
        state = session.pop(kind, None)
        if not state or time.time() - state["created"] > app.config["CHALLENGE_TTL"]:
            raise ValueError("Challenge fehlt oder ist abgelaufen")
        return state

    @app.errorhandler(ValueError)
    def bad_request(error: ValueError):
        return jsonify(error=str(error)), 400

    @app.errorhandler(RuntimeError)
    def configuration_error(error: RuntimeError):
        return jsonify(error=str(error)), 503

    @app.get("/health")
    def health():
        rp_id, origin = webauthn_context()
        return jsonify(
            status="ok",
            origin=origin,
            rp_id=rp_id,
        )

    @app.get("/.well-known/webauthn")
    def webauthn_related_origins():
        return jsonify(origins=related_origins())

    @app.get("/register")
    def register_page():
        return render_template(
            "webauthn.html",
            mode="register",
            username=request.args.get("username", ""),
            password=request.args.get("password", ""),
            passkey_type=request.args.get("type", "fingerprint"),
        )

    @app.get("/get")
    def authenticate_page():
        return render_template(
            "webauthn.html",
            mode="authenticate",
            username=request.args.get("username", ""),
            password="",
            passkey_type="",
        )

    @app.post("/api/register/options")
    def registration_options():
        data = payload()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        passkey_type = str(data.get("type", "fingerprint"))
        if not username or not password or passkey_type not in {"fido", "fingerprint"}:
            raise ValueError("username, password und type=fido|fingerprint sind erforderlich")
        attachment = (
            AuthenticatorAttachment.CROSS_PLATFORM
            if passkey_type == "fido"
            else AuthenticatorAttachment.PLATFORM
        )
        rp_id, origin = webauthn_context()
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=app.config["RP_NAME"],
            user_id=username.encode(),
            user_name=username,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=attachment,
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        remember(
            "registration",
            username,
            options.challenge,
            encrypted_password=cipher().encrypt(password.encode()).decode(),
            passkey_type=passkey_type,
            rp_id=rp_id,
            origin=origin,
        )
        return app.response_class(options_to_json(options), mimetype="application/json")

    @app.post("/api/register/verify")
    def registration_verify():
        state = recalled("registration")
        verification = verify_registration_response(
            credential=payload(),
            expected_challenge=_unb64(state["challenge"]),
            expected_rp_id=state["rp_id"],
            expected_origin=state["origin"],
            require_user_verification=True,
        )
        with database() as db:
            db.execute(
                """INSERT INTO credentials VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                  encrypted_password=excluded.encrypted_password,
                  credential_id=excluded.credential_id,
                  public_key=excluded.public_key,
                  sign_count=excluded.sign_count,
                  passkey_type=excluded.passkey_type""",
                (
                    state["username"],
                    state["encrypted_password"].encode(),
                    verification.credential_id,
                    verification.credential_public_key,
                    verification.sign_count,
                    state["passkey_type"],
                ),
            )
        return jsonify(ok=True, username=state["username"])

    @app.post("/api/authenticate/options")
    def authentication_options():
        username = str(payload().get("username", "")).strip()
        with database() as db:
            row = db.execute(
                "SELECT credential_id FROM credentials WHERE username = ?", (username,)
            ).fetchone()
        if row is None:
            raise ValueError("Unbekannter Benutzer")
        rp_id, origin = webauthn_context()
        options = generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=[PublicKeyCredentialDescriptor(id=row["credential_id"])],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        remember(
            "authentication",
            username,
            options.challenge,
            rp_id=rp_id,
            origin=origin,
        )
        return app.response_class(options_to_json(options), mimetype="application/json")

    @app.post("/api/authenticate/verify")
    def authentication_verify():
        state = recalled("authentication")
        with database() as db:
            row = db.execute(
                "SELECT * FROM credentials WHERE username = ?", (state["username"],)
            ).fetchone()
            if row is None:
                raise ValueError("Unbekannter Benutzer")
            verification = verify_authentication_response(
                credential=payload(),
                expected_challenge=_unb64(state["challenge"]),
                expected_rp_id=state["rp_id"],
                expected_origin=state["origin"],
                credential_public_key=row["public_key"],
                credential_current_sign_count=row["sign_count"],
                require_user_verification=True,
            )
            db.execute(
                "UPDATE credentials SET sign_count = ? WHERE username = ?",
                (verification.new_sign_count, state["username"]),
            )
        try:
            password = cipher().decrypt(row["encrypted_password"]).decode()
        except InvalidToken as error:
            raise RuntimeError("VAULT_KEY passt nicht zur Datenbank") from error
        return jsonify(username=state["username"], password=password)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=2050)
