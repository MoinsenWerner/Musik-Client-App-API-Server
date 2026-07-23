import os
import secrets
import time
import logging
import re
import math
from datetime import datetime
from shutil import move
from logging.handlers import RotatingFileHandler
import requests
from flask import Flask, request, jsonify, render_template_string, render_template, redirect, flash, make_response, abort, Response, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import glob
from packaging.version import parse as parse_version
from werkzeug.utils import safe_join, secure_filename

UPDATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "updates")
os.makedirs(UPDATES_DIR, exist_ok=True)


app = Flask(__name__)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'apk', 'new-upload')
LATEST_FOLDER = os.path.join(BASE_DIR, 'apk', 'latest')
VERSIONS_FOLDER = os.path.join(BASE_DIR, 'apk', 'versions')
ADMIN_USERS = ["felix", "test", "moin"]
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(BASE_DIR, "oauth2_gateway.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = secrets.token_hex(32)

# ==========================================
# KONFIGURATION FÜR NEUE ROUTEN (EIGENE ANPASSUNG)
# ==========================================
ADMIN_REQUEST_PASSWORD = "FelixHertel"  # Wert 2 für die GET-Route

TABELLEN_NAME = "user_positions"  # <tabellenname>

SPALTE_1 = "latitude"   # <spaltenname1> (wert1: zz.zzzzzzz)
SPALTE_2 = "longitude"  # <spaltenname2> (wert2: zz.zzzzzz)
SPALTE_3 = "time_block" # <spaltenname3> (wert3: zz-zz)
SPALTE_4 = "date_block" # <spaltenname4> (wert4: zz-zz-zzzz)
SPALTE_5 = "maps_link"   # <spaltenname5> (wert5: externer Link)

VAR_NAME_1 = "lat"      # <varname1>
VAR_NAME_2 = "lon"      # <varname2>
VAR_NAME_3 = "time"     # <varname3>
VAR_NAME_4 = "date"     # <varname4>
VAR_NAME_5 = "maps_url"     # <varname5>

db = SQLAlchemy(app)

# ==========================================
# ADVANCED LOGGING KONFIGURATION
# ==========================================
log_file_path = os.path.join(BASE_DIR, "log", "api.log.txt")
file_handler = RotatingFileHandler(log_file_path, maxBytes=10485760, backupCount=5, encoding='utf-8')
log_formatter = logging.Formatter(
    '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.DEBUG)

app.logger.setLevel(logging.DEBUG)
app.logger.addHandler(file_handler)

logging.getLogger('werkzeug').addHandler(file_handler)


# ==========================================
# APK-Versionshandler / Autoupdate
# ==========================================
# Verzeichnisse initialisieren
for folder in [UPLOAD_FOLDER, LATEST_FOLDER, VERSIONS_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Datenbank-Modell
class ApkVersion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    version_string = db.Column(db.String(50), unique=True, nullable=False)

with app.app_context():
    db.create_all()

# Hilfsfunktionen
def get_latest_apk_info():
    """Gibt den Dateinamen und die Version der l-apk zurück, falls vorhanden."""
    if not os.path.exists(LATEST_FOLDER):
        return None, None
    files = [f for f in os.listdir(LATEST_FOLDER) if f.endswith('.apk')]
    if not files:
        return None, None
    
    filename = files[0]
    version_str = filename.rsplit('.', 1)[0]
    return filename, version_str

def save_version_to_db(version_str):
    """Speichert eine Version in der Datenbank, falls noch nicht vorhanden."""
    exists = ApkVersion.query.filter_by(version_string=version_str).first()
    if not exists:
        new_version = ApkVersion(version_string=version_str)
        db.session.add(new_version)
        db.session.commit()

def get_next_suffix_version(version_str):
    """Ermittelt für identische Versionen das Suffix .01, .02 etc."""
    counter = 1
    while True:
        suffix_version = f"{version_str}.{counter:02d}"
        target_dir = os.path.join(VERSIONS_FOLDER, suffix_version)
        if not os.path.exists(target_dir):
            return suffix_version, target_dir
        counter += 1

# ==========================================
# CORS KONFIGURATION (OHNE EXTRA BIBLIOTHEK)
# ==========================================

@app.before_request
def handle_options_requests():
    """Fängt Preflight CORS-Anfragen (OPTIONS) direkt ab und loggt eingehende Requests."""
    headers_dict = {k: v for k, v in request.headers.items()}
    
    # Schutz vor dem Lesen von Binärdaten/großen Dateien im Speicher
    if request.path.startswith('/apk/upload/') or (request.content_length and request.content_length > 50000):
        data_log = f"[Payload skipped - Binary or Large Data ({request.content_length} bytes)]"
    else:
        try:
            data_log = request.get_data(as_text=True)[:1000]
        except Exception:
            data_log = "[Undecodable Binary Data]"

    app.logger.debug(
        f"INCOMING REQUEST: {request.method} {request.url}\n"
        f"Headers: {headers_dict}\n"
        f"Remote ADDR: {request.remote_addr}\n"
        f"Data: {data_log}"
    )

    if request.method == 'OPTIONS':
        response = make_response()
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Authorization, Content-Type, Origin, Accept")
        response.headers.add("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        response.headers.add("Access-Control-Max-Age", "86400")
        app.logger.debug("OPTIONS Preflight direkt beantwortet.")
        return response, 200

@app.after_request
def add_cors_headers(response):
    """Hängt die erforderlichen CORS-Header an jede reguläre API-Antwort an und loggt diese."""
    if not response.headers.get("Access-Control-Allow-Origin"):
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Authorization, Content-Type, Origin, Accept")
        response.headers.add("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
    
    # Verhindert den RuntimeError bei Streaming-/Datei-Antworten
    if response.direct_passthrough:
        content_log = "[Direct Passthrough / File Stream]"
    else:
        try:
            content_log = response.get_data(as_text=True)[:1000]
        except Exception:
            content_log = "[Undecodable Binary Data]"

    app.logger.debug(
        f"OUTGOING RESPONSE: Status {response.status_code}\n"
        f"Headers: {dict(response.headers)}\n"
        f"Content: {content_log}"
    )
    return response

# ==========================================
# DATENBANK-MODELLE
# ==========================================

class SystemConfig(db.Model):
    __tablename__ = 'system_config'
    id = db.Column(db.Integer, primary_key=True)
    gateway_mode = db.Column(db.String(20), default='Server')  # 'Server' oder 'Direkt'
    spotify_client_id = db.Column(db.String(100), nullable=True)
    spotify_client_secret = db.Column(db.String(100), nullable=True)
    spotify_refresh_token = db.Column(db.Text, nullable=True)
    spotify_access_token = db.Column(db.Text, nullable=True)
    spotify_token_expires_at = db.Column(db.Integer, default=0)

class ClientCredentials(db.Model):
    __tablename__ = 'client_credentials'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(80), unique=True, nullable=False)
    client_secret_hash = db.Column(db.String(128), nullable=False)
    client_secret_plain = db.Column(db.String(128), nullable=True)
    name = db.Column(db.String(100), nullable=False, default="Unbekannt")
    role = db.Column(db.String(20), nullable=False)
    allowed_scopes = db.Column(db.String(200), nullable=False)

class AuthorizationCode(db.Model):
    __tablename__ = 'authorization_codes'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(128), unique=True, nullable=False)
    client_id = db.Column(db.String(80), nullable=False)
    redirect_uri = db.Column(db.String(255), nullable=False)
    scope = db.Column(db.String(200), nullable=False)
    expires_at = db.Column(db.Integer, nullable=False)

class OAuthToken(db.Model):
    __tablename__ = 'oauth_tokens'
    id = db.Column(db.Integer, primary_key=True)
    access_token = db.Column(db.String(128), unique=True, nullable=False)
    client_id = db.Column(db.String(80), nullable=False)
    scope = db.Column(db.String(200), nullable=False)
    expires_at = db.Column(db.Integer, nullable=False)
    
class UserSessionTimeline(db.Model):
    __tablename__ = 'user_session_timeline'
    timeline_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    datum = db.Column(db.String(10), nullable=False)  # Format: dd-mm-jjjj
    username = db.Column(db.String(100), nullable=False)
    session_starttime = db.Column(db.String(5), nullable=False)  # Format: hh-mm
    session_endtime = db.Column(db.String(5), nullable=False)    # Format: hh-mm

class UserActionTimeline(db.Model):
    __tablename__ = 'user_action_timeline'
    action_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    datum = db.Column(db.String(10), nullable=False)  # Format: dd-mm-jjjj
    time = db.Column(db.String(8), nullable=False)   # Format: hh-mm-ss
    triggered_action = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(100), nullable=False)

class ErrorReport(db.Model):
    __tablename__ = 'error_reports'
    report_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    severity = db.Column(db.String(10), nullable=False)
    username = db.Column(db.String(100), nullable=False)
    app_version = db.Column(db.String(50), nullable=False)
    error_task = db.Column(db.Text, nullable=False)
    error = db.Column(db.Text, nullable=False)
    date = db.Column(db.String(10), nullable=False)
    time = db.Column(db.String(5), nullable=False)
    last_action = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))

class MassErrorReport(db.Model):
    __tablename__ = 'mass_errorreport_errors'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user = db.Column(db.String(100), nullable=False)
    app_version = db.Column(db.String(50), nullable=False)
    mass_errors = db.Column(db.Text, nullable=False)

with app.app_context():
    db.create_all()
    

# Absoluter Pfad zum Basis-Ressourcenordner
BASE_DIR = os.path.abspath("app_ressources")

def validate_error_report_params():
    app_version = request.args.get('app-version')
    error_task = request.args.get('error_task')
    error = request.args.get('error')
    report_date = request.args.get('date')
    report_time = request.args.get('time')
    last_action = request.args.get('last-action')

    required_params = {
        'app-version': app_version,
        'error_task': error_task,
        'error': error,
        'date': report_date,
        'time': report_time,
        'last-action': last_action,
    }
    missing_params = [name for name, value in required_params.items() if value is None]
    if missing_params:
        return None, f"Missing query parameters: {', '.join(missing_params)}"

    if not re.fullmatch(r'\d+\.\d+\.\d+', app_version):
        return None, 'Invalid app-version format. Expected x.y.z.'

    if not re.fullmatch(r'\d{2}\.\d{2}\.(\d{2}|\d{4})', report_date):
        return None, 'Invalid date format. Expected dd.mm.yyyy or dd.mm.yy.'

    if not re.fullmatch(r'\d{2}\.\d{2}', report_time):
        return None, 'Invalid time format. Expected hh.mm.'

    return {
        'app_version': app_version,
        'error_task': error_task,
        'error': error,
        'date': report_date,
        'time': report_time,
        'last_action': last_action,
    }, None


def save_error_report(severity, username):
    report_data, validation_error = validate_error_report_params()
    if validation_error:
        return jsonify({'status': 'error', 'message': validation_error}), 400

    report = ErrorReport(severity=severity, username=username, **report_data)
    db.session.add(report)
    db.session.commit()

    app.logger.info(f"{severity.upper()} Error-Report gespeichert für User: {username}")
    return jsonify({'status': 'ok', 'report_id': report.report_id}), 201


@app.route('/report/error/soft/<username>', methods=['POST'])
def report_soft_error(username):
    return save_error_report('soft', username)


@app.route('/report/error/hard/<username>', methods=['POST'])
def report_hard_error(username):
    return save_error_report('hard', username)


@app.route('/report/error/massreport/<username>', methods=['POST'])
def report_mass_error(username):
    app_version = request.args.get('app-version')
    mass_errors = request.args.get('error')

    missing_params = []
    if app_version is None:
        missing_params.append('app-version')
    if mass_errors is None:
        missing_params.append('error')
    if missing_params:
        return jsonify({'status': 'error', 'message': f"Missing query parameters: {', '.join(missing_params)}"}), 400

    if not re.fullmatch(r'\d+\.\d+\.\d+', app_version):
        return jsonify({'status': 'error', 'message': 'Invalid app-version format. Expected x.y.z.'}), 400

    report = MassErrorReport(user=username, app_version=app_version, mass_errors=mass_errors)
    db.session.add(report)
    db.session.commit()

    app.logger.info(f"Mass-Error-Report gespeichert für User: {username}")
    return jsonify({'status': 'ok', 'id': report.id}), 201


@app.route('/ressources/<path:resource_name>', methods=['GET'])
def download_resource(resource_name):
    # safe_join verhindert Directory-Traversal-Angriffe (z.B. mit '../../../')
    target_dir = safe_join(BASE_DIR, resource_name)
    
    # Validierung: Existiert das Verzeichnis?
    if not target_dir or not os.path.isdir(target_dir):
        abort(404, description="Das angegebene Verzeichnis existiert nicht.")
    
    # Auslesen aller Dateien im spezifischen Ordner
    try:
        files = [f for f in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, f))]
    except OSError:
        abort(500, description="Interner Fehler beim Lesen des Verzeichnisses.")
    
    # Validierung der Anforderung: "immer nur eine Datei"
    if len(files) == 0:
        abort(404, description="Das Verzeichnis ist leer.")
    elif len(files) > 1:
        abort(400, description="Inkonsistenter Zustand: Mehr als eine Datei vorhanden.")
    
    filename = files[0]
    
    # Senden der Datei als Download (as_attachment=True)
    return send_from_directory(target_dir, filename, as_attachment=True)


## --- ROUTE 5: POST/GET Position erfassen ---
# Erlaubt POST und GET, damit die Route direkt im Browser aufgerufen werden kann.
@app.route("/app/user/pos/<wert0>", methods=["POST", "GET"])
def post_user_position(wert0):
    # Parameter aus den URL-Query-Variablen auslesen
    wert1 = request.args.get(VAR_NAME_1)
    wert2 = request.args.get(VAR_NAME_2)
    wert3 = request.args.get(VAR_NAME_3)
    wert4 = request.args.get(VAR_NAME_4)
    wert5 = request.args.get(VAR_NAME_5)

    # Validierung: Alle Parameter müssen vorhanden sein
    if not all([wert1, wert2, wert3, wert4, wert5]):
        return "Bad Request: Missing query parameters.", 400

    # Validierung der Formate via Regex
    if not (
        re.match(r"^\d+\.\d+$", wert1) and       # Zahl mit Punkt (beliebig viele Ziffern)
        re.match(r"^\d+\.\d+$", wert2) and       # Zahl mit Punkt (beliebig viele Ziffern)
        re.match(r"^\d{2}-\d{2}$", wert3) and    # zz-zz
        re.match(r"^\d{2}-\d{2}-\d{4}$", wert4)  # zz-zz-zzzz
    ):
        return "Bad Request: Invalid format constraints.", 400

    # Neues DB-Objekt dynamisch instanziieren
    position_data = {
        "username": wert0,
        SPALTE_1: wert1,
        SPALTE_2: wert2,
        SPALTE_3: wert3,
        SPALTE_4: wert4,
        SPALTE_5: wert5
    }
    
    new_entry = UserPosition(**position_data)
    db.session.add(new_entry)
    db.session.commit()
    
    app.logger.info(f"Position registriert für User: {wert0}")
    return "Position recorded successfully.", 201



## --- ROUTE 6: GET Positionen abfragen (Admin) ---
@app.route("/app/get/pos/<wert0>", methods=["GET"])
def get_user_position_admin(wert0):
    admin_user = request.args.get("admin")
    password = request.args.get("passwd")

    # Authentifizierung prüfen via ADMIN_USERS (aus Ihrem Bestand) und Passwort
    if not admin_user or admin_user not in ADMIN_USERS:
        abort(403, description="Forbidden: Invalid admin user.")
        
    if not password or password != ADMIN_REQUEST_PASSWORD:
        abort(403, description="Forbidden: Invalid password.")

    # Datenbankabfrage nach Username
    rows = UserPosition.query.filter_by(username=wert0).all()
    
    if not rows:
        return Response("No records found.", mimetype="text/plain")

    # Plain-Text Formatierung generieren
    output = []
    for r in rows:
        w1 = getattr(r, SPALTE_1)
        w2 = getattr(r, SPALTE_2)
        w3 = getattr(r, SPALTE_3)
        w4 = getattr(r, SPALTE_4)
        w5 = getattr(r, SPALTE_5)
        output.append(
            f"ID: {r.id} | User: {r.username} | {SPALTE_1}: {w1} | {SPALTE_2}: {w2} | {SPALTE_3}: {w3} | {SPALTE_4}: {w4} | {SPALTE_5}: {w5}"
        )

    return Response("\n".join(output), mimetype="text/plain")


def parse_position_datetime(date_str, time_str):
    """Konvertiert Positionsdatum/-uhrzeit für Sortierung und Linienbildung."""
    date_part = parse_custom_date(date_str)
    time_part = parse_custom_time(time_str)
    if not date_part or not time_part:
        return None
    return datetime.combine(date_part.date(), time_part.time())


def haversine_distance_km(lat1, lon1, lat2, lon2):
    """Berechnet den geographischen Abstand zwischen zwei Punkten in Kilometern."""
    earth_radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return earth_radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_position_map_data():
    """Liest Positionsdaten aus der Datenbank und bereitet Marker-/Linien-Daten für die Karte vor."""
    grouped_points = {}

    for row in UserPosition.query.all():
        try:
            latitude = float(getattr(row, SPALTE_1))
            longitude = float(getattr(row, SPALTE_2))
        except (TypeError, ValueError):
            app.logger.warning(f"Ungültige Positionskoordinaten für Datensatz {row.id} übersprungen.")
            continue

        timestamp = parse_position_datetime(getattr(row, SPALTE_4), getattr(row, SPALTE_3))
        if timestamp is None:
            app.logger.warning(f"Ungültiger Positionszeitpunkt für Datensatz {row.id} übersprungen.")
            continue

        grouped_points.setdefault(row.username, []).append({
            "id": row.id,
            "username": row.username,
            "latitude": latitude,
            "longitude": longitude,
            "date": getattr(row, SPALTE_4),
            "time": getattr(row, SPALTE_3),
            "timestamp": timestamp.isoformat(),
            "maps_link": getattr(row, SPALTE_5),
        })

    users = []
    palette = [
        "#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c",
        "#0891b2", "#be123c", "#4f46e5", "#65a30d", "#ca8a04",
    ]

    for index, username in enumerate(sorted(grouped_points)):
        points = sorted(grouped_points[username], key=lambda point: point["timestamp"])
        lines = []
        current_line = []
        previous_point = None

        for point in points:
            starts_new_line = False
            if previous_point is not None:
                distance_km = haversine_distance_km(
                    previous_point["latitude"],
                    previous_point["longitude"],
                    point["latitude"],
                    point["longitude"],
                )
                time_delta_seconds = (
                    datetime.fromisoformat(point["timestamp"])
                    - datetime.fromisoformat(previous_point["timestamp"])
                ).total_seconds()
                starts_new_line = distance_km > 2 or time_delta_seconds > 60 * 60

            if starts_new_line and current_line:
                lines.append(current_line)
                current_line = []

            current_line.append([point["latitude"], point["longitude"]])
            previous_point = point

        if current_line:
            lines.append(current_line)

        users.append({
            "username": username,
            "color": palette[index % len(palette)],
            "points": points,
            "lines": lines,
        })

    return users


@app.route('/map', methods=['GET'])
def position_map():
    return render_template('map.html', users=build_position_map_data())


# Dynamic Model Creation für flexible Spaltennamen
class UserPosition(db.Model):
    __tablename__ = TABELLEN_NAME
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), nullable=False)

# Dynamisches Hinzufügen der konfigurierbaren Spalten
setattr(UserPosition, SPALTE_1, db.Column(db.String(50), nullable=False))
setattr(UserPosition, SPALTE_2, db.Column(db.String(50), nullable=False))
setattr(UserPosition, SPALTE_3, db.Column(db.String(10), nullable=False))
setattr(UserPosition, SPALTE_4, db.Column(db.String(15), nullable=False))
setattr(UserPosition, SPALTE_5, db.Column(db.Text, nullable=False))
    
    
    
# ==========================================
# TIMELINE & ACTION TRACKING ENDPUNKTE 
# ==========================================

def parse_custom_date(date_str):
    """Konvertiert dd-mm-yyyy oder dd-mm-yy in ein datetime-Objekt für Vergleiche."""
    for fmt in ("%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None

def parse_custom_time(time_str):
    """Konvertiert hh-mm-ss oder hh-mm in ein datetime-Objekt für Vergleiche."""
    for fmt in ("%H-%M-%S", "%H-%M"):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    return None

def verify_admin_header():
    """Überprüft, ob das im 'user'-Header übergebene Wort in der Admin-Liste existiert."""
    user_header = request.headers.get("user")
    if not user_header or user_header not in ADMIN_USERS:
        app.logger.warning(f"Unerlaubter Admin-Zugriffsversuch von Header-User: {user_header}")
        abort(403, description="Forbidden: Invalid or missing admin user header.")


## --- ROUTE 1: POST Session ---
@app.route("/app/user/online/<wert0>/<wert1>/<wert2>/<wert3>", methods=["POST"])
def post_user_online(wert0, wert1, wert2, wert3):
    # Validierung der Formate via Regex
    if not (
        re.match(r"^\d{2}-\d{2}-\d{4}$", wert0)  # dd-mm-jjjj
        and re.match(r"^\d{2}-\d{2}$", wert1)    # hh-mm
        and re.match(r"^\d{2}-\d{2}$", wert2)    # hh-mm
    ):
        return "Bad Request: Invalid format constraints.", 400

    new_session = UserSessionTimeline(
        datum=wert0,
        session_starttime=wert1,
        session_endtime=wert2,
        username=wert3
    )
    db.session.add(new_session)
    db.session.commit()
    
    app.logger.info(f"User-Session registriert für: {wert3}")
    return "Session recorded successfully.", 201


## --- ROUTE 2: GET Session (Admin) ---
@app.route("/app/admin/online/<wert1>", defaults={"p1": None, "p2": None}, methods=["GET"])
@app.route("/app/admin/online/<wert1>/<p1>", defaults={"p2": None}, methods=["GET"])
@app.route("/app/admin/online/<wert1>/<p1>/<p2>", methods=["GET"])
def get_admin_online(wert1, p1, p2):
    verify_admin_header()

    username = wert1
    filter_datum = None
    filter_starttime = None

    # Dynamische Erkennung der optionalen Parameter anhand ihres Formats
    for p in [p1, p2]:
        if not p:
            continue
        if re.match(r"^\d{2}-\d{2}-\d{4}$", p):
            filter_datum = p
        elif re.match(r"^\d{2}-\d{2}$", p):
            filter_starttime = p

    # Vorfilterung auf Datenbank-Ebene nach Benutzername
    rows = UserSessionTimeline.query.filter_by(username=username).all()
    filtered_rows = []

    for row in rows:
        # Filterung Datum (Gleich oder nach Wert0)
        if filter_datum:
            db_date = parse_custom_date(row.datum)
            f_date = parse_custom_date(filter_datum)
            if not db_date or not f_date or db_date < f_date:
                continue

        # Filterung Startzeit (Gleich oder nach Wert2)
        if filter_starttime:
            db_time = parse_custom_time(row.session_starttime)
            f_time = parse_custom_time(filter_starttime)
            if not db_time or not f_time or db_time < f_time:
                continue

        filtered_rows.append(row)

    # Formatierung der Rückgabe als Plain-Text
    output = []
    for r in filtered_rows:
        output.append(f"ID: {r.timeline_id} | Date: {r.datum} | User: {r.username} | Start: {r.session_starttime} | End: {r.session_endtime}")

    return Response("\n".join(output) if output else "No records found.", mimetype="text/plain")


## --- ROUTE 3: POST Action ---
@app.route("/app/user/action/<wert0>/<wert1>/<wert2>/<wert3>", methods=["POST"])
def post_user_action(wert0, wert1, wert2, wert3):
    if not (
        re.match(r"^\d{2}-\d{2}-\d{4}$", wert0)     # dd-mm-jjjj
        and re.match(r"^\d{2}-\d{2}-\d{2}$", wert1)  # hh-mm-ss
    ):
        return "Bad Request: Invalid format constraints.", 400

    # Ersetze explizit alle Leerzeichen im Action-String durch Unterstriche
    action_text = wert2.replace(" ", "_")

    new_action = UserActionTimeline(
        datum=wert0,
        time=wert1,
        triggered_action=action_text,
        username=wert3
    )
    db.session.add(new_action)
    db.session.commit()

    app.logger.info(f"User-Action registriert für: {wert3} ({action_text})")
    return "Action recorded successfully.", 201


## --- ROUTE 4: GET Action (Admin) ---
@app.route("/app/admin/action/<wert0>", defaults={"p1": None, "p2": None, "p3": None}, methods=["GET"])
@app.route("/app/admin/action/<wert0>/<p1>", defaults={"p2": None, "p3": None}, methods=["GET"])
@app.route("/app/admin/action/<wert0>/<p1>/<p2>", defaults={"p3": None}, methods=["GET"])
@app.route("/app/admin/action/<wert0>/<p1>/<p2>/<p3>", methods=["GET"])
def get_admin_action(wert0, p1, p2, p3):
    verify_admin_header()

    username = wert0
    filter_datum = None
    filter_time = None
    filter_action = None

    # Dynamische Parameter-Identifikation anhand von Syntax/Formatierungen
    for p in [p1, p2, p3]:
        if not p:
            continue
        if re.match(r"^\d{2}-\d{2}-\d{2}$", p):  # Format dd-mm-jj (oder dd-mm-jjjj)
            filter_datum = p
        elif re.match(r"^\d{2}-\d{2}$", p):     # Format hh-mm
            filter_time = p
        else:
            filter_action = p

    rows = UserActionTimeline.query.filter_by(username=username).all()
    filtered_rows = []

    for row in rows:
        # Filterung Datum (Gleich oder nach Filterwert)
        if filter_datum:
            db_date = parse_custom_date(row.datum)
            f_date = parse_custom_date(filter_datum)
            if not db_date or not f_date or db_date < f_date:
                continue

        # Filterung Uhrzeit (Gleich oder nach Filterwert)
        if filter_time:
            db_time = parse_custom_time(row.time)
            f_time = parse_custom_time(filter_time)
            if not db_time or not f_time or db_time < f_time:
                continue

        # Filterung exakter Action-Inhalt (falls übergeben)
        if filter_action and row.triggered_action != filter_action:
            continue

        filtered_rows.append(row)

    output = []
    for r in filtered_rows:
        output.append(f"ID: {r.action_id} | Date: {r.datum} | Time: {r.time} | Action: {r.triggered_action} | User: {r.username}")

    return Response("\n".join(output) if output else "No records found.", mimetype="text/plain")
  
  

# ==========================================
# STATISCHE KONFIGURATION & HELFER
# ==========================================

TARGET_BACKENDS = [
    "http://100.115.184.104:8020",
    "http://127.0.0.1:8020",
    "http://37.44.215.123:8020"
]

ALLOWED_REDIRECT_URIS = [
    "https://tasker.joaoapps.com/auth.html",
    "http://100.115.184.104:8020",
    "http://127.0.0.1:8020",
    "http://37.44.215.123:8020",
    "https://client.extrahelden.de/auth/callback"
]

ROLE_SCOPES = {
    'Server': ['server', 'hb-server'],
    'Client': ['client', 'hcb-client']
}

SPOTIFY_FIXED_REDIRECT_URI = "https://api.extrahelden.de/callback"

SPOTIFY_SCOPES = (
    "user-modify-playback-state "
    "user-read-playback-state "
    "user-read-currently-playing "
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-public "
    "playlist-modify-private "
    "user-read-playback-position "
    "app-remote-control"
)

def get_config():
    cfg = SystemConfig.query.first()
    if not cfg:
        app.logger.info("Keine Systemkonfiguration gefunden. Erstelle Default-Eintrag (Server-Modus).")
        cfg = SystemConfig(gateway_mode='Server')
        db.session.add(cfg)
        db.session.commit()
    return cfg

def get_valid_spotify_token(cfg):
    if not cfg.spotify_refresh_token:
        app.logger.warning("Kein Spotify Refresh Token in der Datenbank vorhanden.")
        return None
    if cfg.spotify_access_token and cfg.spotify_token_expires_at > (time.time() + 30):
        app.logger.debug("Bestehender Spotify Access Token ist noch gültig.")
        return cfg.spotify_access_token

    app.logger.info("Spotify Access Token abgelaufen oder nicht vorhanden. Starte Refresh-Vorgang...")
    try:
        url = "https://accounts.spotify.com/api/token"
        data = {
            "grant_type": "refresh_token",
            "refresh_token": cfg.spotify_refresh_token
        }
        app.logger.debug(f"POST zu Spotify Token API: {url} mit Refresh Token.")
        res = requests.post(
            url,
            data=data,
            auth=(cfg.spotify_client_id, cfg.spotify_client_secret),
            timeout=5
        )
        app.logger.debug(f"Spotify Token API Antwort-Status: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            cfg.spotify_access_token = data.get("access_token")
            if "refresh_token" in data:
                cfg.spotify_refresh_token = data.get("refresh_token")
            cfg.spotify_token_expires_at = int(time.time()) + data.get("expires_in", 3600)
            db.session.commit()
            app.logger.info("Spotify Access Token erfolgreich erneuert und in DB gespeichert.")
            return cfg.spotify_access_token
        else:
            app.logger.error(f"Fehler beim Erneuern des Spotify Tokens: {res.text}")
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Netzwerk-Ausnahme während des Spotify Token Refreshes: {str(e)}")
    return None

def verify_gateway_token(headers):
    auth_header = headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        app.logger.warning("Verifizierung fehlgeschlagen: Authorization Header fehlt oder ist kein Bearer-Token.")
        return False, "Missing or malformed Authorization header"
    
    token_str = auth_header.split(' ')[1]
    token_entry = OAuthToken.query.filter_by(access_token=token_str).first()
    
    if not token_entry:
        app.logger.warning(f"Verifizierung fehlgeschlagen: Token '{token_str}' existiert nicht in DB.")
        return False, "Invalid token"
    if token_entry.expires_at < time.time():
        app.logger.warning(f"Verifizierung fehlgeschlagen: Token von Client '{token_entry.client_id}' ist abgelaufen.")
        return False, "Token expired"
        
    app.logger.debug(f"Gateway-Token verifiziert für Client-ID: {token_entry.client_id}")
    return True, token_entry

def execute_proxy_request(target_path, method='GET', custom_spotify_handler=None):
    """Zentraler Proxy-Abforderer für die dedizierten Routen"""
    app.logger.debug(f"Verarbeite Proxy-Request für Pfad: {target_path} [{method}]")
    is_valid, token_or_err = verify_gateway_token(request.headers)
    if not is_valid:
        return jsonify({"error": "unauthorized", "message": token_or_err}), 401

    cfg = get_config()
    app.logger.debug(f"Aktueller Gateway-Modus: {cfg.gateway_mode}")

    if cfg.gateway_mode == 'Direkt':
        spotify_token = get_valid_spotify_token(cfg)
        if not spotify_token:
            app.logger.error("Direkt-Modus aktiv, aber kein gültiger Spotify-Token ermittelbar.")
            return jsonify({"error": "bad_gateway", "message": "Gateway im Direkt-Modus, aber Spotify ist nicht autorisiert!"}), 502

        if custom_spotify_handler:
            app.logger.debug("Führe dedizierten Custom Spotify Handler aus.")
            return custom_spotify_handler(spotify_token)

        proxy_headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
        proxy_headers['Authorization'] = f"Bearer {spotify_token}"
        
        # Bereinige v1-Dopplung und Slashes, da target_path bereits '/v1/...' enthält oder enthalten soll
        clean_path = target_path.lstrip('/')
        if not clean_path.startswith('v1/'):
            target_url = f"https://api.spotify.com/v1/{clean_path}"
        else:
            target_url = f"https://api.spotify.com/{clean_path}"
        
        app.logger.debug(f"Leite Request direkt an Spotify-API weiter: {target_url}")
        try:
            res = requests.request(
                method=method,
                url=target_url,
                headers=proxy_headers,
                data=request.get_data(),
                cookies=request.cookies,
                allow_redirects=False,
                timeout=10
            )
            app.logger.debug(f"Antwort von Spotify-API erhalten. Status: {res.status_code}")
            excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection', 'access-control-allow-origin']
            response_headers = [(k, v) for k, v in res.headers.items() if k.lower() not in excluded_headers]
            return res.content, res.status_code, response_headers
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Fehler bei Anfrage an Spotify-API: {str(e)}")
            return jsonify({"error": "bad_gateway", "message": str(e)}), 502

    # Server-Modus (Lokale Backends durchlaufen)
    app.logger.debug("Server-Modus aktiv. Leite Anfrage an lokale Backends weiter...")
    proxy_headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
    last_response_data = None
    last_status_code = 502
    proxy_response_headers = {}
    success = False

    for backend in TARGET_BACKENDS:
        target_url = f"{backend.rstrip('/')}/{target_path.lstrip('/')}"
        app.logger.debug(f"Probiere Backend: {target_url}")
        try:
            res = requests.request(
                method=method,
                url=target_url,
                headers=proxy_headers,
                data=request.get_data(),
                cookies=request.cookies,
                allow_redirects=False,
                timeout=10
            )
            app.logger.debug(f"Backend {backend} hat geantwortet mit Status: {res.status_code}")
            if not success:
                last_response_data = res.content
                last_status_code = res.status_code
                proxy_response_headers = dict(res.headers)
                success = True
        except requests.exceptions.RequestException as e:
            app.warning(f"Backend {backend} nicht erreichbar: {str(e)}")
            continue

    if not success:
        app.logger.error("Keines der konfigurierten lokalen Backends hat geantwortet.")
        return jsonify({"error": "bad_gateway", "message": "No backend responded"}), 502

    excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection', 'access-control-allow-origin']
    response_headers = [(k, v) for k, v in proxy_response_headers.items() if k.lower() not in excluded_headers]
    return last_response_data, last_status_code, response_headers

# ==========================================
# DASHBOARD TEMPLATE
# ==========================================

DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>HBC OAuth2 & Proxy Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    <script>
        function revealSecret(clientId, plainSecret) {
            let pwd = prompt("Bitte Passwort zur Bestätigung eingeben:");
            // Hier das gewünschte Bestätigungspasswort festlegen (Standard: admin123)
            if (pwd === "112358") {
                document.getElementById('secret-' + clientId).innerText = plainSecret;
            } else if (pwd !== null) {
                alert("Falsches Passwort! Zugriff verweigert.");
            }
        }
    </script>
</head>
<body class="bg-gray-900 text-gray-100 font-sans antialiased p-8">
    <div class="max-w-6xl mx-auto">
        <header class="mb-8 border-b border-gray-800 pb-4">
            <h1 class="text-3xl font-bold text-white tracking-tight">HBC Gateway Management</h1>
            <p class="text-sm text-gray-400 mt-1">OAuth2 Provider & Reverse-Proxy für Debian-Umgebungen</p>
        </header>

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="mb-4 p-4 rounded {% if category == 'error' %}bg-red-900/50 border border-red-700 text-red-200{% else %}bg-green-900/50 border border-green-700 text-green-200{% endif %}">
                {{ message|safe }}
              </div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <div class="bg-gray-800 p-6 rounded-lg border border-gray-700 shadow-xl mb-8">
            <h2 class="text-xl font-semibold text-white mb-4">Gateway-Betriebsmodus</h2>
            <form action="/dashboard/config/save" method="POST" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-3 gap-6 items-end">
                    <div>
                        <label class="block text-xs font-medium uppercase tracking-wider text-gray-400 mb-1">Routing-Modus</label>
                        <select name="gateway_mode" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white focus:outline-none focus:border-blue-500">
                            <option value="Server" {% if config.gateway_mode == 'Server' %}selected{% endif %}>Server-Modus (Lokale Backends)</option>
                            <option value="Direkt" {% if config.gateway_mode == 'Direkt' %}selected{% endif %}>Direkt-Modus (User-Linked Spotify API)</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium uppercase tracking-wider text-gray-400 mb-1">Spotify Client ID</label>
                        <input type="text" name="spotify_client_id" value="{{ config.spotify_client_id or '' }}" placeholder="ID eintragen" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-xs font-medium uppercase tracking-wider text-gray-400 mb-1">Spotify Client Secret</label>
                        <input type="password" name="spotify_client_secret" value="{{ config.spotify_client_secret or '' }}" placeholder="••••••••••••••••" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-blue-500">
                    </div>
                </div>

                <div class="p-3 bg-gray-900 rounded border border-gray-700/50 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
                    <div>
                        <span class="text-xs font-medium uppercase tracking-wider text-gray-400 block">Spotify Login-Status</span>
                        {% if config.spotify_refresh_token %}
                            <span class="text-sm font-semibold text-emerald-400 flex items-center gap-1">● Verbunden und autorisiert</span>
                        {% else %}
                            <span class="text-sm font-semibold text-yellow-500 flex items-center gap-1">○ Nicht autorisiert (Aktion erforderlich)</span>
                        {% endif %}
                    </div>
                    {% if config.spotify_client_id and config.spotify_client_secret %}
                        <a href="/dashboard/spotify/login" class="bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-1.5 rounded transition text-xs shadow">
                            {% if config.spotify_refresh_token %}Konto neu verknüpfen{% else %}Mit Spotify verbinden & autorisieren{% endif %}
                        </a>
                    {% endif %}
                </div>

                <div class="text-xs text-gray-400 font-mono bg-gray-950 p-2 rounded border border-gray-800">
                    Hinweis: Tragen Sie im Spotify Developer Dashboard als Redirect URI exakt ein: <span class="text-blue-400 select-all">{{ callback_url }}</span>
                </div>

                <div class="flex justify-end pt-2">
                    <button type="submit" class="bg-emerald-600 hover:bg-emerald-500 text-white font-medium px-6 py-2 rounded transition shadow-lg text-sm">
                        Konfiguration speichern
                    </button>
                </div>
            </form>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div class="bg-gray-800 p-6 rounded-lg border border-gray-700 shadow-xl h-fit">
                <h2 class="text-xl font-semibold text-white mb-4">OAuth-Client erstellen</h2>
                <form action="/dashboard/client/create" method="POST" class="space-y-4">
                    <div>
                        <label class="block text-xs font-medium uppercase tracking-wider text-gray-400 mb-1">Name / Zuordnung *</label>
                        <input type="text" name="name" required placeholder="z.B. Felix Tasker" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-xs font-medium uppercase tracking-wider text-gray-400 mb-1">Eigene Client ID (Optional)</label>
                        <input type="text" name="custom_client_id" placeholder="Leer lassen für Auto-Gen" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-xs font-medium uppercase tracking-wider text-gray-400 mb-1">Eigenes Client Secret (Optional)</label>
                        <input type="password" name="custom_client_secret" placeholder="Leer lassen für Auto-Gen" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white font-mono text-sm focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-xs font-medium uppercase tracking-wider text-gray-400 mb-1">Rolle</label>
                        <select name="role" class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white focus:outline-none focus:border-blue-500">
                            <option value="Client">Client (Scopes: client, hcb-client)</option>
                            <option value="Server">Server (Scopes: server, hb-server)</option>
                        </select>
                    </div>
                    <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 text-white font-medium py-2 rounded transition shadow-lg">
                        Zugangsdaten generieren
                    </button>
                </form>
            </div>

            <div class="lg:col-span-2 bg-gray-800 p-6 rounded-lg border border-gray-700 shadow-xl">
                <h2 class="text-xl font-semibold text-white mb-4">Aktive API-Clients</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm text-gray-300">
                        <thead class="text-xs uppercase bg-gray-900 text-gray-400 tracking-wider">
                            <tr>
                                <th class="p-3">Zuordnung</th>
                                <th class="p-3">Client ID</th>
                                <th class="p-3">Client Secret</th>
                                <th class="p-3">Rolle</th>
                                <th class="p-3">Zugelassene Scopes</th>
                                <th class="p-3 text-right">Aktion</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-gray-700">
                            {% for client in clients %}
                            <tr class="hover:bg-gray-750 transition">
                                <td class="p-3 font-medium text-white">{{ client.name }}</td>
                                <td class="p-3 font-mono text-blue-400 selection:bg-blue-900">{{ client.client_id }}</td>
                                <td class="p-3 font-mono text-xs">
                                    <span id="secret-{{ client.client_id }}" class="text-gray-500">••••••••••••••••</span>
                                    <button onclick="revealSecret('{{ client.client_id }}', '{{ client.client_secret_plain or '' }}')" class="ml-2 text-xs bg-gray-700 hover:bg-gray-600 text-gray-200 px-1.5 py-0.5 rounded transition">Anzeigen</button>
                                </td>
                                <td class="p-3">
                                    <span class="px-2 py-0.5 rounded text-xs font-medium {% if client.role == 'Server' %}bg-purple-900/60 text-purple-200 border border-purple-700{% else %}bg-emerald-900/60 text-emerald-200 border border-emerald-700{% endif %}">
                                        {{ client.role }}
                                    </span>
                                </td>
                                <td class="p-3 font-mono text-xs text-gray-400">{{ client.allowed_scopes }}</td>
                                <td class="p-3 text-right">
                                    <a href="/dashboard/client/delete/{{ client.id }}" class="text-red-400 hover:text-red-300 font-medium transition" onclick="return confirm('Client unwiderruflich löschen?')">Löschen</a>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

# ==========================================
# APK-Versionshandler / Autoupdater
# ==========================================

@app.route('/apk/online', methods=['GET', 'POST'])
def upload_apk_online():
    if request.method == 'GET':
        return render_template('upload.html')

    version = request.form.get('version', '').strip()
    apk_file = request.files.get('apk_file')

    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        return render_template('upload.html', error='Bitte gib eine Versionsnummer im Format x.y.z an.', version=version), 400

    if apk_file is None or apk_file.filename == '':
        return render_template('upload.html', error='Bitte wähle eine .apk-Datei aus.', version=version), 400

    original_filename = secure_filename(apk_file.filename)
    if not original_filename.lower().endswith('.apk'):
        return render_template('upload.html', error='Bitte wähle eine Datei mit der Endung .apk aus.', version=version), 400

    n_filename = f"{version}.apk"
    n_path = os.path.join(UPLOAD_FOLDER, n_filename)

    bytes_received = 0
    try:
        with open(n_path, 'wb') as f:
            while True:
                chunk = apk_file.stream.read(1048576)
                if not chunk:
                    break
                f.write(chunk)
                bytes_received += len(chunk)
    except Exception as e:
        app.logger.error(f"Fehler beim Online-APK-Upload-Streaming: {str(e)}")
        if os.path.exists(n_path):
            os.remove(n_path)
        return render_template('upload.html', error=f'Upload failed during streaming: {str(e)}', version=version), 500

    if bytes_received == 0:
        if os.path.exists(n_path):
            os.remove(n_path)
        return render_template('upload.html', error='Die hochgeladene Datei ist leer.', version=version), 400

    app.logger.info(f"Online-APK erfolgreich gestreamt. Größe: {bytes_received} Bytes.")

    # Ab hier startet derselbe Verarbeitungsprozess wie beim rohen APK-Upload.
    l_filename, l_version_str = get_latest_apk_info()

    if l_filename is None:
        move(n_path, os.path.join(LATEST_FOLDER, n_filename))
        return render_template('upload.html', success='Initial APK uploaded successfully as latest', version=''), 201

    n_ver = parse_version(version)
    l_ver = parse_version(l_version_str)

    if n_ver > l_ver:
        target_dir = os.path.join(VERSIONS_FOLDER, l_version_str)
        os.makedirs(target_dir, exist_ok=True)
        move(os.path.join(LATEST_FOLDER, l_filename), os.path.join(target_dir, l_filename))
        save_version_to_db(l_version_str)
        move(n_path, os.path.join(LATEST_FOLDER, n_filename))

    elif n_ver < l_ver:
        target_dir = os.path.join(VERSIONS_FOLDER, version)
        os.makedirs(target_dir, exist_ok=True)
        move(n_path, os.path.join(target_dir, n_filename))
        save_version_to_db(version)

    else:
        suffix_version, target_dir = get_next_suffix_version(l_version_str)
        os.makedirs(target_dir, exist_ok=True)
        suffix_filename = f"{suffix_version}.apk"
        move(os.path.join(LATEST_FOLDER, l_filename), os.path.join(target_dir, suffix_filename))
        save_version_to_db(suffix_version)
        move(n_path, os.path.join(LATEST_FOLDER, n_filename))

    return render_template('upload.html', success='APK processed successfully', version=''), 200

## 1. Route: APK Upload (Akzeptiert rohen Binärstream von Tasker)
@app.route('/apk/upload/<version>', methods=['POST'])
def upload_apk(version):
    n_filename = f"{version}.apk" 
    n_path = os.path.join(UPLOAD_FOLDER, n_filename)
    
    # Inkrementelles Streaming direkt in die Datei (umgeht 500KB-Form-Limits und spart RAM)
    bytes_received = 0
    try:
        with open(n_path, 'wb') as f:
            while True:
                # Erhöht auf 256 KB (262144 Bytes) oder 1 MB (1048576 Bytes)
                chunk = request.stream.read(1048576)
                if not chunk:
                    break
                f.write(chunk)
                bytes_received += len(chunk)
    except Exception as e:
        app.logger.error(f"Fehler beim APK-Upload-Streaming: {str(e)}")
        if os.path.exists(n_path):
            os.remove(n_path)
        return f"Upload failed during streaming: {str(e)}", 500

    if bytes_received == 0:
        if os.path.exists(n_path):
            os.remove(n_path)
        return "No data received in request body", 400
    
    app.logger.info(f"APK erfolgreich gestreamt. Größe: {bytes_received} Bytes.")

    # 2. Bestehende l-apk ermitteln
    l_filename, l_version_str = get_latest_apk_info()

    if l_filename is None:
        move(n_path, os.path.join(LATEST_FOLDER, n_filename))
        return "Initial APK uploaded successfully as latest", 201

    # 3. Versionsvergleich
    n_ver = parse_version(version)
    l_ver = parse_version(l_version_str)

    if n_ver > l_ver:
        target_dir = os.path.join(VERSIONS_FOLDER, l_version_str)
        os.makedirs(target_dir, exist_ok=True)
        move(os.path.join(LATEST_FOLDER, l_filename), os.path.join(target_dir, l_filename))
        save_version_to_db(l_version_str)
        move(n_path, os.path.join(LATEST_FOLDER, n_filename))

    elif n_ver < l_ver:
        target_dir = os.path.join(VERSIONS_FOLDER, version)
        os.makedirs(target_dir, exist_ok=True)
        move(n_path, os.path.join(target_dir, n_filename))
        save_version_to_db(version)

    else:
        suffix_version, target_dir = get_next_suffix_version(l_version_str)
        os.makedirs(target_dir, exist_ok=True)
        suffix_filename = f"{suffix_version}.apk"
        move(os.path.join(LATEST_FOLDER, l_filename), os.path.join(target_dir, suffix_filename))
        save_version_to_db(suffix_version)
        move(n_path, os.path.join(LATEST_FOLDER, n_filename))

    return "APK processed successfully", 200

## 2. Route: Latest APK Download
@app.route('/apk/latest', methods=['GET'])
def download_latest():
    l_filename, _ = get_latest_apk_info()
    if not l_filename:
        abort(404, description="No APK available")
    return send_from_directory(LATEST_FOLDER, l_filename, as_attachment=True)

## 3. Route: Latest Version Plain Text
@app.route('/apk/latest/version', methods=['GET'])
def latest_version():
    _, l_version_str = get_latest_apk_info()
    if not l_version_str:
        abort(404, description="No APK available")
    return l_version_str, 200, {'Content-Type': 'text/plain'}

## 4. Route: Alle Versionen auflisten
@app.route('/apk/versions', methods=['GET'])
def list_all_versions():
    # Versionen aus der DB abrufen
    db_versions = [v.version_string for v in ApkVersion.query.all()]
    
    # Aktuelle Version aus 'latest' abrufen
    _, l_version_str = get_latest_apk_info()
    
    all_versions = []
    if l_version_str:
        all_versions.append(l_version_str)
    all_versions.extend(db_versions)
    
    # Sortierung der Versionen (optional, aber empfohlen)
    all_versions.sort(key=parse_version, reverse=True)
    
    output = "\n".join(all_versions)
    return output, 200, {'Content-Type': 'text/plain'}

## 5. Route: Spezifische Version downloaden
@app.route('/apk/version/<version>', methods=['GET'])
def download_specific_version(version):
    version_dir = os.path.join(VERSIONS_FOLDER, version)
    filename = f"{version}.apk"
    
    if not os.path.exists(os.path.join(version_dir, filename)):
        abort(404, description="Version not found")
        
    return send_from_directory(version_dir, filename, as_attachment=True)

    
# ==========================================
# DEFINIERTE PLAYER ENDPUNKTE
# ==========================================

@app.route('/player', methods=['GET'])
def get_player_status():
    return execute_proxy_request('/v1/me/player', method='GET')

def get_all_update_files():
    """Hilfsfunktion: Liest alle x.y.z.txt Dateien, sortiert sie nach SemVer."""
    file_paths = glob.glob(os.path.join(UPDATES_DIR, "*.txt"))
    updates = []
    
    for path in file_paths:
        filename = os.path.basename(path)
        version_str = filename[:-4] # ".txt" abschneiden
        try:
            # Validiert und ermöglicht korrekte Sortierung (1.10.0 > 1.2.0)
            version_obj = parse_version(version_str)
            updates.append({
                'version_str': version_str,
                'version_obj': version_obj,
                'path': path
            })
        except Exception:
            # Ignoriert Dateien, die nicht dem Schema x.y.z entsprechen
            continue
            
    # Sortiert aufsteigend nach Versionsnummer
    updates.sort(key=lambda x: x['version_obj'])
    return updates


@app.route('/add-update/<version>', methods=['POST'])
def add_update(version):
    # Validierung des Versionsformats
    try:
        parse_version(version)
    except Exception:
        abort(400, description="Ungültiges Versionsformat. Erwartet wird x.y.z")

    # Tasker sendet die Datei direkt im Body des Requests (request.data)
    # Wir prüfen, ob Daten mitgesendet wurden
    if not request.data:
        abort(400, description="Der Request-Body ist leer. Keine Dateidaten von Tasker empfangen.")

    file_path = os.path.join(UPDATES_DIR, f"{version}.txt")
    
    try:
        # Die Daten liegen als Bytes vor und werden direkt binär ('wb') geschrieben
        with open(file_path, 'wb') as file:
            file.write(request.data)
            
        return Response(f"Update {version} erfolgreich aus Tasker-Inhalt erstellt.\n", mimetype='text/plain', status=201)
    except Exception as e:
        abort(500, description=f"Fehler beim Schreiben der Datei auf dem Server: {str(e)}")


@app.route('/updates', methods=['GET'])
def get_all_updates():
    updates = get_all_update_files()
    if not updates:
        return Response("Keine Updates vorhanden.\n", mimetype='text/plain')
        
    output = []
    for update in updates:
        try:
            with open(update['path'], 'r', encoding='utf-8') as file:
                output.append(file.read())
        except Exception as e:
            abort(500, description=f"Fehler beim Lesen von {update['version_str']}: {str(e)}")
            
    # Dateien zusammenfügen mit Trenner-Zeile
    separator = "\n------------\n"
    return Response(separator.join(output), mimetype='text/plain')


@app.route('/updates/<start_version>/<end_version>', methods=['GET'])
def get_version_range(start_version, end_version):
    try:
        start_obj = parse_version(start_version)
        end_obj = parse_version(end_version)
    except Exception:
        abort(400, description="Ungültiges Versionsformat in der URL.")
        
    if start_obj > end_obj:
        abort(400, description="Die Startversion darf nicht größer als die Endversion sein.")
        
    updates = get_all_update_files()
    output = []
    
    for update in updates:
        # Filter: Version MUSS strikt größer als start_version UND kleiner oder gleich end_version sein
        if start_obj < update['version_obj'] <= end_obj:
            try:
                with open(update['path'], 'r', encoding='utf-8') as file:
                    output.append(file.read())
            except Exception as e:
                abort(500, description=f"Fehler beim Lesen von {update['version_str']}: {str(e)}")

    if not output:
        return Response("Keine Updates im angegebenen Bereich gefunden.\n", mimetype='text/plain')

    separator = "\n------------\n"
    return Response(separator.join(output), mimetype='text/plain')
    
@app.route('/player/endpoints', methods=['GET'])
def get_player_endpoints():
    return jsonify({
        "endpoints": [
            {"path": "/player", "method": "GET", "description": "Abfragen des Player-Status und des aktuellen Songs"},
            {"path": "/player/endpoints", "method": "GET", "description": "Liste aller Player-Endpunkte (Keine Auth)"},
            {"path": "/player/play-pause", "method": "GET", "description": "Prüft ob Musik aktuell wiedergegeben wird"},
            {"path": "/player/pause", "method": "PUT", "description": "Pausiert die Musikwiedergabe"},
            {"path": "/player/play", "method": "PUT", "description": "Startet oder setzt die Musikwiedergabe fort"},
            {"path": "/player/next", "method": "POST", "description": "Springt zum nächsten Song"},
            {"path": "/player/previous", "method": "POST", "description": "Springt zum vorherigen Song"},
            {"path": "/player/get-repeat", "method": "GET", "description": "Abfragen des aktuellen Repeat-Status"},
            {"path": "/player/repeat/<value>", "method": "PUT", "description": "Setzt den Repeat-Modus (off, context, track)"}
        ]
    }), 200

@app.route('/player/play-pause', methods=['GET'])
def get_player_play_pause():
    def handle_spotify(token):
        headers = {"Authorization": f"Bearer {token}"}
        try:
            url = "https://api.spotify.com/v1/me/player"
            app.logger.debug(f"Custom Handler: GET zu Spotify Player API: {url}")
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 204:
                return jsonify({"is_playing": False}), 200
            if res.status_code == 200:
                data = res.json()
                return jsonify({"is_playing": data.get("is_playing", False)}), 200
            return res.content, res.status_code
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Custom Handler Fehler bei GET /v1/me/player: {str(e)}")
            return jsonify({"error": "bad_gateway", "message": str(e)}), 502

    return execute_proxy_request('/v1/me/player', method='GET', custom_spotify_handler=handle_spotify)

@app.route('/player/pause', methods=['PUT', 'POST', 'GET'])
def set_player_pause():
    return execute_proxy_request('/v1/me/player/pause', method='PUT')

@app.route('/player/play', methods=['PUT', 'POST', 'GET'])
def set_player_play():
    return execute_proxy_request('/v1/me/player/play', method='PUT')

@app.route('/player/next', methods=['POST', 'PUT', 'GET'])
def set_player_next():
    app.logger.info("Endpunkt /player/next aufgerufen.")
    return execute_proxy_request('/v1/me/player/next', method='POST')

@app.route('/player/previous', methods=['POST', 'PUT', 'GET'])
def set_player_previous():
    app.logger.info("Endpunkt /player/previous aufgerufen.")
    return execute_proxy_request('/v1/me/player/previous', method='POST')

@app.route('/player/get-repeat', methods=['GET'])
def get_player_repeat():
    def handle_spotify(token):
        headers = {"Authorization": f"Bearer {token}"}
        try:
            url = "https://api.spotify.com/v1/me/player"
            app.logger.debug(f"Custom Handler Repeat: GET zu Spotify Player API: {url}")
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                return jsonify({"repeat_state": data.get("repeat_state", "off")}), 200
            # Fehlerfall oder Fallback: Sicherstellen, dass hier HTTP 200 mit Fallback-Wert
            # oder der echte, fehlerhafte Statuscode sauber zurückgegeben wird. 
            # Wenn Spotify 204 wirft (Kein aktives Device), fangen wir das ab:
            if res.status_code == 204:
                return jsonify({"repeat_state": "off"}), 200
            return jsonify({"error": "spotify_error", "message": "Could not retrieve repeat state"}), res.status_code
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Custom Handler Repeat Fehler: {str(e)}")
            return jsonify({"error": "bad_gateway", "message": str(e)}), 502

    return execute_proxy_request('/v1/me/player', method='GET', custom_spotify_handler=handle_spotify)

@app.route('/player/repeat/<value>', methods=['PUT', 'POST', 'GET'])
def set_player_repeat(value):
    if value not in ['off', 'context', 'track']:
        return jsonify({"error": "bad_request", "message": "Value must be 'off', 'context' or 'track'"}), 400
    return execute_proxy_request(f'/v1/me/player/repeat?state={value}', method='PUT')

# ==========================================
# DEFINIERTE QUEUE ENDPUNKTE
# ==========================================

@app.route('/queue/endpoints', methods=['GET'])
def get_queue_endpoints():
    return jsonify({
        "endpoints": [
            {"path": "/queue/endpoints", "method": "GET", "description": "Liste aller Queue-Endpunkte (Keine Auth)"},
            {"path": "/queue/get-list", "method": "GET", "description": "Liefert gefilterte Warteschlange mit ID, Name und Artist"},
            {"path": "/queue/remove/<spotify-song-id>", "method": "DELETE", "description": "Entfernt einen Song aus der Warteschlange"},
            {"path": "/queue/add/<spotify-song-id>", "method": "POST", "description": "Fügt einen Song zur Warteschlange hinzu"}
        ]
    }), 200

@app.route('/queue/get-list', methods=['GET'])
def get_queue_list():
    def handle_spotify(token):
        headers = {"Authorization": f"Bearer {token}"}
        try:
            url = "https://api.spotify.com/v1/me/player/queue"
            app.logger.debug(f"Custom Handler Queue-List: GET zu Spotify API: {url}")
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                spotify_data = res.json()
                raw_queue = spotify_data.get("queue", [])
                
                transformed_queue = []
                for track in raw_queue:
                    artists = ", ".join([artist.get("name", "") for artist in track.get("artists", [])])
                    transformed_queue.append({
                        "spotify-song-id": track.get("id"),
                        "songname": track.get("name"),
                        "artistname": artists
                    })
                return jsonify(transformed_queue), 200
            return res.content, res.status_code
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Custom Handler Queue-List Fehler: {str(e)}")
            return jsonify({"error": "bad_gateway", "message": str(e)}), 502

    return execute_proxy_request('/v1/me/player/queue', method='GET', custom_spotify_handler=handle_spotify)

@app.route('/queue/remove/<string:song_id>', methods=['DELETE', 'POST', 'GET'])
def remove_queue_item(song_id):
    def handle_spotify(token):
        app.logger.warning(f"Abgelehnt: Löschen aus der Queue (ID: {song_id}) wird von Spotify nativ nicht unterstützt.")
        return jsonify({
            "error": "not_supported", 
            "message": "Spotify API bietet nativ keine Moeglichkeit, Elemente direkt aus der Warteschlange zu entfernen."
        }), 451
    return execute_proxy_request(f'/v1/me/player/queue/remove/{song_id}', method='DELETE', custom_spotify_handler=handle_spotify)

@app.route('/queue/add/<string:song_id>', methods=['POST', 'GET', 'PUT'])
def add_queue_item(song_id):
    spotify_uri = f"spotify:track:{song_id}"
    return execute_proxy_request(f'/v1/me/player/queue?uri={spotify_uri}', method='POST')

# ==========================================
# OAUTH2 AUTHENTIFIZIERUNGSLOGIK
# ==========================================

@app.route('/authorize', methods=['GET'])
def authorize():
    client_id = request.args.get('client_id')
    redirect_uri = request.args.get('redirect_uri')
    scope = request.args.get('scope', '')
    state = request.args.get('state')

    app.logger.info(f"OAuth /authorize aufgerufen für Client-ID: {client_id}")

    client = ClientCredentials.query.filter_by(client_id=client_id).first()
    if not client:
        app.logger.error(f"OAuth Fehler: Client-ID {client_id} ungültig.")
        return jsonify({"error": "invalid_client"}), 400
    if redirect_uri not in ALLOWED_REDIRECT_URIS:
        app.logger.error(f"OAuth Fehler: Redirect-URI '{redirect_uri}' nicht erlaubt.")
        return jsonify({"error": "invalid_redirect_uri"}), 400

    code = secrets.token_urlsafe(32)
    auth_code = AuthorizationCode(
        code=code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        expires_at=int(time.time()) + 600
    )
    db.session.add(auth_code)
    db.session.commit()

    target_url = f"{redirect_uri}?code={code}"
    if state:
        target_url += f"&state={state}"
    
    app.logger.info(f"OAuth /authorize erfolgreich. Code generiert. Leite weiter zu: {redirect_uri}")
    return redirect(target_url)

@app.route('/token', methods=['POST'])
def token():
    auth = request.authorization
    if auth:
        client_id = auth.username
        client_secret = auth.password
    else:
        client_id = request.form.get('client_id')
        client_secret = request.form.get('client_secret')

    grant_type = request.form.get('grant_type')
    app.logger.info(f"OAuth /token aufgerufen mit Grant-Type '{grant_type}' für Client-ID: {client_id}")

    client = ClientCredentials.query.filter_by(client_id=client_id).first()
    if not client or not check_password_hash(client.client_secret_hash, client_secret):
        app.logger.error("OAuth /token Fehler: Client-Authentifizierung fehlgeschlagen.")
        return jsonify({"error": "invalid_client"}), 401

    if grant_type == 'authorization_code':
        code = request.form.get('code')
        auth_code = AuthorizationCode.query.filter_by(code=code).first()
        if not auth_code or auth_code.expires_at < time.time() or auth_code.client_id != client_id:
            app.logger.error("OAuth /token Fehler: Authorization Code ist abgelaufen oder ungültig.")
            return jsonify({"error": "invalid_grant"}), 400
        db.session.delete(auth_code)
    elif grant_type == 'client_credentials':
        pass
    else:
        app.logger.error(f"OAuth /token Fehler: Nicht unterstützter Grant-Type '{grant_type}'.")
        return jsonify({"error": "unsupported_grant_type"}), 400

    access_token = "hbc_" + secrets.token_urlsafe(64)
    token_entry = OAuthToken(
        access_token=access_token,
        client_id=client_id,
        scope=client.allowed_scopes,
        expires_at=int(time.time()) + 3600 * 24
    )
    db.session.add(token_entry)
    db.session.commit()

    app.logger.info(f"OAuth Token erfolgreich generiert für Client '{client_id}'.")
    return jsonify({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 3600 * 24,
        "scope": client.allowed_scopes
    })

# ==========================================
# DASHBOARD ADMINISTRATIVE ROUTEN
# ==========================================

@app.route('/dashboard', methods=['GET'])
def dashboard():
    clients = ClientCredentials.query.all()
    config = get_config()
    return render_template_string(DASHBOARD_TEMPLATE, clients=clients, config=config, callback_url=SPOTIFY_FIXED_REDIRECT_URI)

@app.route('/dashboard/config/save', methods=['POST'])
def save_config():
    cfg = get_config()
    new_mode = request.form.get('gateway_mode', 'Server')
    cfg.gateway_mode = new_mode
    cfg.spotify_client_id = request.form.get('spotify_client_id', '').strip() or None
    cfg.spotify_client_secret = request.form.get('spotify_client_secret', '').strip() or None
    db.session.commit()
    
    app.logger.info(f"Systemkonfiguration über Dashboard aktualisiert. Neuer Modus: {new_mode}")
    
    if new_mode == 'Direkt' and not cfg.spotify_refresh_token and cfg.spotify_client_id:
        flash("Modus geändert. Bitte klicken Sie jetzt auf 'Mit Spotify verbinden & autorisieren'.", "warning")
    else:
        flash("Konfiguration erfolgreich aktualisiert.", "success")
    return redirect('/dashboard')

@app.route('/dashboard/spotify/login', methods=['GET'])
def spotify_login():
    cfg = get_config()
    if not cfg.spotify_client_id:
        flash("Bitte tragen Sie zuerst die Spotify Client ID ein.", "error")
        return redirect('/dashboard')
        
    spotify_auth_url = (
        f"https://accounts.spotify.com/authorize"
        f"?client_id={cfg.spotify_client_id}"
        f"&response_type=code"
        f"&redirect_uri={SPOTIFY_FIXED_REDIRECT_URI}"
        f"&scope={SPOTIFY_SCOPES}"
    )
    app.logger.info("Initiiere OAuth2-Login zu Spotify über Dashboard.")
    return redirect(spotify_auth_url)

@app.route('/callback', methods=['GET'])
def spotify_callback():
    code = request.args.get('code')
    error = request.args.get('error')
    if error:
        app.logger.error(f"Spotify Autorisierung abgebrochen durch User/API. Fehler: {error}")
        flash(f"Autorisierung abgebrochen: {error}", "error")
        return redirect('/dashboard')
        
    cfg = get_config()
    app.logger.info("Spotify Callback empfangen. Generiere initialen Access/Refresh-Token...")
    try:
        res = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": SPOTIFY_FIXED_REDIRECT_URI
            },
            auth=(cfg.spotify_client_id, cfg.spotify_client_secret),
            timeout=5
        )
        app.logger.debug(f"Spotify Callback API Token-Response Status: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            cfg.spotify_refresh_token = data.get("refresh_token")
            cfg.spotify_access_token = data.get("access_token")
            cfg.spotify_token_expires_at = int(time.time()) + data.get("expires_in", 3600)
            db.session.commit()
            app.logger.info("Erfolgreich initialen Spotify Refresh Token verknüpft und gespeichert.")
            flash("Erfolgreich mit Spotify-Konto verknüpft!", "success")
        else:
            app.logger.error(f"Fehler bei initialer Token-Generierung von Spotify: {res.text}")
            flash(f"Fehler bei Token-Generierung: {res.text}", "error")
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Netzwerkfehler im Spotify Callback Handler: {str(e)}")
        flash(f"Netzwerkfehler zur Spotify-API: {str(e)}", "error")
    return redirect('/dashboard')

@app.route('/dashboard/client/create', methods=['POST'])
def create_client():
    role = request.form.get('role')
    name = request.form.get('name', '').strip()
    custom_id = request.form.get('custom_client_id', '').strip()
    custom_secret = request.form.get('custom_client_secret', '').strip()

    if role not in ROLE_SCOPES:
        flash("Ungültige Rolle ausgewählt.", "error")
        return redirect('/dashboard')

    if not name:
        flash("Ein Name zur Zuordnung muss zwingend angegeben werden.", "error")
        return redirect('/dashboard')

    # Fallback auf automatische Generierung falls leer
    raw_client_id = custom_id if custom_id else secrets.token_hex(12)
    raw_client_secret = custom_secret if custom_secret else secrets.token_urlsafe(32)
    scopes_str = " ".join(ROLE_SCOPES[role])
    
    # Prüfen ob Custom ID bereits existiert
    if ClientCredentials.query.filter_by(client_id=raw_client_id).first():
        flash(f"Die Client ID '{raw_client_id}' existiert bereits.", "error")
        return redirect('/dashboard')

    new_client = ClientCredentials(
        client_id=raw_client_id,
        client_secret_hash=generate_password_hash(raw_client_secret),
        client_secret_plain=raw_client_secret,
        name=name,
        role=role,
        allowed_scopes=scopes_str
    )
    db.session.add(new_client)
    db.session.commit()

    app.logger.info(f"Neuer API-Client über Dashboard generiert. ID: {raw_client_id}, Rolle: {role}, Zuordnung: {name}")
    flash(f"Client erfolgreich erstellt!<br><b>Zuordnung:</b> {name}<br><b>Client-ID:</b> <code class='bg-gray-900 px-1 text-yellow-400 font-mono'>{raw_client_id}</code><br><b>Client-Secret:</b> <code class='bg-gray-900 px-1 text-emerald-400 font-mono'>{raw_client_secret}</code>", "success")
    return redirect('/dashboard')

@app.route('/dashboard/client/delete/<int:id>', methods=['GET'])
def delete_client(id):
    client = ClientCredentials.query.get_or_404(id)
    client_id = client.client_id
    db.session.delete(client)
    db.session.commit()
    app.logger.info(f"API-Client {client_id} über Dashboard gelöscht.")
    flash("Client erfolgreich gelöscht.", "success")
    return redirect('/dashboard')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
    app.logger.info("HBC Gateway API wird gestartet auf Port 2050...")
    app.run(host='0.0.0.0', port=2050, debug=True)
        
    # Ersetzt app.run() durch den produktiven Waitress-Server
    # from waitress import serve
    # app.logger.info("HBC Gateway API (Production WSGI via Waitress) wird gestartet auf Port 2050...")
    # serve(app, host='0.0.0.0', port=2050, threads=4, channel_timeout=120)
