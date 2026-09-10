Vollständig browserlos mit einer Android-Begleit-App
Damit die ursprüngliche Anforderung genau erfüllt wird, muss der Ablauf so aussehen:

Tasker
  → Android-Begleit-App
  → POST /api/.../options
  → Android Credential Manager
  → Fingerabdruck/PIN/FIDO
  → POST /api/.../verify
  → Ergebnis zurück an Tasker
Die erforderlichen Serverendpunkte existieren bereits. 

Was die Begleit-App erledigen muss
Registrierung
Die App muss:

Benutzername, Passwort und Typ von Tasker entgegennehmen.

Diese Daten als JSON senden:

POST https://api.plsreload.de/api/register/options
Content-Type: application/json
{
  "username": "max",
  "password": "geheim",
  "type": "fingerprint"
}
Das von Flask gesetzte Session-Cookie behalten.

Die erhaltenen WebAuthn-Optionen an Android Credential Manager übergeben.

Das erzeugte Passkey-Credential in WebAuthn-JSON umwandeln.

Dieses JSON mit demselben Session-Cookie an folgende Route senden:

POST https://api.plsreload.de/api/register/verify
Die vollständige Serverantwort an Tasker zurückgeben.

Die Options-Route erzeugt die WebAuthn-Challenge und speichert die verschlüsselte Passwortangabe vorübergehend in der Session.  Die Verify-Route prüft das Credential und speichert es anschließend in der Vault-Datenbank. 

Authentifizierung
Die App muss:

Den Benutzernamen von Tasker entgegennehmen.

Folgendes senden:

POST https://api.plsreload.de/api/authenticate/options
Content-Type: application/json
{
  "username": "max"
}
Das Session-Cookie behalten.

Die Optionen an Android Credential Manager übergeben.

Den Fingerabdruck-/PIN-/FIDO-Dialog anzeigen.

Die signierte Assertion an folgende Route senden:

POST https://api.plsreload.de/api/authenticate/verify
Die Antwort an Tasker zurückgeben:

{
  "username": "max",
  "password": "geheim"
}
Die Challenge wird serverseitig nur vorübergehend in der Flask-Session gespeichert und nach dem Abruf entfernt.  Deshalb müssen Options- und Verify-Anfrage zwingend denselben Cookie-Speicher verwenden.

Tasker-Konfiguration mit einer solchen Begleit-App
Die genaue Bezeichnung der Aktionen hängt davon ab, wie die Begleit-App integriert wird. Am zuverlässigsten wäre ein Tasker-Plug-in mit zwei Aktionen:

Register Passkey

Get Password

Tasker-Aufgabe „Passkey registrieren“
Aktion 1: Variablen setzen
%username = max
%password = geheim
%passkey_type = fingerprint
Aktion 2: Plug-in aufrufen
Plugin: Passkey Vault
Aktion: Register Passkey

Username: %username
Password: %password
Type: %passkey_type
Aktion 3: Ergebnis übernehmen
Das Plug-in sollte folgende lokale Tasker-Variablen setzen:

%passkey_result
%passkey_error
%passkey_status
Erfolgsbeispiel:

%passkey_status = 200
%passkey_result = {"ok":true,"username":"max"}
Wenn du zwingend %http_data verwenden möchtest:

Variable setzen
Name: %http_data
Wert: %passkey_result
Danach enthält %http_data:

{"ok":true,"username":"max"}
Tasker-Aufgabe „Passwort abrufen“
Aktion 1: Benutzername setzen
%username = max
Aktion 2: Plug-in aufrufen
Plugin: Passkey Vault
Aktion: Get Password

Username: %username
Jetzt sollte der Android-Passkey-Dialog erscheinen.

Aktion 3: Ergebnis übernehmen
Variable setzen
Name: %http_data
Wert: %passkey_result
Danach enthält %http_data beispielsweise:

{
  "username": "max",
  "password": "geheim"
}
Aktion 4: Passwort aus JSON lesen
Wenn deine Tasker-Version JSON-Feldzugriff unterstützt, kannst du je nach Tasker-Version beispielsweise auf das Feld über eine JSON-Strukturvariable zugreifen. Alternativ verwendest du die Tasker-Aktion „JSON lesen“ bzw. „JSON Read“:

JSON:
%http_data

Feld:
password

Ausgabe:
%retrieved_password
Danach gilt:

%retrieved_password = geheim
Keine unsichere Tasker-Ersatzlösung verwenden
Taskers eigener biometrischer Dialog könnte zwar lokal einen Fingerabdruck abfragen, ist aber kein Ersatz für WebAuthn. Ein unsicherer Ablauf wäre beispielsweise:

Tasker Authentication Dialog
→ bei Erfolg GET /password?username=max
Davon ist dringend abzuraten:

Der Server erhält dabei keine kryptografisch signierte Passkey-Assertion.

Ein Angreifer könnte die Passwort-Route direkt aufrufen.

Der Server kann nicht feststellen, ob Taskers lokaler Dialog tatsächlich ausgeführt wurde.

Es gäbe keine Bindung an RP-ID, Origin und Server-Challenge.

Replay- und Umgehungsangriffe wären möglich.

Der bestehende Server verlangt dagegen ausdrücklich eine verifizierte WebAuthn-Antwort mit Benutzerverifikation. 

