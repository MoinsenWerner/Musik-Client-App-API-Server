import os
import time
import requests
from flask import Flask, request, jsonify, render_template_string, redirect, flash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'hbc_client_secret_key_local_2070'

# ==========================================
# KONFIGURATION & PERSISTENZ (Simuliert Tasker-Variablen)
# ==========================================
CONFIG = {
    "HBC_BaseLink": "http://37.44.215.123:2050",  # Ihr Gateway
    "HBC_ClientId": "testid",                     # Muss im Gateway-Dashboard generiert werden
    "HBC_ClientSecret": "testsecret",             # Muss im Gateway-Dashboard generiert werden
    "HBC_SpotifyAuthHeader": "",                  # Cache für Access-Token
    "HBC_TokenExpiresAt": 0,                      # Ablaufzeitstempel
    "HBC_SpotifyPlayPauseVar": "paused",          # playing / paused
    "HBC_RepeatingSymbol": "off"                  # off / context / track
}

# ==========================================
# OAUTH2 TOKENGANNER & CLIENT-AUTHENTIFIZIERUNG
# ==========================================
def get_authenticated_headers():
    """
    Spiegelt die interne OAuth2-Validierung wider. Holt ein gültiges
    Bearer-Token vom Gateway und speichert es temporär im Cache.
    """
    now = int(time.time())
    
    # Wenn Token existiert und noch mindestens 30 Sekunden gültig ist, nutze es
    if CONFIG["HBC_SpotifyAuthHeader"] and CONFIG["HBC_TokenExpiresAt"] > (now + 30):
        return {"Authorization": CONFIG["HBC_SpotifyAuthHeader"]}
    
    # Falls Platzhalter gesetzt sind, abbrechen
    if CONFIG["HBC_ClientId"] in ["testid", ""] or CONFIG["HBC_ClientSecret"] in ["testsecret", ""]:
        return None

    try:
        # Client Credentials Grant an den /token Endpunkt des Gateways senden
        token_url = f"{CONFIG['HBC_BaseLink'].rstrip('/')}/token"
        res = requests.post(
            token_url,
            data={"grant_type": "client_credentials"},
            auth=(CONFIG["HBC_ClientId"], CONFIG["HBC_ClientSecret"]),
            timeout=5
        )
        if res.status_code == 200:
            data = res.json()
            access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            
            CONFIG["HBC_SpotifyAuthHeader"] = f"Bearer {access_token}"
            CONFIG["HBC_TokenExpiresAt"] = now + expires_in
            return {"Authorization": CONFIG["HBC_SpotifyAuthHeader"]}
    except requests.exceptions.RequestException:
        pass
    
    # Rückfallebene falls Token-Abruf fehlschlägt (nutze gecashten Wert)
    if CONFIG["HBC_SpotifyAuthHeader"]:
        return {"Authorization": CONFIG["HBC_SpotifyAuthHeader"]}
    return None

# ==========================================
# API-ABFRAGEN AN DAS GATEWAY (Tasker Task Äquivalente)
# ==========================================
def fetch_current_song():
    """ Entspricht Task: HBC Get Playing Song """
    headers = get_authenticated_headers()
    if not headers:
        return None
    try:
        url = f"{CONFIG['HBC_BaseLink'].rstrip('/')}/player"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            # Aktualisiere internen Play-State analog zu Tasker
            if data.get("is_playing"):
                CONFIG["HBC_SpotifyPlayPauseVar"] = "playing"
            else:
                CONFIG["HBC_SpotifyPlayPauseVar"] = "paused"
            
            # Repeat State spiegeln
            CONFIG["HBC_RepeatingSymbol"] = data.get("repeat_state", "off")
            return data
    except Exception:
        pass
    return None

def fetch_playlists():
    """ Entspricht Task: HBC Get My Playlists """
    headers = get_authenticated_headers()
    if not headers:
        return []
    try:
        # Direkter Aufruf der Spotify-Schnittstelle über das Gateway im Direkt-Modus
        # Falls Ihr Gateway Routen umschreibt, spiegelt dies den Pfad /playlists wider
        url = f"{CONFIG['HBC_BaseLink'].rstrip('/')}/playlists"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            return res.json().get("items", [])
    except Exception:
        pass
    return []

# ==========================================
# WEB DASHBOARD (HTML / Tailwind UI)
# Spiegelt die Elemente der Szenen "HBC Startseite" & "HBC Einstellungen"
# ==========================================
DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>HBC Client Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
</head>
<body class="bg-slate-950 text-slate-100 font-sans p-4 md:p-8">
    <div class="max-w-5xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        <div class="lg:col-span-2 space-y-6">
            <header class="bg-slate-900 border border-slate-800 p-6 rounded-xl shadow-2xl">
                <h1 class="text-2xl font-black text-white tracking-wide">HBC STARTSEITE</h1>
                <p class="text-xs text-slate-400 font-mono mt-1">Client Connected to: {{ config.HBC_BaseLink }}</p>
            </header>

            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                {% for category, message in messages %}
                  <div class="p-4 rounded-lg text-sm {% if category == 'error' %}bg-red-950/80 border border-red-800 text-red-200{% else %}bg-emerald-950/80 border border-emerald-800 text-emerald-200{% endif %}">
                    {{ message }}
                  </div>
                {% endfor %}
              {% endif %}
            {% endwith %}

            <div class="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-2xl space-y-6">
                <div class="flex flex-col sm:flex-row items-center gap-6">
                    <div class="w-36 h-36 bg-slate-950 border border-slate-800 rounded-lg flex items-center justify-center overflow-hidden shadow-inner relative">
                        {% if song and song.item and song.item.album and song.item.album.images %}
                            <img src="{{ song.item.album.images[0].url }}" class="w-full h-full object-cover">
                        {% else %}
                            <span class="text-slate-600 text-xs font-mono">No Cover</span>
                        {% endif %}
                    </div>
                    
                    <div class="flex-1 text-center sm:text-left space-y-1">
                        <span class="px-2 py-0.5 bg-blue-900/50 text-blue-300 border border-blue-700 text-[10px] font-bold uppercase tracking-widest rounded">Now Playing</span>
                        <h2 class="text-xl font-bold text-white truncate max-w-sm">{{ song.item.name if (song and song.item) else 'Kein Song aktiv' }}</h2>
                        <p class="text-sm text-slate-400 truncate max-w-sm">
                            {{ song.item.artists | map(attribute='name') | join(', ') if (song and song.item) else 'Bitte Player starten' }}
                        </p>
                        <p class="text-xs text-slate-500 italic font-mono pt-1">Device: {{ song.device.name if (song and song.device) else 'N/A' }}</p>
                    </div>
                </div>

                {% set progress_ms = song.progress_ms if song else 0 %}
                {% set duration_ms = song.item.duration_ms if (song and song.item) else 1 %}
                {% set percent = (progress_ms / duration_ms * 100) | round(1) %}
                <div class="space-y-1">
                    <div class="w-full bg-slate-950 rounded-full h-2 overflow-hidden border border-slate-800">
                        <div class="bg-emerald-500 h-full transition-all duration-500" style="width: {{ percent if percent <= 100 else 100 }}%"></div>
                    </div>
                    <div class="flex justify-between text-[11px] font-mono text-slate-400">
                        <span>{{ (progress_ms / 1000 / 60) | int }}:{{ "%02d" | format((progress_ms / 1000) | int % 60) }}</span>
                        <span class="text-slate-600">{{ percent }}%</span>
                        <span>{{ (duration_ms / 1000 / 60) | int }}:{{ "%02d" | format((duration_ms / 1000) | int % 60) }}</span>
                    </div>
                </div>

                <div class="grid grid-cols-4 gap-2 pt-2">
                    <a href="/action/previous" class="bg-slate-950 hover:bg-slate-800 border border-slate-800 py-3 rounded-lg text-center font-medium transition text-sm">
                        ⏮ Prev
                    </a>
                    <a href="/action/playpause" class="bg-slate-950 hover:bg-slate-800 border border-slate-800 py-3 rounded-lg text-center font-bold transition text-sm text-emerald-400">
                        {% if config.HBC_SpotifyPlayPauseVar == 'playing' %}⏸ Pause{% else %}▶ Play{% endif %}
                    </a>
                    <a href="/action/next" class="bg-slate-950 hover:bg-slate-800 border border-slate-800 py-3 rounded-lg text-center font-medium transition text-sm">
                        ⏭ Next
                    </a>
                    <a href="/action/repeat" class="bg-slate-950 hover:bg-slate-800 border border-slate-800 py-3 rounded-lg text-center font-mono text-xs transition flex items-center justify-center gap-1">
                        🔄 <span class="uppercase font-bold {% if config.HBC_RepeatingSymbol != 'off' %}text-yellow-400{% endif %}">{{ config.HBC_RepeatingSymbol }}</span>
                    </a>
                </div>

                <form action="/action/queue" method="POST" class="pt-4 border-t border-slate-800/60 flex gap-2">
                    <input type="text" name="song_id" placeholder="Spotify Track ID (z.B. 4PTG3Z6ehGkBFm6TuvYTXS)" class="flex-1 bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs font-mono focus:outline-none focus:border-blue-500">
                    <button type="submit" class="bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium px-4 py-2 rounded transition shadow">
                        + Queue
                    </button>
                </form>
            </div>

            <div class="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-2xl">
                <h3 class="text-sm font-bold tracking-wider text-slate-400 uppercase mb-3">Eigene Playlists</h3>
                <div class="max-h-60 overflow-y-auto divide-y divide-slate-800/60 pr-2">
                    {% for pl in playlists %}
                        <div class="py-2.5 flex justify-between items-center text-xs">
                            <span class="font-medium text-slate-200 truncate max-w-xs">{{ pl.name }}</span>
                            <span class="font-mono text-slate-500 text-[10px] bg-slate-950 px-2 py-0.5 border border-slate-800 rounded">{{ pl.tracks.total }} Tracks</span>
                        </div>
                    {% else %}
                        <p class="text-xs text-slate-500 font-mono italic">Keine Playlists gefunden oder Gateway nicht im Direktmodus.</p>
                    {% endfor %}
                </div>
            </div>
        </div>

        <div class="space-y-6">
            <div class="bg-slate-900 border border-slate-800 p-6 rounded-xl shadow-2xl space-y-4">
                <div>
                    <h2 class="text-xl font-black text-white tracking-wide">HBC EINSTELLUNGEN</h2>
                    <p class="text-[11px] text-slate-400 font-mono mt-0.5">Hierarchische Systemvariablen</p>
                </div>
                
                <form action="/settings/save" method="POST" class="space-y-4 pt-2">
                    <div>
                        <label class="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">Gateway Base Link</label>
                        <input type="text" name="HBC_BaseLink" value="{{ config.HBC_BaseLink }}" class="w-full bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">Client ID</label>
                        <input type="text" name="HBC_ClientId" value="{{ config.HBC_ClientId }}" class="w-full bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs font-mono text-blue-400 focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">Client Secret</label>
                        <input type="password" name="HBC_ClientSecret" value="{{ config.HBC_ClientSecret }}" placeholder="••••••••••••••••" class="w-full bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs font-mono text-emerald-400 focus:outline-none focus:border-blue-500">
                    </div>
                    
                    <div class="p-3 bg-slate-950 rounded border border-slate-800 flex justify-between items-center text-[11px] font-mono">
                        <span class="text-slate-500">Auth Status:</span>
                        {% if config.HBC_SpotifyAuthHeader %}
                            <span class="text-emerald-400 font-bold">● Token geladen</span>
                        {% else %}
                            <span class="text-red-400 font-bold">○ Token fehlt</span>
                        {% endif %}
                    </div>

                    <button type="submit" class="w-full bg-slate-100 hover:bg-white text-slate-950 font-bold py-2 rounded transition text-xs shadow-md">
                        Variablen aktualisieren
                    </button>
                </form>
                
                <div class="pt-4 border-t border-slate-800 flex justify-center">
                    <a href="/" class="text-xs font-mono text-blue-400 hover:underline">🔄 Dashboard manuell aktualisieren</a>
                </div>
            </div>
        </div>

    </div>
</body>
</html>
"""

# ==========================================
# APPLIKATIONS-ROUTEN
# ==========================================

@app.route('/', methods=['GET'])
def index():
    # Sync-Aufrufe starten (entspricht Task-Triggern beim Öffnen der Szene)
    current_song = fetch_current_song()
    playlists_list = fetch_playlists()
    return render_template_string(
        DASHBOARD_TEMPLATE, 
        config=CONFIG, 
        song=current_song, 
        playlists=playlists_list
    )

@app.route('/settings/save', methods=['POST'])
def save_settings():
    """ Spiegelt die Felder der Szene 'HBC Einstellungen' wider """
    CONFIG["HBC_BaseLink"] = request.form.get("HBC_BaseLink", "").strip()
    CONFIG["HBC_ClientId"] = request.form.get("HBC_ClientId", "").strip()
    
    secret = request.form.get("HBC_ClientSecret", "").strip()
    if secret:
        CONFIG["HBC_ClientSecret"] = secret

    # Token-Cache invalidieren, um Neu-Authentifizierung mit geänderten Daten zu erzwingen
    CONFIG["HBC_SpotifyAuthHeader"] = ""
    CONFIG["HBC_TokenExpiresAt"] = 0
    
    flash("Systemvariablen erfolgreich aktualisiert.", "success")
    return redirect('/')

# ---- STEUERUNGS-AKTIONEN (Proxy zu Spotify über das Gateway) ----

@app.route('/action/playpause', methods=['GET'])
def action_playpause():
    """ Entspricht Task: HBC Set PlayPause """
    headers = get_authenticated_headers()
    if not headers:
        flash("Authentifizierungs-Fehler: Prüfen Sie Client-ID/Secret.", "error")
        return redirect('/')
        
    # Bestimme Aktion basierend auf der aktuellen Variable
    action = "pause" if CONFIG["HBC_SpotifyPlayPauseVar"] == "playing" else "play"
    
    try:
        url = f"{CONFIG['HBC_BaseLink'].rstrip('/')}/player/{action}"
        res = requests.post(url, headers=headers, timeout=5)
        if res.status_code in [200, 204]:
            CONFIG["HBC_SpotifyPlayPauseVar"] = "playing" if action == "play" else "paused"
        else:
            flash(f"Gateway meldet Fehler: {res.text}", "error")
    except Exception as e:
        flash(f"Verbindungsfehler: {str(e)}", "error")
        
    return redirect('/')

@app.route('/action/next', methods=['GET'])
def action_next():
    """ Entspricht Task: HBC Next """
    headers = get_authenticated_headers()
    if headers:
        try:
            url = f"{CONFIG['HBC_BaseLink'].rstrip('/')}/player/next"
            requests.post(url, headers=headers, timeout=5)
        except Exception:
            pass
    return redirect('/')

@app.route('/action/previous', methods=['GET'])
def action_previous():
    """ Entspricht Task: HBC Previous """
    headers = get_authenticated_headers()
    if headers:
        try:
            url = f"{CONFIG['HBC_BaseLink'].rstrip('/')}/player/previous"
            requests.post(url, headers=headers, timeout=5)
        except Exception:
            pass
    return redirect('/')

@app.route('/action/repeat', methods=['GET'])
def action_repeat():
    """ Entspricht Task: HBC Read Repeat State / HB Set Repeat State """
    headers = get_authenticated_headers()
    if not headers:
        return redirect('/')
        
    # Rotationslogik für den State: off -> context -> track -> off
    current_state = CONFIG["HBC_RepeatingSymbol"]
    if current_state == "off":
        next_state = "context"
    elif current_state == "context":
        next_state = "track"
    else:
        next_state = "off"
        
    try:
        url = f"{CONFIG['HBC_BaseLink'].rstrip('/')}/player/repeat?state={next_state}"
        res = requests.post(url, headers=headers, timeout=5)
        if res.status_code in [200, 204]:
            CONFIG["HBC_RepeatingSymbol"] = next_state
        else:
            flash(f"Fehler bei Repeat-Änderung: {res.text}", "error")
    except Exception as e:
        flash(f"Netzwerkfehler: {str(e)}", "error")
        
    return redirect('/')

@app.route('/action/queue', methods=['POST'])
def action_queue():
    """ Entspricht Task: HBC Add Quewe """
    song_id = request.form.get("song_id", "").strip()
    if not song_id:
        flash("Bitte eine gültige Song ID eingeben.", "error")
        return redirect('/')
        
    headers = get_authenticated_headers()
    if not headers:
        return redirect('/')
        
    try:
        # Sendet die Song-ID als Parameter an die Queue-Schnittstelle
        url = f"{CONFIG['HBC_BaseLink'].rstrip('/')}/player/queue?uri=spotify:track:{song_id}"
        res = requests.post(url, headers=headers, timeout=5)
        if res.status_code in [200, 204]:
            flash("Song erfolgreich zur Warteschlange hinzugefügt!", "success")
        else:
            flash(f"Fehler beim Hinzufügen: {res.text}", "error")
    except Exception as e:
        flash(f"Fehler: {str(e)}", "error")
        
    return redirect('/')

# ==========================================
# INITIALISIERUNG UND START
# ==========================================
if __name__ == '__main__':
    # Startet die Anwendung auf Port 2070 analog zu Ihrer Spezifikation
    app.run(host='0.0.0.0', port=2070, debug=False)
