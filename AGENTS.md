# Repository guide for coding agents

> **Scope:** This file applies to the entire repository. Read it before editing. It is both an architectural reference and an inventory of the behavior currently implemented in `servus.py`. Keep this document synchronized when routes, database models, templates, storage paths, authentication, or external integrations change.

## 1. Purpose and high-level architecture

This repository contains a single-process Flask application named **Musik-Client-App-API-Server** (also called the HBC gateway in the UI and logs). It serves as the backend for a Tasker-based music client and combines several responsibilities in one module:

1. an OAuth-like client-credential and authorization-code issuer for this gateway;
2. an authenticated reverse proxy for Spotify playback and queue operations;
3. an administrative browser dashboard for gateway clients and Spotify account linkage;
4. APK upload, version archival, and download endpoints;
5. update-note and static-resource distribution;
6. user session, action, and location telemetry with a Leaflet map;
7. soft, hard, and mass error ingestion;
8. notification creation/editing and per-client delivery tracking;
9. playlist-content storage, pagination, retrieval, and Spotify playback;
10. Core-Data report ingestion/download;
11. a self-documenting route browser; and
12. automatic SQLite backups after successful SQLAlchemy commits, with an optional Git push; and
13. a browser webchat plus persistent per-user communication with OpenAI's Responses API.

Most Python behavior is in `servus.py`; the independently runnable chat subsystem is in `chat.py`. There is no migration framework, test suite, dependency manifest, container definition, or production WSGI configuration checked in. Both development entry points listen on `0.0.0.0:2050` with Flask debug mode enabled; only one can bind that port at a time.

## 2. Repository inventory

### Source and documentation

- `servus.py`: the complete Flask app, configuration constants, SQLAlchemy models, startup schema alterations, backup hooks, request/response logging, OAuth implementation, Spotify/local-backend proxy, all API routes, and an inline dashboard template.
- `chat.py`: independent chat blueprint, chat-specific SQLite schema, attachment storage, direct/self/group messaging, history, and media routes. `servus.py` imports and registers it automatically; `python3 chat.py` runs it alone on the same host/port.
- `README.md`: currently only the repository title; this `AGENTS.md` is the authoritative engineering guide.
- `AGENTS.md`: this file. Update it whenever externally visible behavior or repository structure changes.
- `.gitignore`: ignores `/db_bak/`, which contains sensitive generated database backups and the backup repository clone.

### Browser templates

- `templates/upload.html`: browser form for multipart APK upload at `/apk/online`.
- `templates/map.html`: Leaflet/OpenStreetMap position visualization. It renders points per user, permits per-user visibility/color changes, and rebuilds track lines live when the maximum distance slider changes. Tracks also split after a time gap over 60 minutes.
- `templates/notify_add.html`: notification creation form; notification text is a multiline `textarea`.
- `templates/notify_edit.html`: list/edit/delete UI for stored notifications.
- `templates/routes.html`: searchable/filterable route documentation. It supports filters for browser compatibility, GET, POST, and authentication and shows collapsible fixed-path groups and parameter metadata.
- `templates/webchat.html`: authenticated browser client for direct/self/group chats, uploads, media browsing, and per-user ChatGPT conversations.

### Version and downloadable content

- `updates/3.9.8.txt` through `updates/4.0.9.txt`: UTF-8 release notes returned by `/updates` and `/updates/<start>/<end>`. Version filenames are parsed and SemVer-sorted at request time.
- `updatetracker.txt`: a legacy/sample tracker text; current update routes do not read it.
- `app_ressources/app-resources/loading_spinner_frames.zip`: a downloadable client resource. `/ressources/<resource_name>` expects `resource_name` to identify a directory below `app_ressources`, and that directory must contain exactly one file.

### Generated runtime paths (normally absent from Git)

- `oauth2_gateway.db`: SQLite application database, created automatically.
- `db_bak/`: timestamped SQLite snapshots. `db_bak/git-repository/` is a clone of the backup Git repository when Git backup is enabled.
- `log/api.log.txt`: rotating request/application log (10 MiB per file, five backups).
- `apk/new-upload/`: temporary incoming APK files.
- `apk/latest/`: the current APK, named `<version>.apk`.
- `apk/versions/<version>/`: archived APK versions.
- `uploads/core-data-abfragen.txt`: append-only validated Core-Data submissions.
- `chat.db`: separate SQLite database containing chat messages, groups, memberships, and attachment metadata.
- `chat_uploads/`: uploaded chat files and images; opaque response IDs form the stored filenames.

Do not commit runtime databases, logs, uploaded APKs, credentials, or backup clones. The main databases, chat uploads, logs, backups, and Python bytecode are ignored, but still inspect `git status` carefully for other generated files.

## 3. Runtime dependencies and operation

The imports imply these non-stdlib dependencies:

```text
Flask
Flask-SQLAlchemy
requests
packaging
Werkzeug
SQLAlchemy (installed transitively by Flask-SQLAlchemy)
```

A typical development setup is:

```bash
python3 -m pip install Flask Flask-SQLAlchemy requests packaging Werkzeug ruff
DB_BACKUP_GIT_ENABLED=0 python3 servus.py
```

Disable Git backup in local/tests with `DB_BACKUP_GIT_ENABLED=0`; otherwise every successful database commit attempts network/Git work. Importing `servus.py` creates directories, configures logging, creates database tables, and may alter existing tables. Tests should use a temporary working directory/database or clean up generated `oauth2_gateway.db`, `log/`, and `__pycache__/` afterward.

For code changes, run at least:

```bash
python3 -m py_compile servus.py
python3 -m py_compile chat.py
python3 -m ruff check servus.py chat.py
git diff --check
```

Also exercise changed routes using Flask's `app.test_client()`. Set `DB_BACKUP_GIT_ENABLED=0` for tests that commit. Browser-visible changes should be smoke-tested in a browser and screenshot when the environment supports it. Documentation-only/comment-only changes do not require tests or lint checks per project instruction.

## 4. Configuration and security-sensitive constants

Configuration is currently hard-coded near the top/middle of `servus.py` rather than loaded from a settings file:

- SQLite database: `oauth2_gateway.db` beside `servus.py`.
- Backup target: `db_bak/`.
- Backup remote: `https://github.com/MoinsenWerner/Musik-Client-API-Server-DB-Backups.git`.
- Admin users: `felix`, `test`, `moin`.
- Position-admin query password: defined by `ADMIN_REQUEST_PASSWORD`.
- Spotify redirect URI: `https://api.extrahelden.de/callback`.
- Gateway backend URLs: the three entries in `TARGET_BACKENDS`.
- Allowed client OAuth redirects: `ALLOWED_REDIRECT_URIS`.
- Flask secret key: randomly generated on each process start, so flash/session continuity does not survive restarts.
- OpenAI API key: environment variable `OPENAI_API_KEY`; required only for `/chat/gpt/*`.
- OpenAI model: environment variable `OPENAI_MODEL`, defaulting to `gpt-5`.

Treat the database and backups as highly sensitive: they may contain plain client secrets, gateway tokens, Spotify refresh/access tokens, positions, notifications, and reports. The dashboard itself has no login guard, client deletion uses GET, and the notification/APK management pages have no authorization guard. Preserve compatibility when changing these behaviors, but flag them as security concerns rather than assuming they are intentional best practices.

## 5. Database model and backup behavior

SQLAlchemy uses one SQLite database. Current tables/models are:

- `apk_version`: archived APK version strings.
- `system_config`: gateway mode (`Server` or `Direkt`) and Spotify client/access/refresh-token state.
- `client_credentials`: gateway client ID, hashed and plain secret, display name, role/scopes, and per-client token lifetime.
- `authorization_codes`: 10-minute, single-use gateway authorization codes.
- `oauth_tokens`: issued `hbc_...` bearer tokens, client ID, scope string, and Unix expiry.
- `user_session_timeline`: session date/start/end/user.
- `user_action_timeline`: action date/time/name/user.
- `error_reports`: soft/hard structured reports.
- `mass_errorreport_errors`: large free-text error reports.
- `notifications`: notification body and creation time.
- `notification_deliveries`: per-notification/per-client delivery state, protected by a uniqueness constraint.
- `playlist_contents`: playlist metadata and JSON-encoded parallel arrays for song names, IDs, and images; includes creator and timestamps.
- `playing_playlist`: singleton-like row (`id=1`) pointing at the last playlist successfully started through this API.
- `user_positions`: dynamically declared coordinate/time/date/map-link columns based on constants.

The separate `chat.db`, managed directly with `sqlite3` by `chat.py`, contains `chat_uploads`, `chat_groups`, `chat_group_members`, `chat_messages`, and `chatgpt_messages`. Attachment bytes live in `chat_uploads/`; their metadata and associations live in `chat.db`. Foreign keys connect messages to group, upload, and optional replied-to message rows. Direct messages store a recipient, while group messages store a group ID. ChatGPT prompts and answers are stored per username so later model requests can include that user's prior messages. Startup adds `chat_messages.reply_to_message_id` to older chat databases when needed.

Startup calls `db.create_all()` and performs manual `ALTER TABLE` compatibility additions for `client_credentials.token_lifetime_seconds` and `playlist_contents.creator`. There is no Alembic migration history.

An SQLAlchemy `Session.after_commit` listener calls `create_database_backup()` for commits bound to the app database. SQLite's backup API writes a consistent timestamped copy. If `DB_BACKUP_GIT_ENABLED` is not `0`, the app clones/reuses the configured remote, copies in the backup, configures a local Git identity, pulls with rebase, commits, and pushes. Backup/Git failures are logged and do not undo the already-completed application commit.

## 6. Authentication and Spotify communication

### Gateway authentication

Protected routes call `execute_proxy_request()` or `verify_gateway_token()` and require:

```http
Authorization: Bearer hbc_<token>
```

`verify_gateway_token()` looks up the exact token in `oauth_tokens`. Missing/malformed headers, unknown tokens, and expired tokens return HTTP 401 JSON. Expired-token responses use `error: "token_expired"`, include `reauthenticate: true`, `token_endpoint: "/token"`, and `expired_at`.

The gateway supports:

- **Authorization code:** `/authorize` validates a registered client and allow-listed redirect, stores a code for 600 seconds, then redirects with `code` and optional `state`. `/token` consumes the code.
- **Client credentials:** `/token` accepts `grant_type=client_credentials` directly.

Client authentication at `/token` may be HTTP Basic or form fields `client_id` and `client_secret`. Issued lifetime comes from `client_credentials.token_lifetime_seconds`; `expires_in` is returned in seconds.

### Spotify account linkage

The browser dashboard stores a Spotify application client ID/secret. `/dashboard/spotify/login` redirects to Spotify Accounts with `SPOTIFY_SCOPES`; `/callback` exchanges Spotify's code at `https://accounts.spotify.com/api/token` and stores access/refresh tokens. `get_valid_spotify_token()` reuses a token when it has over 30 seconds remaining, otherwise refreshes it with the stored refresh token and commits new token data.

### Proxy modes

`execute_proxy_request(target_path, method, custom_spotify_handler, request_body)` always validates the gateway bearer token first.

- **Direkt:** obtains a valid server-side Spotify token, replaces the incoming Authorization header, and calls `https://api.spotify.com/v1/...` with `requests`. Selected routes use custom handlers to transform Spotify JSON.
- **Server:** forwards the incoming headers/body to every configured `TARGET_BACKENDS` URL. The first response obtained is returned, although the loop still attempts the remaining backends. This means a client gateway token is forwarded to local backends rather than replaced with a Spotify token.

Network calls use short timeouts and strip hop-by-hop/content-length/CORS response headers before Flask returns the response. Spotify/network failures generally become HTTP 502 JSON.

## 7. Complete route catalog

Unless stated otherwise, query values containing spaces, separators, URLs, HTML, or newlines must be URL-encoded. Flask also answers every route's CORS preflight globally and adds permissive CORS headers.

### Error reports

- **POST `/report/error/soft/<username>`** and **POST `/report/error/hard/<username>`** — Store one structured report with severity `soft` or `hard`. Required query parameters: `app-version` (`x.y.z`), `error_task` (free text), `error` (free text), `date` (`dd.mm.yy` or `dd.mm.yyyy`), `time` (`hh.mm`), and `last-action` (free text). Returns HTTP 201 JSON with `report_id`; missing/invalid values return 400.
- **POST `/report/error/massreport/<username>`** — Stores a large unparsed report in `mass_errorreport_errors`. Required query parameters are `app-version` (`x.y.z`) and `error` (arbitrary URL-encoded text, including escaped/newline content). Returns HTTP 201 JSON with the inserted `id`.

### Core-Data and resources

- **POST `/coredatas/upload`** — Reads a Core-Data block from the raw text body first, otherwise `Core-Data-Text` or `X-Core-Data-Text`. Header `\n` sequences are expanded. A strict regex validates header text, date/time, Tasker/server versions, lowercase dotted package name, multiline notes, HTTPS API URL, and separators. Valid input is appended (never overwritten) to `uploads/core-data-abfragen.txt`; response is HTTP 201 JSON and identifies `body` or `header` as the source.
- **GET `/coredatas/download`** — Downloads `core-data-abfragen.txt` as a text attachment. Returns 404 JSON until a submission exists.
- **GET `/ressources/<path:resource_name>`** — Safely resolves a directory under `app_ressources`. It downloads the directory's sole file. Missing/empty directories return 404, and directories with multiple files return 400. Example: `/ressources/app-resources` downloads `loading_spinner_frames.zip`.

### Position, session, and action telemetry

- **GET or POST `/app/user/pos/<wert0>`** — `wert0` is username. Required query: `lat`, `lon` as unsigned decimal strings, `time` as `hh-mm`, `date` as `dd-mm-yyyy`, and `maps_url`. Stores a `user_positions` row and returns HTTP 201 plain text. Although GET is allowed, it writes data.
- **GET `/app/get/pos/<wert0>`** — Lists all positions for username `wert0` as plain-text rows. Requires query `admin` in `ADMIN_USERS` and `passwd` matching `ADMIN_REQUEST_PASSWORD`; otherwise 403.
- **GET `/map`** — Browser page for all stored positions. Server groups by username and sorts timestamps. Leaflet displays markers and tracks; users can be hidden and recolored. Lines split when points are over the live distance threshold (default 2 km) or over 60 minutes apart.
- **POST `/app/user/online/<wert0>/<wert1>/<wert2>/<wert3>`** — Stores a session. Semantically, `wert0` is date (`dd-mm-yyyy`), `wert1` is start (`hh-mm`), `wert2` is end (`hh-mm`), and `wert3` is username; returns 201 plain text.
- **GET `/app/admin/online/<wert1>`**, **`/app/admin/online/<wert1>/<p1>`**, and **`/app/admin/online/<wert1>/<p1>/<p2>`** — `wert1` is the username. Requires header `user` containing an allowed admin. Optional path values are interpreted by format as a minimum date (`dd-mm-yyyy`) and/or minimum start time (`hh-mm`). Returns matching session rows as plain text or `No records found.`.
- **POST `/app/user/action/<wert0>/<wert1>/<wert2>/<wert3>`** — Stores an action. Semantically, `wert0` is date (`dd-mm-yyyy`), `wert1` is time (`hh-mm-ss`), `wert2` is action, and `wert3` is username. Spaces in action are converted to underscores. Returns 201.
- **GET `/app/admin/action/<wert0>`**, **`/app/admin/action/<wert0>/<p1>`**, **`/app/admin/action/<wert0>/<p1>/<p2>`**, and **`/app/admin/action/<wert0>/<p1>/<p2>/<p3>`** — `wert0` is username. Requires allowed `user` header. Optional values are inferred as date, time, or exact action filters. Returns matching rows or `No records found.`. Be careful: current date-filter recognition expects a two-digit-year-shaped value while stored posts require four digits; inspect existing logic before “fixing” compatibility.

### Notifications

- **GET `/notify/new`** — Gateway bearer authentication required. Selects the oldest notification not yet delivered to that token's `client_id`, inserts a delivery record, and returns exactly one notification as `Title|Text|Category|Group` with optional `|PNG-path`. Returns 204 with no body when none remain. Delivery state is per client, not per token.
- **GET `/notify/add`** — Browser notification form.
- **POST `/notify/add`** — Form fields `title`, multiline `text`, `category`, and `notification_group` are required; `png_path` is optional. `|` is rejected in all fields because it is the wire delimiter. Saves and redirects to `/notify/edit`.
- **GET `/notify/edit`** — Browser list/editor for all notifications, newest first.
- **POST `/notify/edit`** — Requires form `notification_id`; `action=delete` deletes the notification and its delivery rows. Otherwise it validates the same fields as add and updates the row.

### APK and update distribution

- **GET `/apk/online`** — Browser multipart upload page.
- **POST `/apk/online`** — Required form `version` (`x.y.z`) and file `apk_file` ending in `.apk`. Streams in 1 MiB chunks. Newer versions replace latest and archive old latest; older versions are archived; equal versions archive old latest with `.01`, `.02`, etc. Returns rendered HTML.
- **POST `/apk/upload/<version>`** — Raw/streamed APK-body equivalent intended for clients. Validates version, rejects empty upload, then runs the same latest/archive comparison process. Inspect this route before refactoring because its response wording/statuses are relied on by clients.
- **GET `/apk/latest`** — Downloads the current APK attachment; 404 if absent.
- **GET `/apk/latest/version`** — Returns the current version string as plain text; 404 if absent.
- **GET `/apk/versions`** — Returns archived/current version strings as newline-separated plain text, SemVer-sorted descending.
- **GET `/apk/version/<version>`** — Downloads the archived `apk/versions/<version>/<version>.apk`; 404 when absent.
- **POST `/add-update/<version>`** — Writes the raw request body to `updates/<version>.txt` (overwrites the same version). Empty bodies return 400; success returns 201 plain text.
- **GET `/updates`** — Concatenates all valid version-named update files in ascending SemVer order with `------------` separators.
- **GET `/updates/<start_version>/<end_version>`** — Returns notes where `start_version < version <= end_version`; rejects reversed/invalid ranges.

### Playlist persistence and playback

- **POST `/playlistcontent/list`** — Stores or updates a playlist. Required query: `name`, comma-separated `content` song names, and comma-separated `ids`. Optional: comma-separated `bilder`, `pl-id` Spotify playlist ID, and `ersteller`; blank/missing creator becomes `Admin`. Name/ID/image arrays must have matching lengths (missing images become blank entries). Lookup prefers `pl-id`, otherwise name. Returns HTTP 201 JSON.
- **GET `/serverplaylists/list?num=all`** — Returns every playlist oldest-first (`created_at`, then database ID). Each record is `Playlistname•|•Number•|•Playlist-ID•|•Creator`; records are joined by `°|°`. Numbering starts at 1.
- **GET `/serverplaylists/list?num=<amount>&last-num=<last>`** — Returns `amount` rows after the first `last` rows, retaining global numbering. Thus `num=10&last-num=45` emits numbers 46–55. `amount` must be positive and `last-num` at least zero.
- **GET `/serverplaylists/maxnum`** — Plain-text count of stored playlists.
- **GET `/playlistcontent/get/<path:playlist_name>`** — Finds by optional query `id` (Spotify playlist ID) or by name. Response is exact plain text: comma-separated song names, `___`, comma-separated song IDs, `___`, comma-separated PNG links, `___`, playlist name, `___`, playlist ID, with each separator on its own line.
- **GET `/playlistcontent/get/playing`** — Same output format, but resolves the `playing_playlist` pointer set after successful playback. The special word `playing` takes precedence over query `id`.
- **POST `/playlist/play/<path:playlist_name>`** — Gateway bearer authentication required through the proxy. Optional query `id` disambiguates lookup. Requires a stored Spotify playlist ID, then PUTs JSON `{"context_uri":"spotify:playlist:<ID>"}` to Spotify `/v1/me/player/play` (or the configured backends). Only a 2xx response updates the playing pointer.

### Player and queue proxy

All functional player/queue routes below require the gateway Bearer token because they call `execute_proxy_request`; only the endpoint-index routes are unauthenticated.

- **GET `/player`** — Proxies Spotify `GET /v1/me/player` unchanged.
- **GET `/player/endpoints`** — Static JSON index of player routes; no authentication.
- **GET `/player/play-pause`** — In direct mode, converts Spotify player state to `{ "is_playing": boolean }`; Spotify 204 becomes false. In server mode it behaves as a normal backend proxy because custom handlers are direct-mode only.
- **GET, POST, or PUT `/player/pause`** — Always sends upstream `PUT /v1/me/player/pause`.
- **GET, POST, or PUT `/player/play`** — Always sends upstream `PUT /v1/me/player/play`.
- **GET, POST, or PUT `/player/next`** — Always sends upstream `POST /v1/me/player/next`.
- **GET, POST, or PUT `/player/previous`** — Always sends upstream `POST /v1/me/player/previous`.
- **GET `/player/get-repeat`** — Direct mode returns only `repeat_state` and maps Spotify 204 to `off`; server mode proxies.
- **GET, POST, or PUT `/player/repeat/<value>`** — `value` must be `off`, `context`, or `track`; sends `PUT /v1/me/player/repeat?state=<value>`.
- **GET `/queue/endpoints`** — Static JSON index of queue routes; no authentication.
- **GET `/queue/get-list`** — Direct mode maps each Spotify queued track to `spotify-song-id`, `songname`, and comma-joined `artistname`; server mode proxies.
- **GET, POST, or DELETE `/queue/remove/<string:song_id>`** — Authenticates, then direct mode returns HTTP 451 because Spotify has no native arbitrary queue-removal operation. Server mode forwards a DELETE to the corresponding backend path.
- **GET, POST, or PUT `/queue/add/<string:song_id>`** — Sends upstream POST `/v1/me/player/queue?uri=spotify:track:<song_id>`.

### Gateway OAuth and administration

- **GET `/authorize`** — Required query `client_id` and `redirect_uri`; optional `scope` and `state`. Validates the client and exact redirect allow-list, stores a 10-minute code, and redirects to the client. It does not currently validate requested scope against allowed scopes.
- **POST `/token`** — Client credentials via HTTP Basic (preferred) or form. Required form `grant_type`; supports `client_credentials` and `authorization_code` (the latter also needs `code`). Returns bearer token JSON with `expires_in` and configured scopes.
- **GET `/dashboard`** — Unauthenticated browser management page rendered from inline `DASHBOARD_TEMPLATE`. Shows gateway/Spotify settings and all API clients including plain secrets behind a JavaScript prompt.
- **POST `/dashboard/config/save`** — Form fields `gateway_mode`, `spotify_client_id`, and `spotify_client_secret`; commits configuration and redirects.
- **GET `/dashboard/spotify/login`** — Redirects to Spotify authorization using configured credentials and fixed scopes/redirect URI.
- **GET `/callback`** — Spotify redirect target. Query `code` is exchanged for access/refresh tokens; optional `error` is displayed and redirected to dashboard.
- **POST `/dashboard/client/create`** — Required form `name`, valid `role` (`Client` or `Server`), and `token_lifetime_minutes` from 1 to 525600. Optional custom ID/secret; otherwise generated. Stores a hash and a plain secret.
- **POST `/dashboard/client/token-lifetime/<int:id>`** — Updates that client's lifetime from required form `token_lifetime_minutes`, then redirects.
- **GET `/dashboard/client/delete/<int:id>`** — Deletes the client and redirects. It does not cascade/remove already issued tokens in current code.

### Route discovery

- **GET `/routes`** — Dynamically enumerates Flask's URL map, so future routes appear automatically. Default representation depends on the `Accept` header: browsers receive `templates/routes.html`; other clients receive semicolon-separated route paths. Query `format=html` or `format=text` overrides negotiation. Python AST inspection discovers path/query/form/file parameters and augments known routes with manual metadata. The HTML UI offers live search, filters, parameter explanations, and collapsible fixed-route groupings.

### Chat subsystem

The chat API is registered automatically in the main app and is also independently runnable with `python3 chat.py`. It uses `chat.db`, not `oauth2_gateway.db`. Chat routes currently have no bearer-token authentication, so deployment-level access control is important.

- **GET `/webchat`** — Renders the WhatsApp-inspired browser chat UI. A blocking login overlay exchanges gateway client credentials at `/token`; after successful login the UI exposes direct/self/group messages, group membership, media, histories, and ChatGPT. Attachments are selected directly in the composer, uploaded immediately before message creation, and never expose their response IDs in the UI. Message type is inferred from text, URL, and attachment MIME type. Images/videos render inline, open in a full-screen viewer, and support context-menu reply/download actions. In standalone `chat.py` mode, `/token` deliberately returns 503 because gateway clients live in the main database, so the authenticated UI requires `servus.py`.
- **POST `/chat/upload/<kind>`** — Uploads multipart field `upload` before sending a message. `kind` is `bild` or `datei`; optional form field `sender` records who uploaded it. Images are restricted to AVIF, GIF, JPEG/JPG, PNG, or WebP. The file is streamed to `chat_uploads/`, metadata is stored in `chat.db`, and HTTP 201 JSON returns `upload_response_id`.
- **GET `/chat/upload/<response_id>`** — Downloads an uploaded attachment using its opaque upload response ID and original filename. Optional `inline=1` returns it with inline disposition for image/video rendering.
- **POST `/chat/<sender>/<recipient>/<message_type>`** — Sends a direct message; sender and recipient may be identical for a self-chat. Valid types are `picture`, `text`, `text-mit-link`, `link`, `datei`, `text-mit-bild`, and `text-mit-datei`. Query `inhalt` is required for text/link types, `datei-upload` is required for file types, and `bild-upload` is required for image types. Optional `antwort-auf` references a message ID from the same direct chat. Attachment values must be response IDs returned by the matching upload route. Returns HTTP 201 JSON with `message_id`.
- **POST `/chat/self/<username>/<message_type>`** — Explicit convenience route for a self-chat; uses the same type-dependent query parameters and stores username as both sender and recipient.
- **POST `/chat/group/create`** — Creates a group. Required query parameters: `name` and `ersteller`; optional `mitglieder` is a comma-separated list. The creator is always included. Returns `gruppen_id` and members.
- **POST `/chat/group/<int:group_id>/members`** — Changes membership using required query `action=add|remove` and `username`. Returns 404 for a missing group.
- **POST `/chat/group/<int:group_id>/<sender>/<message_type>`** — Sends a group message with the same type-dependent message queries and optional `antwort-auf` from that group. The sender must already be a group member, otherwise HTTP 403 is returned.
- **GET `/chat/history/<user_one>/<user_two>`** — Returns chronological direct-chat history in JSON, including attachment response IDs. Optional `limit` is 1–1000 (default 100), and `offset` is at least 0. Supplying the same user twice retrieves the self-chat.
- **GET `/chat/group/<int:group_id>/history`** — Returns chronological group history with the same optional pagination.
- **GET `/chat/media/<user_one>/<user_two>`** — Returns all distinct files/images assigned to the direct/self chat, oldest first, with metadata and download URLs.
- **GET `/chat/group/<int:group_id>/media`** — Returns all distinct attachments assigned to the group chat.
- **POST `/chat/gpt/<username>`** — Required query `inhalt`. Sends the latest 40 stored user/assistant messages plus the new prompt to OpenAI's Responses API using server environment variable `OPENAI_API_KEY` and model `OPENAI_MODEL` (default `gpt-5`). On success, stores both prompt and answer in `chatgpt_messages` and returns the answer. Missing configuration returns 503; upstream failures return 502.
- **GET `/chat/gpt/<username>/history`** — Returns the persistent per-user ChatGPT conversation with optional `limit` and `offset`.
- **DELETE `/chat/gpt/<username>/history`** — Deletes every remembered ChatGPT prompt and answer for the specified username.

## 8. Response conventions and notable quirks

- The API mixes JSON, plain text, file attachments, redirects, and rendered HTML. Preserve each route's wire format unless explicitly asked to version/change it.
- Unicode delimiters (`•|•`, `°|°`) and newline delimiters (`___`) are contractual client formats.
- Most successful inserts return 201, while updates/admin forms generally redirect.
- There is no global JSON error handler; `abort()` typically produces Flask HTML errors.
- Permissive CORS is attached to every response.
- Request logging includes headers and up to 1000 characters of bodies unless uploads/large payloads are detected. Never add secret values to explicit log messages.
- `BASE_DIR` is initially the repository directory, then later reassigned to `os.path.abspath("app_ressources")`; functions close over the global at call time. Be especially careful when moving path code.
- Database writes trigger backups synchronously after commit, making request latency and tests dependent on backup settings.

## 9. Change guidance for agents

1. Inspect all relevant route helpers and templates before editing; behavior is frequently shared indirectly through `execute_proxy_request`, `save_error_report`, playlist helpers, or route-discovery AST inspection.
2. Do not silently change client-facing delimiters, status codes, parameter spelling (including German names and hyphenated query keys), content type, route methods, or fallback values.
3. For a new SQLAlchemy column, account for existing SQLite databases. `db.create_all()` does not add columns; either add a safe startup migration consistent with current style or introduce a proper migration system as an explicit project-wide change.
4. Any new route should have a clear docstring and parameter metadata discoverable by `/routes`; add `PARAMETER_DETAILS` or `ROUTE_PARAMETER_OVERRIDES` when AST inference is insufficient.
5. Any protected Spotify route should go through `execute_proxy_request()` unless there is a documented reason not to, so gateway-token expiry behavior stays consistent.
6. Use `safe_join`/`secure_filename` and streamed I/O for client-controlled files. Never trust path parameters.
7. Avoid imports inside `try/except`; keep imports at module level.
8. Do not remove existing code unless the task explicitly requires it. This app has Tasker clients that may depend on odd-looking compatibility behavior.
9. Before committing, clean generated files and inspect `git status`. Commit changes on the current branch with a focused message.
10. Keep this guide current, especially the file inventory and complete route catalog.
