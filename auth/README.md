# Passkey-Vault für Tasker

Dieser Flask-Dienst speichert Zugangsdaten verschlüsselt und gibt sie erst nach
einer erfolgreichen WebAuthn-Authentifizierung zurück. Er lauscht standardmäßig
auf `0.0.0.0:4099`.

> **Wichtige Plattformgrenze:** Eine reine Tasker-HTTP-Aktion kann kein
> Fingerabdruck-/PIN-/FIDO-Fenster öffnen. WebAuthn darf nur in einem sicheren
> Browser-Kontext (HTTPS; `localhost` ist die einzige HTTP-Ausnahme) oder in
> einer Android-App mit Credential Manager ausgeführt werden. Der mitgelieferte
> Web-Client öffnet nur die WebAuthn-Oberfläche; für wirklich browserlosen
> Betrieb muss eine kleine Android-/Tasker-Plugin-App die JSON-Endpunkte aufrufen
> und die erhaltenen Optionen an Android Credential Manager übergeben.

## Installation und Start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
source ./set-secrets.sh
python app.py
```

`set-secrets.sh` erklärt alle vier Werte, fragt verständlich danach und erzeugt
auf Wunsch sichere neue Schlüssel. Das Skript muss mit `source` geladen werden,
damit die exportierten Variablen anschließend für Flask verfügbar sind.

### Cloudflare Tunnel für `auth.extrahelden.de`

Der Tunnel darf intern weiterhin auf `http://localhost:4099` zeigen. Nach außen
entscheidend ist ausschließlich die HTTPS-Adresse. Für den genannten Tunnel
müssen diese Werte gelten (sie sind die Vorgaben im Setup-Skript):

```bash
export RP_ID=auth.extrahelden.de
export ORIGIN=https://auth.extrahelden.de
```

Danach sind die Seiten unter folgenden öffentlichen URLs verfügbar:

* `https://auth.extrahelden.de/register`
* `https://auth.extrahelden.de/get`

Flask setzt bei einer HTTPS-`ORIGIN` automatisch ein Secure-Session-Cookie. Die
von Cloudflare an Flask weitergeleitete interne HTTP-Verbindung beeinträchtigt
WebAuthn nicht, weil Registrierung und Authentifizierung im Browser unter der
öffentlichen HTTPS-Origin stattfinden.

Im Netzwerk muss vor Flask ein HTTPS-Reverse-Proxy stehen. `RP_ID` ist nur der
Hostname, `ORIGIN` enthält Schema und gegebenenfalls Port. Ohne dauerhaftes
`VAULT_KEY` können gespeicherte Passwörter nach einem Neustart nicht mehr
entschlüsselt werden.

## Aufrufe

* Registrierung (kompatibel zur gewünschten URL):
  `https://example.local:4099/register?username=max&password=geheim&create-passkey&type=fido`
* Plattform-Passkey (Fingerabdruck/PIN): `type=fingerprint`
* Abruf: `https://example.local:4099/get?username=max`

Beide Seiten können auch ganz ohne URL-Parameter geöffnet werden. In diesem Fall
erscheint ein verständliches Formular für Benutzername, Passwort und Passkey-Typ
beziehungsweise für den abzurufenden Benutzernamen.

Passwörter in URLs landen häufig in Verlauf und Proxy-Logs. Deshalb ist
`POST /api/register/options` mit JSON (`username`, `password`, `type`) die
empfohlene Schnittstelle. Die HTML-Seite ist nur ein Referenz-Client.

## API-Ablauf für Android/Tasker-Plugin

1. `POST /api/register/options`, WebAuthn-Credential erstellen, dann dessen JSON
   an `POST /api/register/verify` senden.
2. `POST /api/authenticate/options` mit `username`, Assertion erzeugen, dann an
   `POST /api/authenticate/verify` senden.
3. Die zweite Antwort enthält bei Erfolg `username` und `password`.

Challenges liegen kurzlebig in der Flask-Session. Cookies müssen deshalb bei
beiden Requests eines Ablaufs erhalten bleiben.

## Entwicklung

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```
