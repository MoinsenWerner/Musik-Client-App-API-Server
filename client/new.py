import os
import time
import requests
import urllib.parse
from flask import Flask, request, jsonify, render_template_string, redirect, flash
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = "client_secret_session_key_nach_bedarf_aendern"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(BASE_DIR, "client_config.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class ClientConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    api_base_link = db.Column(db.String(255), default="https://api.extrahelden.de")
    client_id = db.Column(db.String(100), nullable=True)
    client_secret = db.Column(db.String(100), nullable=True)
    scopes = db.Column(db.String(255), default="client hcb-client")
    access_token = db.Column(db.Text, nullable=True)
    expires_at = db.Column(db.Integer, default=0)
    auth_status = db.Column(db.String(50), default="Nicht autorisiert")
    last_error = db.Column(db.Text, nullable=True)

def get_cfg():
    cfg = ClientConfig.query.first()
    if not cfg:
        cfg = ClientConfig()
        db.session.add(cfg)
        db.session.commit()
    return cfg

def get_valid_token(cfg):
    if not cfg.access_token:
        return None
    if cfg.expires_at > int(time.time()):
        return cfg.access_token
    return None

# ==========================================
# DASHBOARD INTERFACE WITH DIRECT JAVASCRIPT CALLS
# ==========================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>HBC API Controller</title>
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
</head>
<body class="bg-gray-950 text-gray-100 font-sans p-8">
    <div class="max-w-4xl mx-auto space-y-8">
        <header class="border-b border-gray-800 pb-4">
            <h1 class="text-2xl font-bold text-white">HBC API Controller Dashboard</h1>
            <p class="text-sm text-gray-400">Das JavaScript kommuniziert nun direkt und ohne Umwege mit deiner API</p>
        </header>

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            {% for msg in messages %}
              <div class="p-4 rounded bg-blue-900/50 border border-blue-700 text-blue-200 text-sm">{{ msg }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <div class="bg-gray-900 p-6 rounded-lg border border-gray-800 shadow-xl">
            <h2 class="text-lg font-semibold mb-4 text-white">API-Konfiguration (Deine API)</h2>
            <form action="/config/save" method="POST" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-xs font-medium text-gray-400 uppercase mb-1">Deine API Base Link</label>
                        <input type="text" name="api_base_link" value="{{ cfg.api_base_link }}" class="w-full bg-gray-950 border border-gray-700 rounded p-2 text-sm text-white font-mono">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-400 uppercase mb-1">Scopes</label>
                        <input type="text" name="scopes" value="{{ cfg.scopes }}" class="w-full bg-gray-950 border border-gray-700 rounded p-2 text-sm text-white font-mono">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-400 uppercase mb-1">Client ID</label>
                        <input type="text" name="client_id" value="{{ cfg.client_id or '' }}" class="w-full bg-gray-950 border border-gray-700 rounded p-2 text-sm text-white font-mono">
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-gray-400 uppercase mb-1">Client Secret</label>
                        <input type="password" name="client_secret" value="{{ cfg.client_secret or '' }}" class="w-full bg-gray-950 border border-gray-700 rounded p-2 text-sm text-white font-mono">
                    </div>
                </div>
                
                <div class="p-3 bg-gray-950 rounded border border-gray-800 flex justify-between items-center text-sm">
                    <div>
                        <span class="text-gray-400">Status:</span> 
                        <span class="font-bold {% if cfg.auth_status == 'Erfolgreich autorisiert' %}text-emerald-400{% else %}text-red-400{% endif %}">{{ cfg.auth_status }}</span>
                        {% if cfg.last_error %}<p class="text-xs text-red-400 mt-1 font-mono">{{ cfg.last_error }}</p>{% endif %}
                    </div>
                    <button type="submit" class="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded text-xs font-medium transition">Speichern & Autorisieren</button>
                </div>
            </form>
        </div>

        {% if cfg.auth_status == 'Erfolgreich autorisiert' %}
        <div class="bg-gray-900 p-6 rounded-lg border border-gray-800 shadow-xl space-y-6">
            <h2 class="text-lg font-semibold text-white">Mediensteuerung</h2>
            
            <div id="player-status" class="p-4 bg-gray-950 rounded border border-gray-800 flex flex-col sm:flex-row gap-4 items-center">
                <div class="text-center text-xs text-gray-500">Lade Status direkt von deiner API...</div>
            </div>

            <div class="flex flex-wrap gap-2">
                <button onclick="togglePlayback()" class="bg-emerald-600 hover:bg-emerald-500 px-4 py-2 rounded text-sm font-medium">⏯ Play / Pause umschalten</button>
                <button onclick="controlAction('/player/pause', 'PUT')" class="bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded text-sm font-medium">⏸ Pause</button>
                <button onclick="controlAction('/player/play', 'PUT')" class="bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded text-sm font-medium">▶ Play</button>
            </div>

            <div class="flex flex-wrap gap-4 items-center pt-2 border-t border-gray-800">
                <span class="text-xs font-medium text-gray-400 uppercase">Repeat-Modus setzen:</span>
                <button onclick="controlAction('/player/repeat/off', 'PUT')" class="bg-gray-800 hover:bg-gray-700 px-3 py-1 rounded text-xs">Off</button>
                <button onclick="controlAction('/player/repeat/context', 'PUT')" class="bg-gray-800 hover:bg-gray-700 px-3 py-1 rounded text-xs">Context</button>
                <button onclick="controlAction('/player/repeat/track', 'PUT')" class="bg-gray-800 hover:bg-gray-700 px-3 py-1 rounded text-xs">Track</button>
            </div>

            <div class="pt-4 border-t border-gray-800 space-y-4">
                <h3 class="text-md font-semibold text-white">Warteschlange (Queue)</h3>
                <div class="flex gap-2">
                    <input type="text" id="track-id" placeholder="Spotify Track ID" class="flex-1 bg-gray-950 border border-gray-700 rounded p-2 text-sm text-white font-mono">
                    <button onclick="addToQueue()" class="bg-purple-600 hover:bg-purple-500 px-4 py-2 rounded text-sm font-medium">Zur Queue hinzufügen</button>
                </div>
                <div id="queue-list" class="space-y-2 max-h-60 overflow-y-auto"></div>
            </div>
        </div>

        <script>
            // Sichere Übergabe der Konfigurationen aus Python direkt in globale JS-Konstanten
            const API_BASE_URL = "{{ cfg.api_base_link.rstrip('/') }}";
            const ACCESS_TOKEN = "{{ token }}";

            // Zentralisierte Fetch-Funktion für direkte API-Anfragen mit Auth-Header
            async function callApi(endpoint, method = 'GET') {
                const headers = {
                    'Authorization': `Bearer ${ACCESS_TOKEN}`
                };
                const response = await fetch(`${API_BASE_URL}/${endpoint.lstrip('/')}`, {
                    method: method,
                    headers: headers
                });
                if (response.status === 204) return { success: true };
                if (response.status === 451) {
                    alert("Aktion nicht moeglich: Die Spotify API erlaubt im Direktmodus kein Loeschen aus der Queue.");
                    return null;
                }
                return response.json();
            }

            // String-Säuberungs-Helfer für Pfade
            String.prototype.lstrip = function(chars) {
                chars = chars || '\\s';
                return this.replace(new RegExp('^[' + chars + ']+'), '');
            };

            async function updatePlayback() {
                try {
                    let statusData = await callApi('/player', 'GET');
                    let repeatData = await callApi('/player/get-repeat', 'GET');
                    
                    let container = document.getElementById('player-status');
                    
                    if(statusData && statusData.item) {
                        let artwork = statusData.item.album?.images[0]?.url || '';
                        let artists = statusData.item.artists?.map(a => a.name).join(', ') || 'Unbekannt';
                        
                        container.innerHTML = `
                            <img src="${artwork}" class="w-16 h-16 rounded shadow-md object-cover bg-gray-800">
                            <div class="flex-1 text-center sm:text-left">
                                <div class="font-bold text-white text-base">${statusData.item.name}</div>
                                <div class="text-sm text-gray-400">${artists}</div>
                                <div class="text-xs text-emerald-400 mt-1 font-mono uppercase tracking-wider">
                                    ${statusData.is_playing ? '▶ Spielt' : '⏸ Pausiert'} | Repeat: ${repeatData?.repeat_state || 'unbekannt'}
                                </div>
                            </div>
                        `;
                    } else {
                        container.innerHTML = '<div class="text-xs text-gray-400">Keine aktive Wiedergabe im Player-Objekt gefunden.</div>';
                    }
                } catch(e) { console.error("Fehler beim Laden des Status:", e); }
            }

            async function updateQueue() {
                try {
                    let data = await callApi('/queue/get-list', 'GET');
                    let container = document.getElementById('queue-list');
                    
                    if(Array.isArray(data) && data.length > 0) {
                        container.innerHTML = data.map((item, idx) => `
                            <div class="flex items-center justify-between p-2 bg-gray-950 rounded border border-gray-800 text-sm">
                                <div class="flex items-center gap-3 min-w-0 flex-1">
                                    <span class="text-xs text-gray-500 w-4">${idx+1}</span>
                                    <div class="min-w-0 flex-1">
                                        <div class="truncate text-white font-medium">${item.songname}</div>
                                        <div class="truncate text-xs text-gray-400">${item.artistname}</div>
                                    </div>
                                </div>
                                <button onclick="removeFromQueue('${item['spotify-song-id']}')" class="text-xs text-red-400 hover:text-red-300 ml-2 font-medium px-2 py-1 bg-gray-900 border border-gray-800 rounded">Entfernen</button>
                            </div>
                        `).join('');
                    } else {
                        container.innerHTML = '<div class="text-xs text-gray-400 p-2">Warteschlange ist leer oder liefert keine Daten.</div>';
                    }
                } catch(e) { console.error("Fehler beim Laden der Queue:", e); }
            }

            async function togglePlayback() {
                try {
                    let data = await callApi('/player/play-pause', 'GET');
                    if(data) {
                        if (data.is_playing) {
                            await callApi('/player/pause', 'PUT');
                        } else {
                            await callApi('/player/play', 'PUT');
                        }
                        setTimeout(() => { updatePlayback(); updateQueue(); }, 400);
                    }
                } catch(e) { console.error(e); }
            }

            async function controlAction(endpoint, method) {
                await callApi(endpoint, method);
                setTimeout(() => { updatePlayback(); updateQueue(); }, 400);
            }

            async function addToQueue() {
                let idInput = document.getElementById('track-id');
                let trackId = idInput.value.trim();
                if(trackId !== '') {
                    await callApi(`/queue/add/${encodeURIComponent(trackId)}`, 'POST');
                    idInput.value = '';
                    setTimeout(updateQueue, 400);
                }
            }

            async function removeFromQueue(trackId) {
                if(!trackId) return;
                await callApi(`/queue/remove/${encodeURIComponent(trackId)}`, 'DELETE');
                setTimeout(updateQueue, 400);
            }

            setInterval(updatePlayback, 5000);
            setInterval(updateQueue, 10000);
            updatePlayback();
            updateQueue();
        </script>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    cfg = get_cfg()
    token = get_valid_token(cfg) or ""
    return render_template_string(DASHBOARD_HTML, cfg=cfg, token=token)

@app.route('/config/save', methods=['POST'])
def save_config():
    cfg = get_cfg()
    cfg.api_base_link = request.form.get('api_base_link').strip()
    cfg.scopes = request.form.get('scopes').strip()
    cfg.client_id = request.form.get('client_id').strip() or None
    cfg.client_secret = request.form.get('client_secret').strip() or None
    
    cfg.auth_status = "Verbindung wird aufgebaut..."
    cfg.last_error = None
    db.session.commit()

    redirect_uri = "https://client.extrahelden.de/auth/callback"
    
    auth_url = (
        f"{cfg.api_base_link.rstrip('/')}/authorize"
        f"?client_id={cfg.client_id}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&scope={urllib.parse.quote(cfg.scopes, safe='')}"
        f"&state=client_state"
    )
    return redirect(auth_url)

@app.route('/auth/callback')
def auth_callback():
    code = request.args.get('code')
    error = request.args.get('error')
    cfg = get_cfg()

    if error:
        cfg.auth_status = "Fehlgeschlagen"
        cfg.last_error = f"Deine API lieferte Fehler: {error}"
        db.session.commit()
        return redirect('/')

    try:
        base_url = cfg.api_base_link.rstrip('/')
        res = requests.post(
            f"{base_url}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret
            },
            timeout=5
        )
        
        if res.status_code == 200:
            data = res.json()
            cfg.access_token = data.get("access_token")
            cfg.expires_at = int(time.time()) + data.get("expires_in", 86400)
            cfg.auth_status = "Erfolgreich autorisiert"
            cfg.last_error = None
            flash("Erfolgreich an deiner API autorisiert!")
        else:
            cfg.auth_status = "Fehlgeschlagen"
            cfg.last_error = f"Deine API (/token) meldet HTTP {res.status_code}: {res.text}"
    except Exception as e:
        cfg.auth_status = "Fehlgeschlagen"
        cfg.last_error = f"Netzwerkfehler: {str(e)}"

    db.session.commit()
    return redirect('/')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=2070, debug=False)