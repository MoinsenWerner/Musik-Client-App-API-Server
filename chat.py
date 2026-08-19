"""Standalone chat API and Flask blueprint for the Musik Client server."""

import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

import requests
from flask import Blueprint, Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHAT_DATABASE_FILE = os.environ.get('CHAT_DATABASE_FILE', os.path.join(BASE_DIR, 'chat.db'))
CHAT_UPLOAD_FOLDER = os.environ.get('CHAT_UPLOAD_FOLDER', os.path.join(BASE_DIR, 'chat_uploads'))
CHAT_WRITE_LOCK = threading.RLock()
MESSAGE_TYPES = {
    'picture',
    'text',
    'text-mit-link',
    'link',
    'datei',
    'text-mit-bild',
    'text-mit-datei',
}
IMAGE_EXTENSIONS = {'.avif', '.gif', '.jpeg', '.jpg', '.png', '.webp'}
OPENAI_API_URL = 'https://api.openai.com/v1/responses'
OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-5')
OPENAI_HISTORY_LIMIT = 40

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('bild', 'datei')),
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    mime_type TEXT,
    size INTEGER NOT NULL,
    uploaded_by TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    creator TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_group_members (
    group_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    joined_at INTEGER NOT NULL,
    PRIMARY KEY (group_id, username),
    FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL,
    recipient TEXT,
    group_id INTEGER,
    message_type TEXT NOT NULL,
    content TEXT,
    file_upload_id INTEGER,
    image_upload_id INTEGER,
    reply_to_message_id INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (file_upload_id) REFERENCES chat_uploads(id),
    FOREIGN KEY (image_upload_id) REFERENCES chat_uploads(id),
    FOREIGN KEY (reply_to_message_id) REFERENCES chat_messages(id),
    CHECK ((recipient IS NOT NULL AND group_id IS NULL) OR
           (recipient IS NULL AND group_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_chat_direct
    ON chat_messages(sender, recipient, created_at, id);
CREATE INDEX IF NOT EXISTS idx_chat_group
    ON chat_messages(group_id, created_at, id);
CREATE TABLE IF NOT EXISTS chatgpt_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    openai_response_id TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chatgpt_user
    ON chatgpt_messages(username, created_at, id);
"""


def initialize_chat_storage():
    """Create the independent chat database and upload directory."""
    os.makedirs(os.path.dirname(os.path.abspath(CHAT_DATABASE_FILE)), exist_ok=True)
    os.makedirs(CHAT_UPLOAD_FOLDER, exist_ok=True)
    with sqlite3.connect(CHAT_DATABASE_FILE) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        connection.executescript(SCHEMA)
        message_columns = {
            column[1] for column in connection.execute('PRAGMA table_info(chat_messages)')
        }
        if 'reply_to_message_id' not in message_columns:
            connection.execute(
                'ALTER TABLE chat_messages ADD COLUMN reply_to_message_id INTEGER'
            )


@contextmanager
def chat_connection():
    connection = sqlite3.connect(CHAT_DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    try:
        yield connection
    finally:
        connection.close()


def clean_identity(value, field_name):
    value = value.strip()
    if not value or len(value) > 200:
        raise ValueError(f'{field_name} muss zwischen 1 und 200 Zeichen enthalten.')
    return value


def get_upload(connection, response_id, expected_kind):
    if not response_id:
        return None
    upload = connection.execute(
        'SELECT * FROM chat_uploads WHERE response_id = ? AND kind = ?',
        (response_id, expected_kind),
    ).fetchone()
    if upload is None:
        raise ValueError(f'Ungültige oder nicht passende {expected_kind}-upload-response-id.')
    return upload


def validate_message(message_type, content, file_upload, image_upload):
    if message_type not in MESSAGE_TYPES:
        raise ValueError('Unbekannter Nachrichtentyp.')
    requires_content = message_type in {'text', 'text-mit-link', 'link', 'text-mit-bild', 'text-mit-datei'}
    requires_file = message_type in {'datei', 'text-mit-datei'}
    requires_image = message_type in {'picture', 'text-mit-bild'}
    if requires_content and not content:
        raise ValueError('Für diesen Nachrichtentyp ist inhalt erforderlich.')
    if requires_file and file_upload is None:
        raise ValueError('Für diesen Nachrichtentyp ist datei-upload erforderlich.')
    if requires_image and image_upload is None:
        raise ValueError('Für diesen Nachrichtentyp ist bild-upload erforderlich.')
    if not requires_file and file_upload is not None:
        raise ValueError('datei-upload passt nicht zum gewählten Nachrichtentyp.')
    if not requires_image and image_upload is not None:
        raise ValueError('bild-upload passt nicht zum gewählten Nachrichtentyp.')


def insert_message(sender, recipient, group_id, message_type):
    content = request.args.get('inhalt', '').strip() or None
    file_response_id = request.args.get('datei-upload', '').strip() or None
    image_response_id = request.args.get('bild-upload', '').strip() or None
    reply_to_raw = request.args.get('antwort-auf', '').strip() or None
    try:
        sender = clean_identity(sender, 'sender')
        recipient = clean_identity(recipient, 'empfänger') if recipient is not None else None
        with CHAT_WRITE_LOCK, chat_connection() as connection:
            file_upload = get_upload(connection, file_response_id, 'datei')
            image_upload = get_upload(connection, image_response_id, 'bild')
            validate_message(message_type, content, file_upload, image_upload)
            try:
                reply_to_message_id = int(reply_to_raw) if reply_to_raw else None
            except ValueError as error:
                raise ValueError('antwort-auf muss eine Nachrichten-ID sein.') from error
            if reply_to_message_id is not None:
                replied_message = connection.execute(
                    'SELECT sender, recipient, group_id FROM chat_messages WHERE id = ?',
                    (reply_to_message_id,),
                ).fetchone()
                if replied_message is None:
                    raise ValueError('Die beantwortete Nachricht wurde nicht gefunden.')
                same_group = group_id is not None and replied_message['group_id'] == group_id
                same_direct_chat = (
                    group_id is None
                    and replied_message['group_id'] is None
                    and {sender, recipient} == {
                        replied_message['sender'], replied_message['recipient'],
                    }
                )
                if not same_group and not same_direct_chat:
                    raise ValueError('Die beantwortete Nachricht gehört nicht zu diesem Chat.')
            cursor = connection.execute(
                """INSERT INTO chat_messages
                   (sender, recipient, group_id, message_type, content,
                    file_upload_id, image_upload_id, reply_to_message_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sender,
                    recipient,
                    group_id,
                    message_type,
                    content,
                    file_upload['id'] if file_upload else None,
                    image_upload['id'] if image_upload else None,
                    reply_to_message_id,
                    int(time.time()),
                ),
            )
            connection.commit()
            message_id = cursor.lastrowid
    except (ValueError, sqlite3.IntegrityError) as error:
        return jsonify({'status': 'error', 'message': str(error)}), 400
    return jsonify({'status': 'ok', 'message_id': message_id}), 201


def serialize_message(row):
    return {
        'id': row['id'],
        'sender': row['sender'],
        'empfaenger': row['recipient'],
        'gruppen_id': row['group_id'],
        'typ': row['message_type'],
        'inhalt': row['content'],
        'datei_upload': row['file_response_id'],
        'bild_upload': row['image_response_id'],
        'datei_name': row['file_original_name'],
        'datei_mime_type': row['file_mime_type'],
        'bild_name': row['image_original_name'],
        'bild_mime_type': row['image_mime_type'],
        'antwort_auf': row['reply_to_message_id'],
        'antwort_sender': row['reply_sender'],
        'antwort_inhalt': row['reply_content'],
        'created_at': row['created_at'],
    }


def message_select(where_clause, parameters, limit, offset):
    query = f"""SELECT m.*, file.response_id AS file_response_id,
                       file.original_name AS file_original_name,
                       file.mime_type AS file_mime_type,
                       image.response_id AS image_response_id,
                       image.original_name AS image_original_name,
                       image.mime_type AS image_mime_type,
                       reply.sender AS reply_sender,
                       reply.content AS reply_content
                FROM chat_messages AS m
                LEFT JOIN chat_uploads AS file ON file.id = m.file_upload_id
                LEFT JOIN chat_uploads AS image ON image.id = m.image_upload_id
                LEFT JOIN chat_messages AS reply ON reply.id = m.reply_to_message_id
                WHERE {where_clause}
                ORDER BY m.created_at ASC, m.id ASC
                LIMIT ? OFFSET ?"""
    with chat_connection() as connection:
        return connection.execute(query, (*parameters, limit, offset)).fetchall()


def pagination_values():
    try:
        limit = int(request.args.get('limit', '100'))
        offset = int(request.args.get('offset', '0'))
    except ValueError as error:
        raise ValueError('limit und offset müssen Ganzzahlen sein.') from error
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError('limit muss 1 bis 1000 und offset mindestens 0 sein.')
    return limit, offset


def extract_openai_text(response_data):
    """Extract text content from a raw OpenAI Responses API response."""
    texts = []
    for output in response_data.get('output', []):
        if output.get('type') != 'message':
            continue
        for content in output.get('content', []):
            if content.get('type') == 'output_text' and content.get('text'):
                texts.append(content['text'])
    return '\n'.join(texts).strip()


def chatgpt_history_rows(username, limit=None, offset=0):
    parameters = [username]
    pagination = ''
    if limit is not None:
        pagination = ' LIMIT ? OFFSET ?'
        parameters.extend((limit, offset))
    with chat_connection() as connection:
        return connection.execute(
            """SELECT id, username, role, content, openai_response_id, created_at
               FROM chatgpt_messages WHERE username = ?
               ORDER BY created_at ASC, id ASC""" + pagination,
            parameters,
        ).fetchall()


def create_chat_blueprint():
    blueprint = Blueprint('chat', __name__)

    @blueprint.get('/webchat')
    def webchat():
        """Render the browser chat client with a client-credential login overlay."""
        return render_template('webchat.html')

    @blueprint.post('/chat/upload/<kind>')
    def upload_chat_media(kind):
        """Upload a file/image and return its response ID for a later message."""
        if kind not in {'bild', 'datei'}:
            return jsonify({'status': 'error', 'message': 'kind muss bild oder datei sein.'}), 400
        uploaded_file = request.files.get('upload')
        if uploaded_file is None or not uploaded_file.filename:
            return jsonify({'status': 'error', 'message': 'Multipart-Feld upload ist erforderlich.'}), 400
        original_name = secure_filename(uploaded_file.filename)
        if not original_name:
            return jsonify({'status': 'error', 'message': 'Ungültiger Dateiname.'}), 400
        extension = os.path.splitext(original_name)[1].lower()
        if kind == 'bild' and extension not in IMAGE_EXTENSIONS:
            return jsonify({'status': 'error', 'message': 'Nicht unterstütztes Bildformat.'}), 400
        response_id = secrets.token_urlsafe(24)
        stored_name = f'{response_id}{extension}'
        target_path = os.path.join(CHAT_UPLOAD_FOLDER, stored_name)
        size = 0
        try:
            with open(target_path, 'wb') as target:
                while chunk := uploaded_file.stream.read(1024 * 1024):
                    target.write(chunk)
                    size += len(chunk)
            if size == 0:
                raise ValueError('Die hochgeladene Datei ist leer.')
            with CHAT_WRITE_LOCK, chat_connection() as connection:
                connection.execute(
                    """INSERT INTO chat_uploads
                       (response_id, kind, original_name, stored_name, mime_type,
                        size, uploaded_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        response_id,
                        kind,
                        original_name,
                        stored_name,
                        uploaded_file.mimetype,
                        size,
                        request.form.get('sender', '').strip() or None,
                        int(time.time()),
                    ),
                )
                connection.commit()
        except (OSError, ValueError, sqlite3.Error) as error:
            if os.path.exists(target_path):
                os.remove(target_path)
            return jsonify({'status': 'error', 'message': str(error)}), 400
        return jsonify({'status': 'ok', 'upload_response_id': response_id, 'typ': kind}), 201

    @blueprint.get('/chat/upload/<response_id>')
    def download_chat_media(response_id):
        """Download a previously uploaded chat attachment by response ID."""
        with chat_connection() as connection:
            upload = connection.execute(
                'SELECT * FROM chat_uploads WHERE response_id = ?', (response_id,),
            ).fetchone()
        if upload is None:
            return jsonify({'status': 'error', 'message': 'Upload nicht gefunden.'}), 404
        return send_from_directory(
            CHAT_UPLOAD_FOLDER,
            upload['stored_name'],
            as_attachment=request.args.get('inline') != '1',
            download_name=upload['original_name'],
        )

    @blueprint.post('/chat/<sender>/<recipient>/<message_type>')
    def send_direct_message(sender, recipient, message_type):
        """Store a direct message; sender and recipient may be identical."""
        return insert_message(sender, recipient, None, message_type)

    @blueprint.post('/chat/self/<username>/<message_type>')
    def send_self_message(username, message_type):
        """Store a message in a user's explicit self-chat."""
        return insert_message(username, username, None, message_type)

    @blueprint.post('/chat/group/create')
    def create_group():
        """Create a group chat and its initial member list."""
        try:
            name = clean_identity(request.args.get('name', ''), 'name')
            creator = clean_identity(request.args.get('ersteller', ''), 'ersteller')
            members = {
                clean_identity(member, 'mitglied')
                for member in request.args.get('mitglieder', '').split(',')
                if member.strip()
            }
            members.add(creator)
            with CHAT_WRITE_LOCK, chat_connection() as connection:
                cursor = connection.execute(
                    'INSERT INTO chat_groups (name, creator, created_at) VALUES (?, ?, ?)',
                    (name, creator, int(time.time())),
                )
                group_id = cursor.lastrowid
                connection.executemany(
                    'INSERT INTO chat_group_members (group_id, username, joined_at) VALUES (?, ?, ?)',
                    [(group_id, member, int(time.time())) for member in sorted(members)],
                )
                connection.commit()
        except (ValueError, sqlite3.Error) as error:
            return jsonify({'status': 'error', 'message': str(error)}), 400
        return jsonify({'status': 'ok', 'gruppen_id': group_id, 'mitglieder': sorted(members)}), 201

    @blueprint.post('/chat/group/<int:group_id>/members')
    def change_group_members(group_id):
        """Add or remove one group member using action=add/remove."""
        action = request.args.get('action', '').lower()
        try:
            username = clean_identity(request.args.get('username', ''), 'username')
            with CHAT_WRITE_LOCK, chat_connection() as connection:
                group = connection.execute('SELECT id FROM chat_groups WHERE id = ?', (group_id,)).fetchone()
                if group is None:
                    return jsonify({'status': 'error', 'message': 'Gruppe nicht gefunden.'}), 404
                if action == 'add':
                    connection.execute(
                        'INSERT OR IGNORE INTO chat_group_members (group_id, username, joined_at) VALUES (?, ?, ?)',
                        (group_id, username, int(time.time())),
                    )
                elif action == 'remove':
                    connection.execute(
                        'DELETE FROM chat_group_members WHERE group_id = ? AND username = ?',
                        (group_id, username),
                    )
                else:
                    raise ValueError('action muss add oder remove sein.')
                connection.commit()
        except (ValueError, sqlite3.Error) as error:
            return jsonify({'status': 'error', 'message': str(error)}), 400
        return jsonify({'status': 'ok'}), 200

    @blueprint.post('/chat/group/<int:group_id>/<sender>/<message_type>')
    def send_group_message(group_id, sender, message_type):
        """Store a message after checking that the sender belongs to the group."""
        with chat_connection() as connection:
            member = connection.execute(
                'SELECT 1 FROM chat_group_members WHERE group_id = ? AND username = ?',
                (group_id, sender),
            ).fetchone()
        if member is None:
            return jsonify({'status': 'error', 'message': 'Sender ist kein Mitglied dieser Gruppe.'}), 403
        return insert_message(sender, None, group_id, message_type)

    @blueprint.get('/chat/history/<user_one>/<user_two>')
    def direct_history(user_one, user_two):
        """Return a chronological direct/self-chat history."""
        try:
            limit, offset = pagination_values()
        except ValueError as error:
            return jsonify({'status': 'error', 'message': str(error)}), 400
        rows = message_select(
            'm.group_id IS NULL AND ((m.sender = ? AND m.recipient = ?) OR (m.sender = ? AND m.recipient = ?))',
            (user_one, user_two, user_two, user_one),
            limit,
            offset,
        )
        return jsonify({'status': 'ok', 'nachrichten': [serialize_message(row) for row in rows]})

    @blueprint.get('/chat/group/<int:group_id>/history')
    def group_history(group_id):
        """Return a chronological group-chat history."""
        try:
            limit, offset = pagination_values()
        except ValueError as error:
            return jsonify({'status': 'error', 'message': str(error)}), 400
        rows = message_select('m.group_id = ?', (group_id,), limit, offset)
        return jsonify({'status': 'ok', 'nachrichten': [serialize_message(row) for row in rows]})

    def media_response(where_clause, parameters):
        query = f"""SELECT DISTINCT u.response_id, u.kind, u.original_name,
                            u.mime_type, u.size, u.uploaded_by, u.created_at
                     FROM chat_messages AS m
                     JOIN chat_uploads AS u
                       ON u.id = m.file_upload_id OR u.id = m.image_upload_id
                     WHERE {where_clause}
                     ORDER BY u.created_at ASC, u.id ASC"""
        with chat_connection() as connection:
            uploads = [dict(row) for row in connection.execute(query, parameters).fetchall()]
        for upload in uploads:
            upload['download_url'] = f"/chat/upload/{upload['response_id']}"
        return jsonify({'status': 'ok', 'medien': uploads})

    @blueprint.get('/chat/media/<user_one>/<user_two>')
    def direct_media(user_one, user_two):
        """Return every attachment assigned to a direct/self chat."""
        return media_response(
            'm.group_id IS NULL AND ((m.sender = ? AND m.recipient = ?) OR (m.sender = ? AND m.recipient = ?))',
            (user_one, user_two, user_two, user_one),
        )

    @blueprint.get('/chat/group/<int:group_id>/media')
    def group_media(group_id):
        """Return every attachment assigned to a group chat."""
        return media_response('m.group_id = ?', (group_id,))

    @blueprint.post('/chat/gpt/<username>')
    def send_chatgpt_message(username):
        """Send a prompt to OpenAI while preserving per-user conversation history."""
        api_key = os.environ.get('OPENAI_API_KEY', '').strip()
        prompt = request.args.get('inhalt', '').strip()
        try:
            username = clean_identity(username, 'username')
        except ValueError as error:
            return jsonify({'status': 'error', 'message': str(error)}), 400
        if not prompt:
            return jsonify({'status': 'error', 'message': 'inhalt ist erforderlich.'}), 400
        if not api_key:
            return jsonify({
                'status': 'error',
                'error': 'openai_not_configured',
                'message': 'OPENAI_API_KEY ist auf dem Server nicht gesetzt.',
            }), 503

        with CHAT_WRITE_LOCK:
            history = chatgpt_history_rows(username)
            input_messages = [
                {'role': row['role'], 'content': row['content']}
                for row in history[-OPENAI_HISTORY_LIMIT:]
            ]
            input_messages.append({'role': 'user', 'content': prompt})
            try:
                openai_response = requests.post(
                    OPENAI_API_URL,
                    headers={
                        'Authorization': f'Bearer {api_key}',
                        'Content-Type': 'application/json',
                    },
                    json={
                        'model': OPENAI_MODEL,
                        'input': input_messages,
                    },
                    timeout=60,
                )
            except requests.RequestException as error:
                return jsonify({'status': 'error', 'error': 'openai_unavailable', 'message': str(error)}), 502
            if not openai_response.ok:
                return jsonify({
                    'status': 'error',
                    'error': 'openai_error',
                    'upstream_status': openai_response.status_code,
                    'message': openai_response.text[:2000],
                }), 502
            response_data = openai_response.json()
            answer = extract_openai_text(response_data)
            if not answer:
                return jsonify({
                    'status': 'error',
                    'error': 'empty_openai_response',
                    'message': 'OpenAI hat keine Textantwort geliefert.',
                }), 502
            created_at = int(time.time())
            with chat_connection() as connection:
                connection.execute(
                    """INSERT INTO chatgpt_messages
                       (username, role, content, openai_response_id, created_at)
                       VALUES (?, 'user', ?, NULL, ?)""",
                    (username, prompt, created_at),
                )
                cursor = connection.execute(
                    """INSERT INTO chatgpt_messages
                       (username, role, content, openai_response_id, created_at)
                       VALUES (?, 'assistant', ?, ?, ?)""",
                    (username, answer, response_data.get('id'), created_at),
                )
                connection.commit()
        return jsonify({
            'status': 'ok',
            'message_id': cursor.lastrowid,
            'antwort': answer,
            'model': response_data.get('model', OPENAI_MODEL),
        }), 201

    @blueprint.get('/chat/gpt/<username>/history')
    def get_chatgpt_history(username):
        """Return the persistent ChatGPT conversation history for one user."""
        try:
            username = clean_identity(username, 'username')
            limit, offset = pagination_values()
        except ValueError as error:
            return jsonify({'status': 'error', 'message': str(error)}), 400
        rows = chatgpt_history_rows(username, limit, offset)
        return jsonify({'status': 'ok', 'nachrichten': [dict(row) for row in rows]})

    @blueprint.delete('/chat/gpt/<username>/history')
    def delete_chatgpt_history(username):
        """Delete the remembered ChatGPT conversation for one user."""
        try:
            username = clean_identity(username, 'username')
        except ValueError as error:
            return jsonify({'status': 'error', 'message': str(error)}), 400
        with CHAT_WRITE_LOCK, chat_connection() as connection:
            cursor = connection.execute(
                'DELETE FROM chatgpt_messages WHERE username = ?', (username,),
            )
            connection.commit()
        return jsonify({'status': 'ok', 'geloeschte_nachrichten': cursor.rowcount})

    return blueprint


def register_chat_routes(app):
    """Initialize storage and attach all chat routes to an existing Flask app."""
    initialize_chat_storage()
    app.register_blueprint(create_chat_blueprint())


def create_standalone_app():
    """Create an app exposing only the chat API for standalone operation."""
    standalone_app = Flask(__name__)
    register_chat_routes(standalone_app)

    @standalone_app.post('/token')
    def standalone_token_unavailable():
        return jsonify({
            'error': 'standalone_mode',
            'message': 'Die Client-Anmeldung ist nur in der vollständigen servus.py-App verfügbar.',
        }), 503

    return standalone_app


if __name__ == '__main__':
    create_standalone_app().run(host='0.0.0.0', port=2050, debug=True)
