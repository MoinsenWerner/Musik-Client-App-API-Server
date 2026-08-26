import os
import ast
import json
import inspect
import secrets
import sqlite3
import subprocess
import threading
import time
import logging
import re
import math
from datetime import datetime
from shutil import copy2, move, rmtree
from logging.handlers import RotatingFileHandler
import requests
from flask import Flask, request, jsonify, render_template_string, render_template, redirect, flash, make_response, abort, Response, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash, check_password_hash
import glob
from packaging.version import parse as parse_version
from werkzeug.utils import safe_join, secure_filename
from auth.app import app as auth_app
from chat import register_chat_routes

UPDATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "updates")
os.makedirs(UPDATES_DIR, exist_ok=True)


class AuthRoutingMiddleware:
    """Serve the passkey vault and gateway through the same WSGI listener."""

    AUTH_PATHS = {'/health', '/register', '/get'}

    def __init__(self, main_application, authentication_application):
        self.main_application = main_application
        self.authentication_application = authentication_application

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path == '/auth' or path.startswith('/auth/'):
            auth_environ = environ.copy()
            auth_environ['SCRIPT_NAME'] = environ.get('SCRIPT_NAME', '') + '/auth'
            auth_environ['PATH_INFO'] = path[5:] or '/'
            return self.authentication_application(auth_environ, start_response)
        if path in self.AUTH_PATHS or path.startswith('/api/register/') or path.startswith('/api/authenticate/'):
            return self.authentication_application(environ, start_response)
        return self.main_application(environ, start_response)


app = Flask(__name__)
register_chat_routes(app)
app.wsgi_app = AuthRoutingMiddleware(app.wsgi_app, auth_app.wsgi_app)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_FILE = os.path.join(BASE_DIR, 'oauth2_gateway.db')
DB_BACKUP_FOLDER = os.path.join(BASE_DIR, 'db_bak')
DB_BACKUP_GIT_FOLDER = os.path.join(DB_BACKUP_FOLDER, 'git-repository')
DB_BACKUP_GIT_URL = 'https://github.com/MoinsenWerner/Musik-Client-API-Server-DB-Backups.git'
DATABASE_BACKUP_LOCK = threading.Lock()
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'apk', 'new-upload')
LATEST_FOLDER = os.path.join(BASE_DIR, 'apk', 'latest')
VERSIONS_FOLDER = os.path.join(BASE_DIR, 'apk', 'versions')
COREDATA_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
COREDATA_FILENAME = 'core-data-abfragen.txt'
COREDATA_FILE = os.path.join(COREDATA_UPLOAD_FOLDER, COREDATA_FILENAME)
COREDATA_WRITE_LOCK = threading.Lock()
NOTIFICATION_DELIVERY_LOCK = threading.Lock()
ADMIN_USERS = ["felix", "test", "moin"]
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATABASE_FILE}'
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


def run_backup_git_command(arguments, cwd=None, timeout=20):
    """Führt einen Git-Befehl für das Backup-Repository ohne interaktive Eingaben aus."""
    return subprocess.run(
        ['git', *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
    )


def upload_database_backup_to_git(backup_path):
    """Versucht, ein Datenbank-Backup in das konfigurierte GitHub-Repository zu pushen."""
    if os.environ.get('DB_BACKUP_GIT_ENABLED', '1') != '1':
        return False

    try:
        if not os.path.isdir(os.path.join(DB_BACKUP_GIT_FOLDER, '.git')):
            if os.path.exists(DB_BACKUP_GIT_FOLDER):
                rmtree(DB_BACKUP_GIT_FOLDER)
            clone_result = run_backup_git_command([
                'clone', DB_BACKUP_GIT_URL, DB_BACKUP_GIT_FOLDER,
            ], cwd=DB_BACKUP_FOLDER, timeout=30)
            if clone_result.returncode != 0:
                app.logger.warning(f"Git-Clone für DB-Backup fehlgeschlagen: {clone_result.stderr.strip()}")
                return False

        copy2(backup_path, os.path.join(DB_BACKUP_GIT_FOLDER, os.path.basename(backup_path)))
        run_backup_git_command(['config', 'user.name', 'Musik Client API Backup'], cwd=DB_BACKUP_GIT_FOLDER)
        run_backup_git_command(['config', 'user.email', 'backup@musik-client-api.local'], cwd=DB_BACKUP_GIT_FOLDER)
        run_backup_git_command(['pull', '--rebase'], cwd=DB_BACKUP_GIT_FOLDER, timeout=30)
        add_result = run_backup_git_command(['add', os.path.basename(backup_path)], cwd=DB_BACKUP_GIT_FOLDER)
        if add_result.returncode != 0:
            app.logger.warning(f"Git-Add für DB-Backup fehlgeschlagen: {add_result.stderr.strip()}")
            return False
        commit_result = run_backup_git_command([
            'commit', '-m', f"Database backup {os.path.basename(backup_path)}",
        ], cwd=DB_BACKUP_GIT_FOLDER)
        if commit_result.returncode != 0:
            app.logger.warning(f"Git-Commit für DB-Backup fehlgeschlagen: {commit_result.stderr.strip()}")
            return False
        push_result = run_backup_git_command(['push', 'origin', 'HEAD'], cwd=DB_BACKUP_GIT_FOLDER, timeout=30)
        if push_result.returncode != 0:
            app.logger.warning(f"Git-Push für DB-Backup fehlgeschlagen: {push_result.stderr.strip()}")
            return False
        app.logger.info(f"Datenbank-Backup zu GitHub hochgeladen: {os.path.basename(backup_path)}")
        return True
    except (OSError, subprocess.SubprocessError) as error:
        app.logger.warning(f"Git-Upload für DB-Backup fehlgeschlagen: {error}")
        return False


def create_database_backup():
    """Erstellt mit der SQLite-Backup-API eine konsistente Kopie der aktuellen Datenbank."""
    if not os.path.isfile(DATABASE_FILE):
        app.logger.warning(f"DB-Backup übersprungen, Datenbankdatei fehlt: {DATABASE_FILE}")
        return None

    with DATABASE_BACKUP_LOCK:
        os.makedirs(DB_BACKUP_FOLDER, exist_ok=True)
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S-%f')
        backup_path = os.path.join(DB_BACKUP_FOLDER, f'oauth2_gateway-{timestamp}.db')
        with sqlite3.connect(DATABASE_FILE) as source_database:
            with sqlite3.connect(backup_path) as backup_database:
                source_database.backup(backup_database)
    app.logger.info(f"Datenbank-Backup erstellt: {backup_path}")
    upload_database_backup_to_git(backup_path)
    return backup_path


@event.listens_for(Session, 'after_commit')
def backup_database_after_commit(session):
    """Sichert die Datenbank nach jedem erfolgreichen SQLAlchemy-Commit dieser Anwendung."""
    bind = session.get_bind()
    if bind.url.database and os.path.abspath(bind.url.database) == DATABASE_FILE:
        try:
            create_database_backup()
        except (OSError, sqlite3.Error) as error:
            app.logger.error(f"Datenbank-Backup nach Commit fehlgeschlagen: {error}")

# ==========================================
# ADVANCED LOGGING KONFIGURATION
# ==========================================
log_file_path = os.path.join(BASE_DIR, "log", "api.log.txt")
os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
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
os.makedirs(COREDATA_UPLOAD_FOLDER, exist_ok=True)

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
    token_lifetime_seconds = db.Column(db.Integer, nullable=False, default=86400)

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


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    text = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(100), nullable=False)
    notification_group = db.Column(db.String(100), nullable=False)
    png_path = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))


class NotificationDelivery(db.Model):
    __tablename__ = 'notification_deliveries'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    notification_id = db.Column(db.Integer, nullable=False, index=True)
    client_id = db.Column(db.String(80), nullable=False, index=True)
    delivered_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))
    __table_args__ = (
        db.UniqueConstraint('notification_id', 'client_id', name='uq_notification_client'),
    )


class PlaylistContent(db.Model):
    __tablename__ = 'playlist_contents'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    playlist_id = db.Column(db.String(100), nullable=True, index=True)
    creator = db.Column(db.String(200), nullable=False, default='Admin')
    song_names = db.Column(db.Text, nullable=False)
    song_ids = db.Column(db.Text, nullable=False)
    image_links = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))
    updated_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))


class PlayingPlaylist(db.Model):
    __tablename__ = 'playing_playlist'
    id = db.Column(db.Integer, primary_key=True, default=1)
    playlist_content_id = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))


with app.app_context():
    db.create_all()
    client_columns = {
        column[1] for column in db.session.execute(db.text("PRAGMA table_info(client_credentials)"))
    }
    if 'token_lifetime_seconds' not in client_columns:
        db.session.execute(db.text(
            "ALTER TABLE client_credentials "
            "ADD COLUMN token_lifetime_seconds INTEGER NOT NULL DEFAULT 86400"
        ))
        db.session.commit()
    playlist_columns = {
        column[1] for column in db.session.execute(db.text("PRAGMA table_info(playlist_contents)"))
    }
    if 'creator' not in playlist_columns:
        db.session.execute(db.text(
            "ALTER TABLE playlist_contents "
            "ADD COLUMN creator VARCHAR(200) NOT NULL DEFAULT 'Admin'"
        ))
        db.session.commit()
    

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


COREDATA_PATTERN = re.compile(
    r'^Core-Data-Text:\n'
    r'Core-Data-Abfrage vom (?P<date>\d{2}\.\d{2}\.(?:\d{2}|\d{4})) '
    r'um (?P<time>\d{2}\.\d{2}) Uhr:\s*\n+'
    r'App-Versionsname Tasker: (?P<tasker_version>\d+\.\d+\.\d+)\s*\n'
    r'-_-_-_-_-_-\s*\n'
    r'App-Paketname: (?P<package>[a-z]+(?:\.[a-z]+)+)\s*\n'
    r'-_-_-_-_-_-\s*\n'
    r'Latest App-Versionsname auf Server: (?P<server_version>\d+\.\d+\.\d+)\s*\n'
    r'-_-_-_-_-_-\s*\n'
    r'Latest Updatenotes: (?P<update_notes>[\s\S]+?)\n'
    r'-_-_-_-_-_-\s*\n'
    r'API-Base-URL: (?P<api_url>https://\S+)\s*\n'
    r'-_-_-_-_-_-\s*\n'
    r'-{67}\s*$',
)


def get_coredata_request_text():
    """Liest Core-Daten bevorzugt aus dem Body, alternativ aus einem benannten Header."""
    body_text = request.get_data(as_text=True).strip()
    if body_text:
        return body_text, 'body'

    header_text = (
        request.headers.get('Core-Data-Text')
        or request.headers.get('X-Core-Data-Text')
    )
    if header_text:
        header_text = header_text.replace('\\n', '\n')
        if not header_text.startswith('Core-Data-Text:'):
            header_text = f"Core-Data-Text:\n{header_text}"
        return header_text.strip(), 'header'
    return None, None


@app.route('/coredatas/download', methods=['GET'])
def download_coredata():
    """Lädt die bisher gesammelten Core-Data-Abfragen als Textdatei herunter."""
    if not os.path.isfile(COREDATA_FILE):
        return jsonify({
            'status': 'error',
            'error': 'coredata_file_not_found',
            'message': 'Es wurden noch keine Core-Daten hochgeladen.',
        }), 404
    return send_from_directory(
        COREDATA_UPLOAD_FOLDER,
        COREDATA_FILENAME,
        as_attachment=True,
        download_name=COREDATA_FILENAME,
        mimetype='text/plain',
    )


@app.route('/coredatas/upload', methods=['POST'])
def upload_coredata():
    """Validiert eine Core-Data-Abfrage und hängt sie an die bestehende Datei an."""
    coredata_text, source = get_coredata_request_text()
    if not coredata_text:
        return jsonify({
            'status': 'error',
            'error': 'missing_coredata',
            'message': 'Core-Daten müssen im Textkörper oder Core-Data-Text-Header stehen.',
        }), 400

    normalized_text = coredata_text.replace('\r\n', '\n').replace('\r', '\n')
    if not COREDATA_PATTERN.fullmatch(normalized_text):
        return jsonify({
            'status': 'error',
            'error': 'invalid_coredata_format',
            'message': 'Die Core-Daten entsprechen nicht dem erwarteten Format.',
        }), 400

    os.makedirs(COREDATA_UPLOAD_FOLDER, exist_ok=True)
    with COREDATA_WRITE_LOCK:
        has_content = os.path.isfile(COREDATA_FILE) and os.path.getsize(COREDATA_FILE) > 0
        with open(COREDATA_FILE, 'a', encoding='utf-8', newline='\n') as coredata_file:
            if has_content:
                coredata_file.write('\n\n')
            coredata_file.write(normalized_text.rstrip() + '\n')

    app.logger.info(f"Core-Data-Abfrage aus dem {source} an {COREDATA_FILE} angehängt.")
    return jsonify({
        'status': 'ok',
        'source': source,
        'message': 'Core-Daten wurden an die bestehende Datei angehängt.',
    }), 201


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
    "https://client.cube-kingdom.de/auth/callback"
]

ROLE_SCOPES = {
    'Server': ['server', 'hb-server'],
    'Client': ['client', 'hcb-client']
}

SPOTIFY_FIXED_REDIRECT_URI = "https://api.cube-kingdom.de/callback"

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
        return False, {
            "error": "unauthorized",
            "message": "Missing or malformed Authorization header",
            "reauthenticate": True,
        }
    
    token_str = auth_header.split(' ')[1]
    token_entry = OAuthToken.query.filter_by(access_token=token_str).first()
    
    if not token_entry:
        app.logger.warning(f"Verifizierung fehlgeschlagen: Token '{token_str}' existiert nicht in DB.")
        return False, {
            "error": "invalid_token",
            "message": "The access token is invalid.",
            "reauthenticate": True,
        }
    if token_entry.expires_at < time.time():
        app.logger.warning(f"Verifizierung fehlgeschlagen: Token von Client '{token_entry.client_id}' ist abgelaufen.")
        return False, {
            "error": "token_expired",
            "message": "The access token has expired. Authenticate again to obtain a new token.",
            "reauthenticate": True,
            "token_endpoint": "/token",
            "expired_at": token_entry.expires_at,
        }
        
    app.logger.debug(f"Gateway-Token verifiziert für Client-ID: {token_entry.client_id}")
    return True, token_entry

def execute_proxy_request(target_path, method='GET', custom_spotify_handler=None, request_body=None):
    """Zentraler Proxy-Abforderer für die dedizierten Routen"""
    app.logger.debug(f"Verarbeite Proxy-Request für Pfad: {target_path} [{method}]")
    is_valid, token_or_err = verify_gateway_token(request.headers)
    if not is_valid:
        return jsonify(token_or_err), 401

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
        if request_body is not None:
            proxy_headers['Content-Type'] = 'application/json'
        
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
                data=request.get_data() if request_body is None else request_body,
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
    if request_body is not None:
        proxy_headers['Content-Type'] = 'application/json'
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
                data=request.get_data() if request_body is None else request_body,
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


def validate_notification_form():
    """Validiert und bereinigt die Eingabefelder einer Benachrichtigung."""
    values = {
        'title': request.form.get('title', '').strip(),
        'text': request.form.get('text', '').strip(),
        'category': request.form.get('category', '').strip(),
        'notification_group': request.form.get('notification_group', '').strip(),
        'png_path': request.form.get('png_path', '').strip() or None,
    }
    missing = [
        field for field in ('title', 'text', 'category', 'notification_group')
        if not values[field]
    ]
    if missing:
        return None, 'Titel, Text, Categorie und Gruppe sind Pflichtfelder.'
    if any('|' in value for value in values.values() if value):
        return None, 'Das Zeichen | ist in Benachrichtigungsfeldern nicht erlaubt.'
    return values, None


@app.route('/notify/new', methods=['GET'])
def get_new_notification():
    """Gibt die älteste, vom authentifizierten Client noch nicht empfangene Nachricht zurück."""
    is_valid, token_or_error = verify_gateway_token(request.headers)
    if not is_valid:
        return jsonify(token_or_error), 401

    with NOTIFICATION_DELIVERY_LOCK:
        delivered_ids = db.select(NotificationDelivery.notification_id).where(
            NotificationDelivery.client_id == token_or_error.client_id
        )
        notification = Notification.query.filter(
            ~Notification.id.in_(delivered_ids)
        ).order_by(Notification.created_at.asc(), Notification.id.asc()).first()
        if notification is None:
            return Response(status=204)

        db.session.add(NotificationDelivery(
            notification_id=notification.id,
            client_id=token_or_error.client_id,
        ))
        db.session.commit()

    fields = [
        notification.title,
        notification.text,
        notification.category,
        notification.notification_group,
    ]
    if notification.png_path:
        fields.append(notification.png_path)
    return Response('|'.join(fields), content_type='text/plain; charset=utf-8')


@app.route('/notify/add', methods=['GET', 'POST'])
def add_notification():
    """Zeigt das Formular zum Erstellen einer Benachrichtigung und speichert dessen Inhalt."""
    if request.method == 'GET':
        return render_template('notify_add.html')

    values, error = validate_notification_form()
    if error:
        return render_template('notify_add.html', error=error, values=request.form), 400

    notification = Notification(**values)
    db.session.add(notification)
    db.session.commit()
    flash('Benachrichtigung wurde gespeichert.', 'success')
    return redirect('/notify/edit')


@app.route('/notify/edit', methods=['GET', 'POST'])
def edit_notifications():
    """Zeigt vorhandene Benachrichtigungen und verarbeitet Änderungen oder Löschungen."""
    error = None
    if request.method == 'POST':
        notification_id = request.form.get('notification_id', type=int)
        notification = db.session.get(Notification, notification_id) if notification_id else None
        if notification is None:
            error = 'Die ausgewählte Benachrichtigung wurde nicht gefunden.'
        elif request.form.get('action') == 'delete':
            NotificationDelivery.query.filter_by(notification_id=notification.id).delete()
            db.session.delete(notification)
            db.session.commit()
            flash('Benachrichtigung wurde gelöscht.', 'success')
            return redirect('/notify/edit')
        else:
            values, error = validate_notification_form()
            if not error:
                for field, value in values.items():
                    setattr(notification, field, value)
                db.session.commit()
                flash('Benachrichtigung wurde aktualisiert.', 'success')
                return redirect('/notify/edit')

    notifications = Notification.query.order_by(
        Notification.created_at.desc(), Notification.id.desc()
    ).all()
    return render_template('notify_edit.html', notifications=notifications, error=error), 400 if error else 200

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
                    <div>
                        <label class="block text-xs font-medium uppercase tracking-wider text-gray-400 mb-1">Schlüssel-Gültigkeit (Minuten)</label>
                        <input type="number" name="token_lifetime_minutes" min="1" max="525600" value="1440" required class="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white focus:outline-none focus:border-blue-500">
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
                                <th class="p-3">Schlüssel-Gültigkeit</th>
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
                                <td class="p-3">
                                    <form action="/dashboard/client/token-lifetime/{{ client.id }}" method="POST" class="flex items-center gap-2">
                                        <input type="number" name="token_lifetime_minutes" min="1" max="525600" value="{{ (client.token_lifetime_seconds / 60)|int }}" required class="w-24 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white">
                                        <span class="text-xs text-gray-400">Min.</span>
                                        <button type="submit" class="text-blue-400 hover:text-blue-300 font-medium">Speichern</button>
                                    </form>
                                </td>
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


def parse_playlist_values(value):
    """Teilt eine kommaseparierte Query-Angabe und erhält bewusst leere Bildeinträge."""
    if value is None:
        return None
    return [item.strip() for item in value.split(',')]


def find_playlist_content(name, playlist_id=None):
    """Sucht eine gespeicherte Playlist bevorzugt anhand ihrer Spotify-ID."""
    query = PlaylistContent.query
    if playlist_id:
        return query.filter_by(playlist_id=playlist_id).order_by(PlaylistContent.updated_at.desc()).first()
    return query.filter_by(name=name).order_by(PlaylistContent.updated_at.desc()).first()


def serialize_playlist_content(playlist):
    """Gibt gespeicherten Playlistinhalt als strukturiertes JSON-kompatibles Objekt zurück."""
    names = json.loads(playlist.song_names)
    song_ids = json.loads(playlist.song_ids)
    images = json.loads(playlist.image_links)
    return {
        'name': playlist.name,
        'playlist_id': playlist.playlist_id,
        'ersteller': playlist.creator or 'Admin',
        'content': names,
        'ids': song_ids,
        'bilder': images,
        'songs': [
            {'name': name, 'id': song_id, 'bild': images[index] or None}
            for index, (name, song_id) in enumerate(zip(names, song_ids))
        ],
    }


def format_playlist_content(playlist):
    """Formatiert Playlistdaten im vom Musik-Client erwarteten Textformat."""
    names = json.loads(playlist.song_names)
    song_ids = json.loads(playlist.song_ids)
    images = json.loads(playlist.image_links)
    return "\n___\n".join((
        ','.join(names),
        ','.join(song_ids),
        ','.join(images),
        playlist.name,
        playlist.playlist_id or '',
    ))


@app.route('/playlistcontent/list', methods=['POST'])
def save_playlist_content():
    """Speichert oder aktualisiert den per Query-Parametern übertragenen Playlistinhalt."""
    name = request.args.get('name', '').strip()
    names = parse_playlist_values(request.args.get('content'))
    song_ids = parse_playlist_values(request.args.get('ids'))
    images = parse_playlist_values(request.args.get('bilder'))
    playlist_id = request.args.get('pl-id', '').strip() or None
    creator = request.args.get('ersteller', '').strip() or 'Admin'

    if not name or names is None or song_ids is None:
        return jsonify({
            'status': 'error',
            'message': 'name, content und ids sind Pflichtangaben.',
        }), 400
    if not names or any(not item for item in names) or any(not item for item in song_ids):
        return jsonify({'status': 'error', 'message': 'Songnamen und Song-IDs dürfen nicht leer sein.'}), 400
    if len(names) != len(song_ids):
        return jsonify({'status': 'error', 'message': 'content und ids müssen gleich viele Einträge enthalten.'}), 400
    if images is None or images == ['']:
        images = [''] * len(names)
    if len(images) != len(names):
        return jsonify({'status': 'error', 'message': 'bilder muss gleich viele Einträge wie content enthalten.'}), 400

    playlist = find_playlist_content(name, playlist_id)
    if playlist is None:
        playlist = PlaylistContent(name=name, playlist_id=playlist_id)
        db.session.add(playlist)
    playlist.name = name
    if playlist_id is not None:
        playlist.playlist_id = playlist_id
    playlist.creator = creator
    playlist.song_names = json.dumps(names, ensure_ascii=False)
    playlist.song_ids = json.dumps(song_ids, ensure_ascii=False)
    playlist.image_links = json.dumps(images, ensure_ascii=False)
    playlist.updated_at = int(time.time())
    db.session.commit()
    return jsonify({'status': 'ok', 'playlist': serialize_playlist_content(playlist)}), 201


def format_server_playlist(playlist, number):
    """Formatiert einen Playlist-Listeneintrag für den Musik-Client."""
    return '•|•'.join((
        playlist.name,
        str(number),
        playlist.playlist_id or '',
        playlist.creator or 'Admin',
    ))


@app.route('/serverplaylists/list', methods=['GET'])
def list_server_playlists():
    """Liefert alle oder einen vom Client bestimmten Ausschnitt der Server-Playlists."""
    requested_number = request.args.get('num', '').strip().lower()
    query = PlaylistContent.query.order_by(
        PlaylistContent.created_at.asc(),
        PlaylistContent.id.asc(),
    )

    if requested_number == 'all':
        start_number = 1
        playlists = query.all()
    else:
        last_number_raw = request.args.get('last-num')
        try:
            amount = int(requested_number)
            last_number = int(last_number_raw) if last_number_raw is not None else -1
        except ValueError:
            amount = 0
            last_number = -1
        if amount < 1 or last_number < 0:
            return jsonify({
                'status': 'error',
                'message': 'num muss "all" oder eine positive Ganzzahl sein; last-num muss mindestens 0 sein.',
            }), 400
        start_number = last_number + 1
        playlists = query.offset(last_number).limit(amount).all()

    entries = [
        format_server_playlist(playlist, start_number + index)
        for index, playlist in enumerate(playlists)
    ]
    return '°|°'.join(entries), 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route('/serverplaylists/maxnum', methods=['GET'])
def get_server_playlists_maxnum():
    """Liefert die Anzahl aller in der Datenbank gespeicherten Playlists."""
    return str(PlaylistContent.query.count()), 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route('/playlistcontent/get/<path:playlist_name>', methods=['GET'])
def get_playlist_content(playlist_name):
    """Liefert die aktuell spielende oder eine namentlich beziehungsweise per ID gewählte Playlist."""
    playlist_id = request.args.get('id', '').strip() or None
    if playlist_name.lower() == 'playing':
        playing = db.session.get(PlayingPlaylist, 1)
        playlist = db.session.get(PlaylistContent, playing.playlist_content_id) if playing else None
    else:
        playlist = find_playlist_content(playlist_name, playlist_id)
    if playlist is None:
        return jsonify({'status': 'error', 'message': 'Playlist wurde nicht gefunden.'}), 404
    return format_playlist_content(playlist), 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route('/playlist/play/<path:playlist_name>', methods=['POST'])
def play_playlist(playlist_name):
    """Startet die angegebene gespeicherte Playlist über die Spotify-Playback-API."""
    playlist_id = request.args.get('id', '').strip() or None
    playlist = find_playlist_content(playlist_name, playlist_id)
    if playlist is None:
        return jsonify({'status': 'error', 'message': 'Playlist wurde nicht gefunden.'}), 404
    if not playlist.playlist_id:
        return jsonify({
            'status': 'error',
            'message': 'Zum Abspielen ist eine Spotify Playlist-ID erforderlich.',
        }), 400

    spotify_body = json.dumps({'context_uri': f'spotify:playlist:{playlist.playlist_id}'})
    response = execute_proxy_request(
        '/v1/me/player/play',
        method='PUT',
        request_body=spotify_body,
    )
    status_code = response[1] if isinstance(response, tuple) else response.status_code
    if 200 <= status_code < 300:
        playing = db.session.get(PlayingPlaylist, 1)
        if playing is None:
            playing = PlayingPlaylist(id=1, playlist_content_id=playlist.id)
            db.session.add(playing)
        playing.playlist_content_id = playlist.id
        playing.updated_at = int(time.time())
        db.session.commit()
    return response


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
    token_lifetime = client.token_lifetime_seconds or 86400
    token_entry = OAuthToken(
        access_token=access_token,
        client_id=client_id,
        scope=client.allowed_scopes,
        expires_at=int(time.time()) + token_lifetime
    )
    db.session.add(token_entry)
    db.session.commit()

    app.logger.info(f"OAuth Token erfolgreich generiert für Client '{client_id}'.")
    return jsonify({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": token_lifetime,
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

    try:
        token_lifetime_minutes = int(request.form.get('token_lifetime_minutes', '1440'))
    except ValueError:
        token_lifetime_minutes = 0
    if not 1 <= token_lifetime_minutes <= 525600:
        flash("Die Schlüssel-Gültigkeit muss zwischen 1 und 525600 Minuten liegen.", "error")
        return redirect('/dashboard')

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
        allowed_scopes=scopes_str,
        token_lifetime_seconds=token_lifetime_minutes * 60,
    )
    db.session.add(new_client)
    db.session.commit()

    app.logger.info(f"Neuer API-Client über Dashboard generiert. ID: {raw_client_id}, Rolle: {role}, Zuordnung: {name}")
    flash(f"Client erfolgreich erstellt!<br><b>Zuordnung:</b> {name}<br><b>Client-ID:</b> <code class='bg-gray-900 px-1 text-yellow-400 font-mono'>{raw_client_id}</code><br><b>Client-Secret:</b> <code class='bg-gray-900 px-1 text-emerald-400 font-mono'>{raw_client_secret}</code>", "success")
    return redirect('/dashboard')


@app.route('/dashboard/client/token-lifetime/<int:id>', methods=['POST'])
def update_client_token_lifetime(id):
    client = ClientCredentials.query.get_or_404(id)
    try:
        token_lifetime_minutes = int(request.form.get('token_lifetime_minutes', ''))
    except ValueError:
        token_lifetime_minutes = 0

    if not 1 <= token_lifetime_minutes <= 525600:
        flash("Die Schlüssel-Gültigkeit muss zwischen 1 und 525600 Minuten liegen.", "error")
        return redirect('/dashboard')

    client.token_lifetime_seconds = token_lifetime_minutes * 60
    db.session.commit()
    app.logger.info(
        f"Token-Gültigkeit für Client {client.client_id} auf "
        f"{token_lifetime_minutes} Minuten geändert."
    )
    flash(f"Schlüssel-Gültigkeit für {client.name} gespeichert.", "success")
    return redirect('/dashboard')


def route_example_url(rule):
    """Ersetzt dynamische URL-Parameter durch anklickbare Beispielwerte."""
    examples = {
        'id': '1',
        'username': 'username',
        'version': '1.0.0',
        'song_id': 'song-id',
    }

    def replace_parameter(match):
        parameter = match.group(1).split(':')[-1]
        return examples.get(parameter, 'value')

    return re.sub(r'<([^>]+)>', replace_parameter, rule)


PARAMETER_DETAILS = {
    'username': ('Benutzername, dem der Request zugeordnet wird.', 'Freier Text als URL-Pfadsegment'),
    'id': ('Eindeutige numerische ID des Datensatzes.', 'Positive Ganzzahl'),
    'version': ('Versionsnummer der App oder APK.', 'x.y.z, zum Beispiel 4.1.0'),
    'app-version': ('Version der meldenden App.', 'x.y.z, zum Beispiel 4.1.0'),
    'error': ('Fehlermeldung beziehungsweise Fehlerprotokoll.', 'URL-kodierter Freitext; Sonderzeichen und Zeilenumbrüche erlaubt'),
    'error_task': ('Task oder Arbeitsschritt, bei dem der Fehler auftrat.', 'URL-kodierter Freitext'),
    'date': ('Datum des Ereignisses.', 'dd.mm.yy oder dd.mm.yyyy'),
    'time': ('Uhrzeit des Ereignisses.', 'hh.mm'),
    'last-action': ('Zuletzt ausgeführte Aktion.', 'URL-kodierter Freitext'),
    'client_id': ('OAuth-Client-ID.', 'Textwert der registrierten Client-ID'),
    'client_secret': ('OAuth-Client-Secret.', 'Geheimer Textwert'),
    'grant_type': ('Verwendeter OAuth-Grant.', 'authorization_code oder client_credentials'),
    'redirect_uri': ('Zieladresse nach der Autorisierung.', 'Erlaubte absolute HTTPS-URL'),
    'scope': ('Angeforderte Berechtigungen.', 'Leerzeichengetrennte Scope-Liste'),
    'state': ('Vom Client gesetzter OAuth-Statuswert.', 'Beliebiger URL-kodierter Text'),
    'code': ('Kurzlebiger OAuth-Autorisierungscode.', 'Vom /authorize-Endpunkt ausgegebener Textwert'),
    'song_id': ('Spotify-ID des Songs.', 'Spotify Track-ID als Text'),
    'value': ('Wert für die gewählte Aktion.', 'Vom Endpunkt abhängiger Textwert'),
    'format': ('Erzwingt das Ausgabeformat der Routenliste.', 'html oder text'),
    'token_lifetime_minutes': ('Gültigkeitsdauer eines Access-Tokens.', 'Ganzzahl zwischen 1 und 525600'),
    'apk_file': ('APK-Datei, die hochgeladen wird.', 'Multipart-Datei mit Endung .apk'),
    'lat': ('Geographischer Breitengrad der Position.', 'Dezimalgrad, zum Beispiel 52.520008'),
    'lon': ('Geographischer Längengrad der Position.', 'Dezimalgrad, zum Beispiel 13.404954'),
    'maps_url': ('Link zur Position in einem Kartendienst.', 'Vollständige URL, URL-kodiert'),
    'admin': ('Name des Administrators.', 'In ADMIN_USERS eingetragener Benutzername'),
    'passwd': ('Passwort für den administrativen Request.', 'Textwert des konfigurierten Admin-Passworts'),
    'core_data': ('Vollständiger Core-Data-Textblock.', 'Mehrzeiliger UTF-8-Text im dokumentierten Core-Data-Format'),
    'name': ('Name der zu speichernden Playlist.', 'Freier URL-kodierter Text'),
    'content': ('Namen der Songs in ihrer Playlist-Reihenfolge.', 'Durch Kommas getrennte Songnamen'),
    'ids': ('Spotify-IDs der Songs in derselben Reihenfolge.', 'Durch Kommas getrennte Spotify Track-IDs'),
    'bilder': ('Bildlinks passend zu den Songs.', 'Durch Kommas getrennte HTTPS-Links; einzelne Einträge dürfen leer sein'),
    'pl-id': ('Spotify-ID der Playlist.', 'Spotify Playlist-ID als Text'),
    'ersteller': ('Name des Erstellers der Playlist.', 'Freier URL-kodierter Text; Standardwert Admin'),
    'num': ('Anzahl der auszugebenden Playlists oder Auswahl aller Playlists.', 'all oder positive Ganzzahl'),
    'last-num': ('Anzahl der bereits vom Client empfangenen Playlists.', 'Ganzzahl ab 0; bei num=all nicht erforderlich'),
    'kind': ('Art des Chat-Uploads.', 'bild oder datei'),
    'upload': ('Datei oder Bild für eine spätere Chatnachricht.', 'Multipart-Datei'),
    'sender': ('Absender einer Chatnachricht.', 'Benutzername als URL-Pfadsegment'),
    'recipient': ('Empfänger einer direkten Chatnachricht.', 'Benutzername als URL-Pfadsegment'),
    'message_type': ('Typ des Nachrichteninhalts.', 'picture, text, text-mit-link, link, datei, text-mit-bild oder text-mit-datei'),
    'inhalt': ('Text- oder Linkinhalt der Chatnachricht.', 'URL-kodierter Freitext'),
    'datei-upload': ('Zuordnung einer zuvor hochgeladenen Datei.', 'upload-response-id von /chat/upload/datei'),
    'bild-upload': ('Zuordnung eines zuvor hochgeladenen Bildes.', 'upload-response-id von /chat/upload/bild'),
    'mitglieder': ('Anfängliche Mitglieder eines Gruppenchats.', 'Durch Kommas getrennte Benutzernamen'),
    'action': ('Auszuführende Änderung an der Gruppenmitgliedschaft.', 'add oder remove'),
    'limit': ('Maximale Anzahl zurückgegebener Chatnachrichten.', 'Ganzzahl zwischen 1 und 1000; Standard 100'),
    'offset': ('Anzahl der zu überspringenden Chatnachrichten.', 'Ganzzahl ab 0; Standard 0'),
    'antwort-auf': ('Nachrichten-ID, auf die geantwortet wird.', 'Positive Ganzzahl aus demselben Chat'),
    'inline': ('Steuert die Browserdarstellung eines Chat-Anhangs.', '1 für Inline-Anzeige; sonst Download'),
}

ROUTE_PARAMETER_OVERRIDES = {
    'upload_apk_online': [('form', 'version', True), ('file', 'apk_file', True)],
    'list_routes': [('query', 'format', False)],
    'authorize': [('query', 'scope', False), ('query', 'state', False)],
    'spotify_callback': [('query', 'error', False)],
    'upload_coredata': [('body', 'core_data', True)],
    'save_playlist_content': [
        ('query', 'name', True),
        ('query', 'content', True),
        ('query', 'ids', True),
        ('query', 'bilder', False),
        ('query', 'pl-id', False),
        ('query', 'ersteller', False),
    ],
    'get_playlist_content': [('query', 'id', False)],
    'play_playlist': [('query', 'id', False)],
    'list_server_playlists': [('query', 'num', True), ('query', 'last-num', False)],
    'chat.upload_chat_media': [('file', 'upload', True), ('form', 'sender', False)],
    'chat.download_chat_media': [('query', 'inline', False)],
    'chat.send_direct_message': [
        ('query', 'inhalt', False),
        ('query', 'datei-upload', False),
        ('query', 'bild-upload', False),
        ('query', 'antwort-auf', False),
    ],
    'chat.send_self_message': [
        ('query', 'inhalt', False),
        ('query', 'datei-upload', False),
        ('query', 'bild-upload', False),
        ('query', 'antwort-auf', False),
    ],
    'chat.create_group': [
        ('query', 'name', True),
        ('query', 'ersteller', True),
        ('query', 'mitglieder', False),
    ],
    'chat.change_group_members': [('query', 'action', True), ('query', 'username', True)],
    'chat.send_group_message': [
        ('query', 'inhalt', False),
        ('query', 'datei-upload', False),
        ('query', 'bild-upload', False),
        ('query', 'antwort-auf', False),
    ],
    'chat.direct_history': [('query', 'limit', False), ('query', 'offset', False)],
    'chat.group_history': [('query', 'limit', False), ('query', 'offset', False)],
    'chat.send_chatgpt_message': [('query', 'inhalt', True)],
    'chat.get_chatgpt_history': [('query', 'limit', False), ('query', 'offset', False)],
}


def extract_request_parameters(view_function, visited=None):
    """Liest Request-Parameter auch aus aufgerufenen lokalen Hilfsfunktionen."""
    parameters = []
    visited = visited or set()
    if view_function in visited:
        return parameters
    visited.add(view_function)
    try:
        tree = ast.parse(inspect.getsource(view_function))
    except (OSError, TypeError, IndentationError, SyntaxError):
        return parameters

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Attribute) or not isinstance(owner.value, ast.Name):
            continue
        if owner.value.id != 'request' or node.func.attr != 'get' or not node.args:
            continue
        if owner.attr not in {'args', 'form', 'files'}:
            continue
        parameter_name = None
        if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            parameter_name = node.args[0].value
        elif isinstance(node.args[0], ast.Name):
            configured_name = view_function.__globals__.get(node.args[0].id)
            if isinstance(configured_name, str):
                parameter_name = configured_name
        if parameter_name is None:
            continue
        parameter_type = {'args': 'query', 'form': 'form', 'files': 'file'}[owner.attr]
        required = len(node.args) == 1 or (
            len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value is None
        )
        parameters.append((parameter_type, parameter_name, required))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        helper = view_function.__globals__.get(node.func.id)
        if (
            inspect.isfunction(helper)
            and getattr(helper, '__module__', None) == __name__
            and helper not in visited
        ):
            parameters.extend(extract_request_parameters(helper, visited))
    return parameters


def describe_parameter(parameter_type, name, required, converter=None):
    purpose, value_format = PARAMETER_DETAILS.get(
        name,
        (f"Wert für den Parameter {name}.", 'Textwert; Details richten sich nach dem Endpunkt'),
    )
    if parameter_type == 'path' and converter:
        value_format = {
            'int': 'Ganzzahl als URL-Pfadsegment',
            'float': 'Dezimalzahl als URL-Pfadsegment',
            'path': 'URL-Pfad, darf Schrägstriche enthalten',
            'string': value_format,
        }.get(converter, value_format)
    return {
        'name': name,
        'location': parameter_type,
        'required': required,
        'purpose': purpose,
        'format': value_format,
    }


def collect_routes():
    """Erfasst bei jedem Aufruf alle aktuell registrierten Anwendungsrouten."""
    routes = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda item: item.rule):
        if rule.endpoint == 'static':
            continue
        methods = sorted(rule.methods - {'HEAD', 'OPTIONS'})
        view_function = app.view_functions.get(rule.endpoint)
        try:
            source = inspect.getsource(view_function) if view_function else ''
        except (OSError, TypeError):
            source = ''
        description = (
            view_function.__doc__.strip().splitlines()[0]
            if view_function and view_function.__doc__
            else f"Endpunkt {rule.endpoint.replace('_', ' ')}"
        )
        parameters = []
        for converter, name in re.findall(r'<(?:(\w+):)?(\w+)>', rule.rule):
            parameters.append(describe_parameter('path', name, True, converter or 'string'))
        detected_parameters = list(ROUTE_PARAMETER_OVERRIDES.get(rule.endpoint, []))
        detected_parameters.extend(extract_request_parameters(view_function) if view_function else [])
        known_parameters = {(item['location'], item['name']) for item in parameters}
        for parameter_type, name, required in detected_parameters:
            if (parameter_type, name) not in known_parameters:
                parameters.append(describe_parameter(parameter_type, name, required))
                known_parameters.add((parameter_type, name))
        if rule.endpoint in {'get_playlist_content', 'play_playlist'}:
            for parameter in parameters:
                if parameter['location'] == 'query' and parameter['name'] == 'id':
                    parameter['purpose'] = 'Optionale Spotify-ID zur eindeutigen Auswahl der Playlist.'
                    parameter['format'] = 'Spotify Playlist-ID als Text'
        query_parameters = [item for item in parameters if item['location'] == 'query']
        query_example = '&'.join(
            f"{item['name']}=<{item['format']}>" for item in query_parameters
        )
        authentication = (
            'execute_proxy_request' in source
            or 'verify_gateway_token' in source
            or rule.rule in {'/authorize', '/token'}
        )
        routes.append({
            'rule': rule.rule,
            'url': route_example_url(rule.rule),
            'methods': methods,
            'description': description,
            'parameters': parameters,
            'query_parameters': query_parameters,
            'query_example': query_example,
            'authentication': authentication,
            'browser_compatible': (
                'GET' in methods
                and not authentication
                and not any(
                    item['required'] and item['location'] in {'path', 'query'}
                    for item in parameters
                )
            ),
        })
    return routes


@app.route('/routes', methods=['GET'])
def list_routes():
    """Zeigt alle aktuell in der Flask-Anwendung registrierten Routen an."""
    routes = collect_routes()
    requested_format = request.args.get('format')
    wants_html = requested_format == 'html' or (
        requested_format != 'text'
        and request.accept_mimetypes.best_match(['text/html', 'text/plain']) == 'text/html'
    )
    if wants_html:
        return render_template('routes.html', routes=routes)

    return Response(
        ';'.join(route['rule'] for route in routes),
        content_type='text/plain; charset=utf-8',
    )


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
