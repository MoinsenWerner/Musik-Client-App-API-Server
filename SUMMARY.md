# Code-Zusammenfassung: Musik-Client-App-API-Server

> Stand der Analyse: 6. August 2026, Branch `main`
>
> Repository: `MoinsenWerner/Musik-Client-App-API-Server`

## 1. Zweck der Anwendung

Das Repository enthält einen monolithischen Flask-Server für den Musik-Client. Der Name beschreibt nur einen Teil des tatsächlichen Funktionsumfangs. `servus.py` kombiniert derzeit:

- einen eigenen OAuth2-ähnlichen Provider für API-Clients,
- einen Reverse Proxy zu lokalen Musik-Backends,
- einen direkten Spotify-Web-API-Modus,
- Player- und Queue-Endpunkte,
- APK-Upload, Versionsarchiv und Download,
- Updatehinweise als Textdateien,
- Client-Benachrichtigungen,
- Fehler- und Mass-Error-Reports,
- Session- und Aktions-Telemetrie,
- Positionsspeicherung und öffentliche Kartenansicht,
- Core-Data-Upload und -Download,
- Ressourcen-Downloads,
- ein ungeschütztes Verwaltungsdashboard,
- automatische SQLite-Backups nach jedem SQLAlchemy-Commit,
- automatisches Pushen dieser Backups in ein separates GitHub-Repository,
- eine automatisch erzeugte Routenübersicht.

Fast die gesamte Logik befindet sich in der einzelnen Datei `servus.py`.

## 2. Technischer Aufbau

- **Backend:** Python und Flask
- **Datenbank:** SQLite mit Flask-SQLAlchemy/SQLAlchemy
- **HTTP-Client:** `requests`
- **Passwort-Hashing:** Werkzeug
- **Versionsvergleich:** `packaging.version`
- **Frontend:** Jinja2-Templates; Dashboard als großer Inline-Template-String
- **Karte:** Daten werden serverseitig für `templates/map.html` vorbereitet
- **Logging:** rotierende Datei `log/api.log.txt`
- **Dateispeicher:** APKs, Updatehinweise, Ressourcen, Core-Data und DB-Backups im Repository-/Arbeitsverzeichnis
- **Systemabhängigkeit:** installiertes `git` für Backup-Clone, Commit und Push

Es existiert keine `requirements.txt`, kein `pyproject.toml`, kein Dockerfile und keine Testsuite. Reproduzierbare Installation und automatisierte Regressionstests fehlen damit vollständig.

Voraussichtlich benötigte Python-Pakete aus den Imports:

```text
Flask
Flask-SQLAlchemy
SQLAlchemy
requests
packaging
Werkzeug
```

## 3. Start und Laufzeit

Direktstart:

```bash
python3 servus.py
```

Die Anwendung startet auf:

```text
0.0.0.0:2050
```

Am Dateiende ist `debug=True` gesetzt. Für einen öffentlich erreichbaren Server ist das ungeeignet.

Ein produktiver Waitress-Start ist nur als auskommentierter Vorschlag vorhanden. Wird Waitress verwendet, muss es zusätzlich als Abhängigkeit dokumentiert werden.

### Instabiler Flask-Secret-Key

`app.config['SECRET_KEY']` wird bei jedem Prozessstart zufällig erzeugt. Dadurch werden alle bestehenden Flask-Sitzungen und Flash-Nachrichten nach jedem Neustart ungültig. Der Wert muss aus einer dauerhaft gesetzten Umgebungsvariable oder Secret-Datei geladen werden.

### Doppelte Bedeutung von `BASE_DIR`

Am Anfang bezeichnet `BASE_DIR` das Verzeichnis von `servus.py`. Später wird dieselbe Variable neu gesetzt:

```python
BASE_DIR = os.path.abspath("app_ressources")
```

Ab dort hängt der Ressourcenpfad vom aktuellen Arbeitsverzeichnis des Prozesses ab, nicht mehr zuverlässig vom Verzeichnis der Python-Datei. Die Variable sollte nicht überschrieben werden. Sinnvoll wären getrennte Namen wie `PROJECT_DIR` und `APP_RESOURCES_DIR`.

## 4. Repository- und Laufzeitstruktur

| Pfad | Bedeutung |
|---|---|
| `servus.py` | gesamte Serverlogik |
| `oauth2_gateway.db` | SQLite-Datenbank, aktuell im Repository versioniert |
| `templates/map.html` | Positionskarte |
| `templates/notify_add.html` | Benachrichtigung erstellen |
| `templates/notify_edit.html` | Benachrichtigungen ändern/löschen |
| `templates/routes.html` | dynamische Routenübersicht |
| `templates/upload.html` | Browser-Upload für APKs |
| `apk/latest/` | aktuelle APK zur Laufzeit |
| `apk/new-upload/` | temporärer APK-Upload |
| `apk/versions/<version>/` | archivierte APK-Versionen |
| `updates/<version>.txt` | Updatehinweise |
| `app_ressources/app-resources/` | herunterladbare App-Ressourcen |
| `uploads/core-data-abfragen.txt` | gesammelte Core-Data-Blöcke |
| `db_bak/` | lokale DB-Backups; einziger Eintrag in `.gitignore` |
| `log/api.log.txt` | rotierendes Request-/Response-Log |

Die im Repository vorhandenen APK-Dateien der Versionen 4.0.0 bis 4.0.6 sind laut Git-Dateibaum jeweils leer. Sie sind damit Platzhalter und keine auslieferbaren APKs.

`updatetracker.txt` wird vom aktuellen Python-Code nicht verwendet.

## 5. Datenmodell

### `system_config`

Speichert:

- Gateway-Modus `Server` oder `Direkt`
- Spotify Client-ID
- Spotify Client-Secret
- Spotify Refresh-Token
- Spotify Access-Token
- Ablaufzeit des Spotify-Tokens

Alle Spotify-Geheimnisse werden im Klartext in SQLite gespeichert.

### `client_credentials`

Registrierte Gateway-Clients:

- Client-ID
- gehashter Client-Secret
- zusätzlich Client-Secret im Klartext
- Name/Zuweisung
- Rolle `Client` oder `Server`
- erlaubte Scopes
- Token-Lebensdauer

Das Klartext-Secret wird für die Anzeige im Dashboard aufbewahrt. Das ist ein erhebliches Sicherheitsrisiko und sollte durch eine einmalige Anzeige beim Erstellen ersetzt werden.

### `authorization_codes`

Kurzlebige Autorisierungscodes mit Client-ID, Redirect-URI, Scope und Ablaufzeit.

### `oauth_tokens`

Gateway-Access-Tokens mit Client-ID, Scope und Ablaufzeit. Tokens werden im Klartext gespeichert. Eine Revocation-, Rotation- oder automatische Bereinigungsfunktion fehlt.

### `apk_version`

Speichert archivierte APK-Versionsnamen.

### `user_session_timeline`

Speichert Datum, Benutzername, Start- und Endzeit einer App-Sitzung.

### `user_action_timeline`

Speichert Datum, Zeit, Aktion und Benutzername.

### `error_reports`

Einzelne Soft-/Hard-Fehlerberichte mit App-Version, Task, Fehlertext, Datum, Uhrzeit und letzter Aktion.

### `mass_errorreport_errors`

Mass-Error-Berichte als großer Freitext pro Benutzer und App-Version.

### `notifications`

Benachrichtigung mit Titel, Text, Kategorie, Gruppe, optionalem PNG-Pfad und Erstellungszeit.

### `notification_deliveries`

Merkt pro Benachrichtigung und Client-ID, dass die Nachricht bereits ausgeliefert wurde.

### `user_positions`

Dynamisch aufgebautes Positionsmodell mit:

- Benutzername
- Breitengrad
- Längengrad
- Uhrzeitblock
- Datumsblock
- Kartenlink

Die Tabellen- und Spaltennamen werden über Konstanten am Dateianfang festgelegt.

## 6. Automatische Datenbank-Backups

Nach jedem erfolgreichen SQLAlchemy-Commit löst der globale `Session.after_commit`-Listener ein Backup aus.

Ablauf:

1. SQLite-Backup-API erzeugt eine konsistente Kopie in `db_bak/`.
2. Ein separates Backup-Repository wird bei Bedarf geklont.
3. Die neue Datenbankdatei wird kopiert.
4. `git pull --rebase`, `git add`, `git commit` und `git push` werden ausgeführt.

Steuerung:

```text
DB_BACKUP_GIT_ENABLED=1  # Standard: aktiviert
```

### Wichtige Folgen

- Jeder Commit, auch kleine Änderungen wie Benachrichtigungsauslieferung, erzeugt eine komplette DB-Kopie und einen Git-Push.
- Requests werden dadurch von Dateisystem, GitHub-Erreichbarkeit und Git-Performance abhängig.
- Die Zahl der Backup-Dateien und Git-Commits wächst sehr schnell.
- Bei mehreren Serverprozessen schützt der `threading.Lock` nur Threads innerhalb eines Prozesses, nicht mehrere Prozesse.
- Fehler beim Upload werden protokolliert, verhindern den ursprünglichen DB-Commit aber nicht.

### Kritisches Datenschutz- und Sicherheitsrisiko

Die produktive Datenbankdatei `oauth2_gateway.db` ist bereits im öffentlichen Hauptrepository versioniert. Zusätzlich ist das konfigurierte Backup-Repository öffentlich auffindbar. Da das Schema Spotify-Secrets, Gateway-Secrets/Tokens, Positionsdaten, Fehlerberichte und Nutzungsdaten speichern kann, dürfen produktiv befüllte Datenbanken nicht öffentlich versioniert oder gepusht werden.

Sofortige Maßnahmen:

1. DB-Datei und Backup-Dateien aus Git entfernen und in `.gitignore` aufnehmen.
2. Öffentliche Git-Historie und Backup-Historie als potenziell kompromittiert behandeln.
3. Spotify Client-Secret, Refresh-Token und Access-Token rotieren.
4. alle Gateway-Client-Secrets und Access-Tokens widerrufen/neu erzeugen.
5. personenbezogene Daten prüfen und gegebenenfalls löschen.
6. Backups verschlüsselt und zugriffsgeschützt speichern.

Das bloße Löschen der aktuellen Datei entfernt sie nicht aus der Git-Historie.

## 7. Logging und CORS

### Logging

Jeder Request wird mit folgenden Informationen protokolliert:

- vollständige URL
- sämtliche Header
- Remote-IP
- bis zu 1000 Zeichen Request-Body

Jede Response wird mit Headern und bis zu 1000 Zeichen Inhalt protokolliert. Nur große/binäre Requests und direkte Dateistreams werden teilweise ausgenommen.

Dadurch können unter anderem in `log/api.log.txt` landen:

- Bearer-Tokens aus `Authorization`
- OAuth-Clientdaten
- Fehlerberichte
- Positionsparameter
- Core-Data
- Antworten mit vertraulichen Informationen

Das Log rotiert bei 10 MiB und behält fünf Backups. Vor Produktivbetrieb müssen sensible Header und Felder konsequent maskiert werden.

### CORS

Alle Antworten erhalten:

```text
Access-Control-Allow-Origin: *
```

Zusätzlich werden Authorization- und Content-Type-Header sowie GET, POST, PUT, DELETE und OPTIONS erlaubt. Für eine API mit Verwaltungsfunktionen und personenbezogenen Daten ist eine feste Origin-Allowlist erforderlich.

## 8. Authentifizierung und Berechtigungen

### Gateway-Bearer-Token

`verify_gateway_token()` schützt hauptsächlich Player-/Queue-Proxy-Routen und `/notify/new`.

Prüfungen:

- `Authorization: Bearer <token>` vorhanden
- Token existiert in `oauth_tokens`
- Token ist nicht abgelaufen

Die im Token gespeicherten Scopes werden bei den einzelnen Routen nicht geprüft. Ein gültiger Token kann daher grundsätzlich alle gateway-geschützten Funktionen verwenden.

### Schwach geschützte Admin-Abfragen

Positionsabfrage:

- Adminname als Query-Parameter aus einer hardcodierten Liste
- zusätzlich hardcodiertes Passwort als Query-Parameter

Timeline-Abfragen:

- prüfen lediglich den Header `user` gegen die hardcodierte Adminliste
- kein Passwort und kein Bearer-Token

Query-Passwörter erscheinen in Browserhistorie, Proxylogs und dem eigenen Requestlog.

### Ungeschützte Verwaltungsfunktionen

Die folgenden Bereiche besitzen aktuell keine echte Authentifizierung:

- `/dashboard`
- Dashboard-Konfiguration und Spotify-Verknüpfung
- API-Client erstellen, löschen und Token-Lebensdauer ändern
- Klartext-Client-Secrets anzeigen
- Benachrichtigungen erstellen, ändern und löschen
- Browser- und Roh-APK-Upload
- Updatehinweise hochladen
- Core-Data hoch- und herunterladen
- Positionskarte
- Fehlerberichte schreiben
- Session-/Aktions-/Positionsdaten schreiben
- Routenübersicht

Diese Funktionen dürfen nicht unverändert öffentlich erreichbar sein.

## 9. Eigenes OAuth-Verfahren

### `/authorize`

- prüft Client-ID
- prüft Redirect-URI gegen eine hardcodierte Allowlist
- erzeugt ohne Benutzerlogin oder Zustimmungsseite sofort einen zehn Minuten gültigen Code
- leitet zum Redirect mit `code` und optional `state` weiter

Der angeforderte Scope wird gespeichert, aber nicht gegen die erlaubten Client-Scopes validiert.

### `/token`

Unterstützt:

- `authorization_code`
- `client_credentials`

Clientauthentifizierung erfolgt über HTTP Basic oder Formularfelder.

Beim Authorization-Code-Grant werden geprüft:

- Code existiert
- nicht abgelaufen
- gehört zur Client-ID

Die Redirect-URI wird am Token-Endpunkt nicht erneut verlangt oder geprüft. Nach erfolgreicher Verwendung wird der Code gelöscht.

Das ausgegebene Token erhält immer `client.allowed_scopes`, nicht den tatsächlich angeforderten/autorisierten Scope.

### Fehlende OAuth-Sicherheitsfunktionen

- keine PKCE-Unterstützung
- keine Benutzeranmeldung/Zustimmung
- keine Redirect-URI-Prüfung beim Tokenaustausch
- keine Scope-Durchsetzung an Ressourcen-Endpunkten
- keine Token-Revocation
- keine Refresh-Tokens für Gateway-Clients
- keine automatische Bereinigung abgelaufener Codes/Tokens
- keine Rate-Limits

Das System ist damit kein vollständig standardkonformer OAuth2-Authorization-Server.

## 10. Gateway-Modi und Musik-Proxy

### Direkt-Modus

Der Server verwendet die in `system_config` gespeicherten Spotify-Zugangsdaten. Abgelaufene Access-Tokens werden über den Refresh-Token erneuert und wieder in SQLite gespeichert.

Generische Proxyaufrufe gehen an:

```text
https://api.spotify.com/v1/...
```

Dedizierte Handler transformieren einzelne Spotify-Antworten, etwa Playerstatus, Repeat-Status und Queue-Liste.

### Server-Modus

Requests werden an mehrere fest im Code eingetragene HTTP-Backends weitergeleitet.

Kritischer Seiteneffekt: Die Schleife beendet sich nach der ersten erfolgreichen Antwort nicht. Sie sendet denselben Request weiterhin an alle erreichbaren Backends und gibt nur die erste erfolgreiche Antwort zurück. Bei zustandsändernden Befehlen wie Play, Pause, Next, Previous oder Queue-Add kann die Aktion dadurch mehrfach auf unterschiedlichen Backends ausgeführt werden.

Zusätzlich enthält der Exception-Zweig:

```python
app.warning(...)
```

Flask stellt üblicherweise `app.logger.warning(...)` bereit. `app.warning` kann selbst einen `AttributeError` auslösen und die vorgesehene Failover-Logik abbrechen.

Empfohlene Korrektur:

- Backends nacheinander probieren,
- nach der ersten akzeptablen Antwort sofort `return` oder `break`,
- Fehler mit `app.logger.warning` protokollieren,
- klare Regeln definieren, welche HTTP-Statuscodes als erreichbar/erfolgreich gelten.

## 11. Player-Endpunkte

Gateway-geschützt sind unter anderem:

- `GET /player`
- `GET /player/play-pause`
- `PUT|POST|GET /player/pause`
- `PUT|POST|GET /player/play`
- `POST|PUT|GET /player/next`
- `POST|PUT|GET /player/previous`
- `GET /player/get-repeat`
- `PUT|POST|GET /player/repeat/<value>`

`/player/endpoints` listet die Player-Routen ohne Authentifizierung auf.

Mehrere zustandsändernde Aktionen akzeptieren zusätzlich GET. Dadurch können Browser, Crawler, Link-Previews oder Caches unbeabsichtigt Aktionen auslösen. Zustandsänderungen sollten nur POST/PUT/PATCH/DELETE akzeptieren.

## 12. Queue-Endpunkte

- `GET /queue/get-list`: Spotify-Queue in vereinfachte JSON-Liste umwandeln
- `POST|GET|PUT /queue/add/<song_id>`: Song hinzufügen
- `DELETE|POST|GET /queue/remove/<song_id>`: liefert im Direktmodus absichtlich HTTP 451, weil Spotify kein Entfernen einzelner Queue-Elemente unterstützt
- `GET /queue/endpoints`: Dokumentation

Auch hier sollte GET nicht für zustandsändernde Aufrufe erlaubt sein.

## 13. APK-Verwaltung

### Upload

- `/apk/online`: HTML-Formular und Multipart-Upload
- `/apk/upload/<version>`: roher Binärstream, gedacht für Tasker

Dateien werden in 1-MiB-Blöcken geschrieben. Es gibt keine Authentifizierung, keine Größenbegrenzung, keine APK-Signaturprüfung, keine MIME-Prüfung und keine globale Upload-Sperre.

### Versionslogik

- neuere Version wird `latest`
- vorherige `latest` wird archiviert
- ältere Version wird direkt archiviert
- gleiche Version verschiebt die bisherige Latest-Datei in einen Suffix-Ordner wie `.01`, `.02` und setzt den neuen Upload als Latest

`packaging.version.parse()` akzeptiert mehr Formate als nur das kommentierte Schema `x.y.z`. Der rohe Upload validiert den Versionsstring vor der Verwendung als Dateiname nicht ausdrücklich. Ein streng definierter regulärer Ausdruck und `secure_filename`/Pfadprüfung sind erforderlich.

### Downloads

- `/apk/latest`
- `/apk/latest/version`
- `/apk/versions`
- `/apk/version/<version>`

Bei Suffixversionen müssen Ordner- und Dateiname exakt zur implementierten Verschiebelogik passen.

## 14. Updatehinweise

Updatehinweise liegen als `updates/<version>.txt` vor.

Routen:

- `POST /add-update/<version>`: Request-Body direkt als Datei speichern
- `GET /updates`: alle Updatehinweise aufsteigend verbinden
- `GET /updates/<start_version>/<end_version>`: Versionen mit `start < version <= end`

Der Upload ist ungeschützt und überschreibt eine vorhandene Datei derselben Version. Dateigröße und Inhalt werden nicht begrenzt oder validiert.

## 15. Benachrichtigungen

### Abruf durch Clients

`GET /notify/new` ist Bearer-Token-geschützt. Der Server liefert die älteste Nachricht, die für die jeweilige Client-ID noch nicht in `notification_deliveries` markiert ist.

Ausgabeformat:

```text
Titel|Text|Kategorie|Gruppe[|PNG-Pfad]
```

Das Zeichen `|` ist deshalb in Eingabefeldern verboten.

Die Nachricht wird bereits vor dem Senden der HTTP-Antwort als zugestellt gespeichert. Bricht die Verbindung danach ab, kann der Client die Nachricht dauerhaft verpassen. Robuster wäre ein Ack-/Lease-Verfahren.

### Verwaltung

- `/notify/add`
- `/notify/edit`

Beide Verwaltungsseiten sind ungeschützt.

## 16. Fehlerberichte und Core-Data

### Fehlerberichte

- `/report/error/soft/<username>`
- `/report/error/hard/<username>`
- `/report/error/massreport/<username>`

Die eigentlichen Fehlerdaten werden überwiegend als Query-Parameter übertragen. Lange oder sensible Fehlertexte erscheinen dadurch in URLs und Logs und können URL-Limits überschreiten. JSON im Request-Body ist geeigneter.

### Core-Data

- `POST /coredatas/upload`
- `GET /coredatas/download`

Der Upload akzeptiert einen streng formatierten mehrzeiligen Text im Body oder Header und hängt ihn threadgesperrt an eine gemeinsame Textdatei an. Upload und vollständiger Download sind ungeschützt.

## 17. Telemetrie und Positionsdaten

### Session-Timeline

`POST /app/user/online/...` speichert Benutzer, Datum, Start- und Endzeit.

Die Admin-Abfrage `/app/admin/online/...` filtert optional nach Datum und Startzeit, ist aber nur durch den frei setzbaren `user`-Header gegen eine hardcodierte Namensliste geschützt.

### Action-Timeline

`POST /app/user/action/...` speichert Aktionen. Leerzeichen werden durch Unterstriche ersetzt.

Die Filtererkennung der Admin-Abfrage ist inkonsistent:

- POST erwartet ein vierstelliges Jahr,
- die GET-Heuristik erkennt für das Datum nur zwei Jahresziffern,
- gespeicherte Aktionszeiten enthalten Sekunden,
- der optionale Zeitfilter erkennt nur Stunden und Minuten.

Diese Route benötigt eindeutige Query-Parameter statt formatabhängig interpretierter Pfadsegmente.

### Positionen

`/app/user/pos/<username>` speichert Positionen über GET oder POST. GET darf keine personenbezogenen Daten erzeugen.

Die Koordinatenprüfung akzeptiert nur positive Dezimalzahlen ohne Vorzeichen. Negative Längen-/Breitengrade und explizit positive Werte mit `+` werden abgelehnt. Gültigkeitsbereiche von Breiten- und Längengrad werden nicht geprüft.

`/map` zeigt alle gespeicherten Positionen ohne Authentifizierung. Punkte werden je Benutzer sortiert; Linien werden unterbrochen, wenn der Abstand größer als 2 km oder die Zeitdifferenz größer als eine Stunde ist.

Positionsdaten sind besonders schutzbedürftig. Speicherung, Aufbewahrung, Zugriff, Löschung und Einwilligung müssen ausdrücklich geregelt werden.

## 18. Ressourcen-Download

`GET /ressources/<path:resource_name>` verwendet `safe_join` und erwartet, dass das angegebene Verzeichnis exakt eine Datei enthält.

- kein Verzeichnis: 404
- leeres Verzeichnis: 404
- mehr als eine Datei: 400
- exakt eine Datei: Download

Der Pfadschutz ist sinnvoll, allerdings ist die Route ungeschützt und der Ressourcen-Basisordner hängt aktuell vom Arbeitsverzeichnis ab.

## 19. Dashboard

`/dashboard` verwaltet:

- Gateway-Modus
- Spotify Client-ID und Client-Secret
- Spotify-Kontoverknüpfung
- Gateway-Clients
- Token-Lebensdauer
- Löschen von Clients
- Anzeige des Klartext-Client-Secrets

Die Oberfläche selbst besitzt keine Server-Authentifizierung. Die „Secret anzeigen“-Prüfung findet ausschließlich als hardcodierter JavaScript-Vergleich im Browser statt. Das Klartext-Secret ist bereits im gerenderten HTML/JavaScript enthalten und kann ohne dieses Passwort aus dem Quelltext gelesen werden.

Das Dashboard muss vollständig hinter eine echte serverseitige Anmeldung und Autorisierung.

## 20. Dynamische Routenübersicht

`/routes` ermittelt die registrierten Flask-Routen bei jedem Aufruf. Die Implementierung:

- liest Pfadparameter aus Flask-Regeln,
- analysiert Python-Quelltext per `inspect` und `ast`,
- sucht Query-, Form- und Datei-Parameter,
- folgt teilweise lokalen Hilfsfunktionen,
- ergänzt manuelle Overrides,
- rendert HTML oder eine semikolongetrennte Textliste.

Die Erkennung ist hilfreich, aber heuristisch. Dynamisch gelesene Header, JSON-Felder, indirekte Parameter und komplexe Kontrollflüsse können fehlen oder falsch als Pflichtfeld erkannt werden. Sie ersetzt keine versionierte API-Spezifikation wie OpenAPI.

## 21. Datenbankinitialisierung und Migrationen

`db.create_all()` wird bereits während des Imports und nochmals beim Direktstart ausgeführt.

Es existiert genau eine manuelle Migration für `client_credentials.token_lifetime_seconds`. Weitere Schemaänderungen werden nicht versioniert.

Vor zukünftigen Datenbankänderungen sollte Alembic/Flask-Migrate eingeführt werden. `create_all()` ändert bestehende Tabellen nicht zuverlässig.

## 22. Besonders kritische Probleme

Priorität 1:

1. produktive SQLite-Datenbank und Backups nicht öffentlich versionieren
2. alle möglicherweise offengelegten Secrets und Tokens rotieren
3. Dashboard und Verwaltungsrouten serverseitig absichern
4. hardcodierte Passwörter und Adminlisten entfernen
5. Request-/Response-Logging vertraulicher Daten stoppen oder maskieren
6. Flask-Debugmodus deaktivieren
7. dauerhaften `SECRET_KEY` konfigurieren
8. Positionskarte und personenbezogene Daten schützen

Priorität 2:

9. Server-Proxy nach erster geeigneter Antwort beenden
10. `app.warning` zu `app.logger.warning` korrigieren
11. GET von zustandsändernden Routen entfernen
12. CORS auf bekannte Origins beschränken
13. Scopes tatsächlich erzwingen
14. Uploads authentifizieren, begrenzen und validieren
15. DB-Backup nicht nach jedem einzelnen Commit synchron pushen
16. Klartext-Client-Secrets nicht speichern
17. OAuth-Ablauf härten oder bewährten Provider verwenden
18. Rate-Limits und Missbrauchsschutz ergänzen

Priorität 3:

19. `servus.py` in Module/Blueprints teilen
20. Abhängigkeiten versionieren
21. automatisierte Tests und CI einführen
22. Alembic-Migrationen einführen
23. OpenAPI-Spezifikation erstellen
24. strukturierte Konfiguration statt hardcodierter Hosts/URLs

## 23. Vorgehen bei zukünftigen Änderungen

### Vor jeder Änderung

1. Keine produktive DB ungeprüft kopieren oder committen.
2. verschlüsseltes, zugriffsgeschütztes Backup erstellen.
3. aktuelle Routenliste und relevante Beispielantworten sichern.
4. Geheimnisse nur über Environment/Secret-Manager bereitstellen.
5. Änderung zunächst in einer separaten Testdatenbank durchführen.

### Bei neuen Routen

- Authentifizierung und erforderliche Rolle/Scope ausdrücklich festlegen.
- keine zustandsändernde GET-Route verwenden.
- Eingaben aus JSON/Form/Pfad strikt validieren.
- Größenlimits setzen.
- sensible Werte niemals vollständig loggen.
- Rate-Limit und Missbrauchsszenario prüfen.
- OpenAPI-Dokumentation und Tests ergänzen.

### Bei Spotify-/Proxyänderungen

- Direkt- und Servermodus getrennt testen.
- Timeouts, Retry und Statuscodebehandlung definieren.
- sicherstellen, dass eine Mutation nur an genau ein Ziel gesendet wird.
- Authorization-Header nie loggen.
- Spotify-Fehlertexte nicht ungefiltert in öffentliche Dashboards übernehmen.

### Bei APK-/Updateänderungen

- Versionsstring streng validieren.
- Upload authentifizieren.
- maximale Dateigröße festlegen.
- APK-Struktur, Paketname, Version und Signatur prüfen.
- atomar in temporäre Datei schreiben und erst danach verschieben.
- parallele Uploads sperren.
- Hashwerte für Downloads veröffentlichen.

### Bei Datenbankänderungen

- Migration schreiben.
- Migration auf einer anonymisierten Kopie testen.
- Rollback-Plan erstellen.
- Backup- und Datenschutzfolgen prüfen.
- keine Secrets im Klartext hinzufügen.

## 24. Empfohlene modulare Zielstruktur

```text
musik_api/
├── __init__.py                 # Application-Factory
├── config.py                   # Environment-basierte Konfiguration
├── extensions.py               # SQLAlchemy, Login/Auth, Limiter
├── models/
│   ├── oauth.py
│   ├── telemetry.py
│   ├── notifications.py
│   └── versions.py
├── blueprints/
│   ├── oauth.py
│   ├── dashboard.py
│   ├── player.py
│   ├── queue.py
│   ├── apk.py
│   ├── updates.py
│   ├── notifications.py
│   ├── reports.py
│   ├── telemetry.py
│   ├── positions.py
│   ├── resources.py
│   └── routes_documentation.py
├── services/
│   ├── spotify.py
│   ├── backend_proxy.py
│   ├── backups.py
│   └── apk_validation.py
├── templates/
├── migrations/
└── tests/
```

Die Zerlegung sollte schrittweise erfolgen. Zuerst Tests für das aktuelle Verhalten schreiben, danach jeweils eine Funktionsgruppe extrahieren.

## 25. Kurzreferenz der Funktionsgruppen

| Bereich | Zentrale Routen |
|---|---|
| OAuth | `/authorize`, `/token` |
| Dashboard | `/dashboard`, `/dashboard/config/save`, `/dashboard/client/...`, `/dashboard/spotify/login`, `/callback` |
| Player | `/player`, `/player/play`, `/player/pause`, `/player/next`, `/player/previous`, `/player/repeat/...` |
| Queue | `/queue/get-list`, `/queue/add/...`, `/queue/remove/...` |
| APK | `/apk/online`, `/apk/upload/...`, `/apk/latest`, `/apk/versions`, `/apk/version/...` |
| Updates | `/add-update/...`, `/updates`, `/updates/<start>/<end>` |
| Notifications | `/notify/new`, `/notify/add`, `/notify/edit` |
| Reports | `/report/error/soft/...`, `/report/error/hard/...`, `/report/error/massreport/...` |
| Core-Data | `/coredatas/upload`, `/coredatas/download` |
| Telemetrie | `/app/user/online/...`, `/app/admin/online/...`, `/app/user/action/...`, `/app/admin/action/...` |
| Positionen | `/app/user/pos/...`, `/app/get/pos/...`, `/map` |
| Ressourcen | `/ressources/...` |
| Dokumentation | `/routes`, `/player/endpoints`, `/queue/endpoints` |

---

Diese Datei beschreibt den aktuellen Ist-Zustand. Sie sollte nach jeder Änderung an Routen, Modellen, Authentifizierung, Gateway-Logik, Dateiformaten, Deployment oder Datenschutz aktualisiert werden.
