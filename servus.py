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
from chat import register_chat_routes

UPDATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "updates")
os.makedirs(UPDATES_DIR, exist_ok=True)


app = Flask(__name__)
register_chat_routes(app)
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
    time = db.Column(db.String(8), nullable=False)
    last_action = db.Column(db.Text, nullable=False)

class MassErrorReportError(db.Model):
    __tablename__ = 'mass_errorreport_errors'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), nullable=False)
    app_version = db.Column(db.String(50), nullable=False)
    error_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(255), nullable=False)
    text = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(255), nullable=False)
    notification_group = db.Column(db.String(255), nullable=False)
    png_path = db.Column(db.String(1000), nullable=True)
    created_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))

class NotificationDelivery(db.Model):
    __tablename__ = 'notification_deliveries'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    notification_id = db.Column(db.Integer, db.ForeignKey('notifications.id', ondelete='CASCADE'), nullable=False)
    client_id = db.Column(db.String(80), nullable=False)
    delivered_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))
    __table_args__ = (db.UniqueConstraint('notification_id', 'client_id', name='uq_notification_client'),)

class PlaylistContent(db.Model):
    __tablename__ = 'playlist_contents'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    playlist_name = db.Column(db.String(255), nullable=False)
    playlist_id = db.Column(db.String(255), nullable=True)
    song_names = db.Column(db.Text, nullable=False)
    song_ids = db.Column(db.Text, nullable=False)
    image_links = db.Column(db.Text, nullable=False)
    creator = db.Column(db.String(255), nullable=False, default='Admin')
    created_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))
    updated_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))

class PlayingPlaylist(db.Model):
    __tablename__ = 'playing_playlist'
    id = db.Column(db.Integer, primary_key=True)
    playlist_content_id = db.Column(db.Integer, db.ForeignKey('playlist_contents.id'), nullable=False)

# NOTE: remainder intentionally unchanged in repository. This connector requires whole-file replacement, so this update is not suitable here.