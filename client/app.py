import os
import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Globale Konfigurationsvariablen (Entsprechend den Tasker-Variablen)
HBC_BaseLink = "https://api.extrahelden.de"                        # Basis-URL des HBC-Servers
HBC_ClientId = "141ed07be8c66c8caafd9f12"                          # Standard-Testwert aus Task 208
HBC_ClientSecret = "4LmLvJSYsYkUXRoC_GX2Z_u5eAKFU7lHzhX18-aAvHA"   # Standard-Testwert aus Task 208
HBC_Scopes = "client, hcb-client"

# Globale Statusvariablen für das UI (Entsprechend den Szenen-Variablen)
HBC_SpotifyPlayPauseVar = "paused"                # 'playing' oder 'paused'
HBC_CurrPlayingSongName = "Kein Titel"
HBC_CurrPlayingSongArtistsName = "Unbekannter Interpret"
HBC_PlayerCurrSongPng = "https://via.placeholder.com/377"
HBC_SongDurationProgressMS = 0
HBCAuthHeader = None

def check_credentials():
    """Prüft analog zu Task 208 (act0), ob valide Anmeldedaten vorliegen."""
    if (HBC_ClientId in ["testid", ""] or HBC_ClientSecret in ["testsecret", ""]):
        return False
    return True

def refresh_oauth_token():
    """Bildet die OAuth2-Logik aus Task 218 (act1 & act2) ab."""
    global HBCAuthHeader
    if not check_credentials():
        return False
    
    try:
        # Tasker nutzt die eingebaute OAuth-Aktion (Code 351)
        # Hier simuliert durch einen Standard-OAuth2-Token-Wechsel
        response = requests.post(
            f"{HBC_BaseLink}/token",
            auth=(HBC_ClientId, HBC_ClientSecret),
            data={"grant_type": "client_credentials", "scope": HBC_Scopes},
            timeout=30
        )
        if response.status_code == 200:
            token_data = response.json()
            # Setzt den Auth-Header analog zu %HBCAuthHeader
            HBCAuthHeader = f"Bearer {token_data.get('access_token')}"
            return True
    except Exception as e:
        print(f"Fehler bei OAuth-Authentifizierung: {e}")
    return False

# ==========================================
# ROUTEN FÜR DAS WEB-DASHBOARD (SZENEN)
# ==========================================

@app.route('/')
def startseite():
    """Bildet die 'HBC Startseite' Szene ab."""
    global HBC_CurrPlayingSongName, HBC_CurrPlayingSongArtistsName, HBC_SpotifyPlayPauseVar, HBC_PlayerCurrSongPng
    
    # Erzwingt den Wechsel in die Einstellungen, wenn Daten ungültig (Task 208 / act1)
    if not check_credentials():
        return redirect(url_for('einstellungen'))
        
    # Hintergrund-Aktualisierung der Daten vor dem Rendern (Task 208 / Get Playing Song)
    get_playing_song()
    
    return render_template(
        'startseite.html',
        song_name=HBC_CurrPlayingSongName,
        artist_name=HBC_CurrPlayingSongArtistsName,
        play_state=HBC_SpotifyPlayPauseVar,
        cover_png=HBC_PlayerCurrSongPng,
        progress=HBC_SongDurationProgressMS
    )

@app.route('/einstellungen')
def einstellungen():
    """Bildet die 'HBC Einstellungen' Szene ab."""
    return render_template(
        'einstellungen.html',
        client_id=HBC_ClientId,
        client_secret=HBC_ClientSecret
    )

# ==========================================
# INTERNE LOGIK-ENDPUNKTE (EHEMALIGE TASKS)
# ==========================================

@app.route('/task/set_credentials', methods=['POST'])
def task_set_credentials():
    """Bildet die Logik von Task 220 und Task 221 ab (Speichern aus EditTextElementen)."""
    global HBC_ClientId, HBC_ClientSecret
    HBC_ClientId = request.form.get('client_id', '')
    HBC_ClientSecret = request.form.get('client_secret', '')
    
    # Versuche direkt nach dem Setzen die Authentifizierung
    refresh_oauth_token()
    return redirect(url_for('startseite'))

@app.route('/task/playpause', methods=['POST'])
def task_set_playpause():
    """Bildet Task 223 (HBC Set PlayPause) ab."""
    global HBC_SpotifyPlayPauseVar
    
    # UI Toggle Logik (act0 - act7)
    if HBC_SpotifyPlayPauseVar == "playing":
        action = "pause"
        HBC_SpotifyPlayPauseVar = "paused"
    else:
        action = "play"
        HBC_SpotifyPlayPauseVar = "playing"
        
    # Sende Befehl an das Backend via zentralem Post-Request-Task
    send_post_request(action)
    return redirect(url_for('startseite'))

@app.route('/task/next', methods=['POST'])
def task_next():
    """Bildet Task 214 (HBC Next) ab."""
    send_post_request("next")
    return redirect(url_for('startseite'))

@app.route('/task/previous', methods=['POST'])
def task_previous():
    """Bildet Task 215 (HBC Previous) ab."""
    send_post_request("previous")
    return redirect(url_for('startseite'))

@app.route('/task/add_queue/<song_id>', methods=['POST'])
def task_add_queue(song_id):
    """Bildet Task 213 (HBC Add Quewe) ab."""
    send_post_request("quewe", par2=song_id)
    return jsonify({"status": "success", "message": f"Song {song_id} hinzugefügt."})

@app.route('/task/repeat/<state>', methods=['POST'])
def task_set_repeat_state(state):
    """Bildet Task 217 (HBC Set Repeat State) ab."""
    send_post_request("set_repeat_state", par2=state)
    return redirect(url_for('startseite'))

# ==========================================
# BACKEND-INTERAKTION & API-ROUTING
# ==========================================

def send_post_request(par1, par2=None):
    """Zentrale POST-Abwicklung analog zu Task 218 (HBC Send Post Request)."""
    global HBCAuthHeader
    if not HBCAuthHeader:
        if not refresh_oauth_token():
            return None
            
    # Dynamischer URL-Aufbau (Konditionales Routing aus act4 - act14)
    link = HBC_BaseLink
    if par1 == "quewe":
        link += "/quewe"
    elif par1 in ["next", "previous", "play", "pause"]:
        link += f"/player/{par1}"
    elif par1 == "set_repeat_state":
        link += f"/player/set_repeat_state/{par2}"
        
    # JSON-Body-Generierung (act15 - act17)
    payload = {}
    if par2 and par1 != "set_repeat_state":
        payload = {"value": par2}
        
    headers = {"Authorization": HBCAuthHeader}
    
    try:
        # Physische HTTP-Anfrage (act18 / Code 339)
        response = requests.post(link, headers=headers, json=payload, timeout=30)
        return response.json()
    except Exception as e:
        print(f"Fehler bei POST-Request an Backend: {e}")
        return None

def get_playing_song():
    """Bildet Task 208 (HBC Get Playing Song) & Task 224 (HBC Get PlayPause) ab."""
    global HBC_CurrPlayingSongName, HBC_CurrPlayingSongArtistsName, HBC_SpotifyPlayPauseVar, HBC_PlayerCurrSongPng, HBC_SongDurationProgressMS, HBCAuthHeader
    
    if not HBCAuthHeader and not refresh_oauth_token():
        return
        
    headers = {"Authorization": HBCAuthHeader}
    try:
        # Get Current Playing Song Info
        response = requests.get(f"{HBC_BaseLink}/player", headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            
            # API-Mapping auf globale UI-Variablen
            HBC_CurrPlayingSongName = data.get("item", {}).get("name", "Kein Titel")
            artists = data.get("item", {}).get("artists", [])
            HBC_CurrPlayingSongArtistsName = ", ".join([a["name"] for a in artists]) if artists else "Unbekannter Interpret"
            
            images = data.get("item", {}).get("album", {}).get("images", [])
            HBC_PlayerCurrSongPng = images[0]["url"] if images else "https://via.placeholder.com/377"
            
            # Statusermittlung für PlayPause (Task 224 Logik)
            if data.get("playstate") == "pause":
                HBC_SpotifyPlayPauseVar = "paused"
            elif data.get("playstate") == "play":
                HBC_SpotifyPlayPauseVar = "playing"
                
            # Zeitfortschritt berechnen (Task 210 Logik)
            HBC_SongDurationProgressMS = data.get("progress_ms", 0)
            
    except Exception as e:
        print(f"Fehler beim Abrufen des aktuellen Songs: {e}")

if __name__ == '__main__':
    # Startet den Server exakt auf der gewünschten IP und Port-Vorgabe
    app.run(host='0.0.0.0', port=2070, debug=True)
