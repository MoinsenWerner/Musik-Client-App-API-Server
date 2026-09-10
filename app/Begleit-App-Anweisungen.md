# Vollständig browserlos mit einer Android-Begleit-App

Damit die ursprüngliche Anforderung genau erfüllt wird, läuft der Ablauf so ab:

```text
Tasker
  → Android-Begleit-App
  → POST /api/.../options
  → Android Credential Manager
  → Fingerabdruck/PIN/FIDO
  → POST /api/.../verify
  → Ergebnis zurück an Tasker
```

Die erforderlichen Serverendpunkte existieren bereits. Die Begleit-App verwendet Android Credential Manager und öffnet keinen Browser.

## Was die Begleit-App erledigt

### Registrierung

Die App:

1. nimmt Benutzername, Passwort und Typ von Tasker entgegen;
2. sendet diese Daten als JSON:

   ```http
   POST https://api.plsreload.de/api/register/options
   Content-Type: application/json
   ```

   ```json
   {
     "username": "max",
     "password": "geheim",
     "type": "fingerprint"
   }
   ```

3. behält das von Flask gesetzte Session-Cookie;
4. übergibt die erhaltenen WebAuthn-Optionen an Android Credential Manager;
5. wandelt das erzeugte Passkey-Credential in WebAuthn-JSON um;
6. sendet dieses JSON mit demselben Session-Cookie an:

   ```http
   POST https://api.plsreload.de/api/register/verify
   ```

7. gibt die vollständige Serverantwort an Tasker zurück.

Die Options-Route erzeugt die WebAuthn-Challenge und speichert das verschlüsselte Passwort vorübergehend in der Flask-Session. Die Verify-Route prüft das Credential und speichert es anschließend in der Vault-Datenbank.

### Authentifizierung

Die App:

1. nimmt den Benutzernamen von Tasker entgegen;
2. sendet:

   ```http
   POST https://api.plsreload.de/api/authenticate/options
   Content-Type: application/json
   ```

   ```json
   {
     "username": "max"
   }
   ```

3. behält das Session-Cookie;
4. übergibt die Optionen an Android Credential Manager;
5. zeigt den Fingerabdruck-/PIN-/FIDO-Dialog an;
6. sendet die signierte Assertion an:

   ```http
   POST https://api.plsreload.de/api/authenticate/verify
   ```

7. gibt die Antwort an Tasker zurück:

   ```json
   {
     "username": "max",
     "password": "geheim"
   }
   ```

Die Challenge wird serverseitig nur vorübergehend in der Flask-Session gespeichert und nach dem Abruf entfernt. Options- und Verify-Anfrage müssen deshalb denselben Cookie-Speicher verwenden.

## Tasker-Konfiguration

Die App stellt für Tasker zwei explizite Aktionen bereit:

- `de.plsreload.passkey_vault.REGISTER` – **Register Passkey**
- `de.plsreload.passkey_vault.AUTHENTICATE` – **Get Password**

Sie sendet das Ergebnis mit der Broadcast-Aktion `de.plsreload.passkey_vault.RESULT` zurück.

### Tasker-Aufgabe „Passkey registrieren“

#### Aktion 1: Variablen setzen

```text
%username = max
%password = geheim
%passkey_type = fingerprint
```

`%passkey_type` darf `fingerprint` oder `fido` sein.

#### Aktion 2: Begleit-App aufrufen

Füge **System → Intent senden** hinzu:

```text
Aktion: de.plsreload.passkey_vault.REGISTER
Ziel: Activity
Paket: de.plsreload.passkey_vault
Klasse: de.plsreload.passkey_vault.MainActivity
Extra 1: username:%username
Extra 2: password:%password
Extra 3: type:%passkey_type
```

#### Aktion 3: Ergebnis empfangen

Lege ein Tasker-Profil **Ereignis → System → Intent empfangen** an:

```text
Aktion: de.plsreload.passkey_vault.RESULT
```

Die Begleit-App setzt in diesem Broadcast:

```text
%passkey_result
%passkey_error
%passkey_status
```

Erfolgsbeispiel:

```text
%passkey_status = 200
%passkey_result = {"ok":true,"username":"max"}
```

Wenn das Ergebnis in `%http_data` benötigt wird:

```text
Variable setzen
Name: %http_data
Wert: %passkey_result
```

Danach enthält `%http_data`:

```json
{"ok":true,"username":"max"}
```

### Tasker-Aufgabe „Passwort abrufen“

#### Aktion 1: Benutzername setzen

```text
%username = max
```

#### Aktion 2: Begleit-App aufrufen

Füge **System → Intent senden** hinzu:

```text
Aktion: de.plsreload.passkey_vault.AUTHENTICATE
Ziel: Activity
Paket: de.plsreload.passkey_vault
Klasse: de.plsreload.passkey_vault.MainActivity
Extra 1: username:%username
```

Jetzt erscheint der Android-Credential-Manager-Dialog.

#### Aktion 3: Ergebnis übernehmen

Das oben beschriebene Profil **Intent empfangen** empfängt das Ergebnis. Führe anschließend aus:

```text
Variable setzen
Name: %http_data
Wert: %passkey_result
```

Danach enthält `%http_data` beispielsweise:

```json
{
  "username": "max",
  "password": "geheim"
}
```

#### Aktion 4: Passwort aus JSON lesen

Wenn die Tasker-Version JSON-Feldzugriff unterstützt, verwende **JSON lesen/JSON Read**:

```text
JSON: %http_data
Feld: password
Ausgabe: %retrieved_password
```

Danach gilt:

```text
%retrieved_password = geheim
```

## Alternative Deep Links

Die App akzeptiert zusätzlich:

```text
passkeyvault://register?username=max&password=geheim&type=fingerprint
passkeyvault://get?username=max
```

Für Passwörter ist der explizite Intent vorzuziehen, weil URL-Werte leichter protokolliert werden können.

## Server-Verknüpfung

Android verlangt für native Passkeys eine Digital-Asset-Links-Verknüpfung. Der Server stellt `/.well-known/assetlinks.json` bereit, sobald folgende Variablen gesetzt sind:

```bash
export ANDROID_APP_PACKAGE=de.plsreload.passkey_vault
export ANDROID_CERT_SHA256='<Ausgabe des Build-Skripts>'
```

Nach dem ersten Build zeigt `setup-and-build.sh` den SHA-256-Fingerabdruck an. Setze ihn dauerhaft auf dem Flask-Server und starte diesen neu, bevor Passkeys verwendet werden. Der von `setup-and-build.sh` erzeugte Keystore unter `/home/passkey-apk/passkey-release.jks` muss dauerhaft gesichert und bei allen späteren Builds wiederverwendet werden.

## Keine unsichere Tasker-Ersatzlösung verwenden

Taskers eigener biometrischer Dialog ist kein Ersatz für WebAuthn. Unsicher wäre beispielsweise:

```text
Tasker Authentication Dialog
→ bei Erfolg GET /password?username=max
```

Davon ist abzuraten:

- Der Server erhält keine kryptografisch signierte Passkey-Assertion.
- Ein Angreifer könnte die Passwort-Route direkt aufrufen.
- Der Server kann nicht feststellen, ob Taskers lokaler Dialog ausgeführt wurde.
- Es gäbe keine Bindung an RP-ID, Origin und Server-Challenge.
- Replay- und Umgehungsangriffe wären möglich.

Der Server verlangt stattdessen eine verifizierte WebAuthn-Antwort mit Benutzerverifikation.
