#!/usr/bin/env bash

# Diese Datei muss mit "source ./set-secrets.sh" geladen werden, damit die
# exportierten Variablen in der aktuellen Shell verfügbar bleiben.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'Bitte mit "source ./set-secrets.sh" statt direkt ausführen.\n' >&2
  exit 1
fi

printf '\nTasker Passkey Vault einrichten\n\n'
printf 'VAULT_KEY verschlüsselt die Passwörter in der Datenbank.\n'
read -r -p 'Vorhandenen VAULT_KEY eingeben oder Enter für einen neuen Schlüssel: ' vault_key
if [[ -z "$vault_key" ]]; then
  vault_key="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" || return 1
fi

printf '\nSECRET_KEY schützt die kurzlebigen Flask-Sitzungen und Challenges.\n'
read -r -p 'Vorhandenen SECRET_KEY eingeben oder Enter für einen neuen Schlüssel: ' secret_key
if [[ -z "$secret_key" ]]; then
  secret_key="$(python -c 'import secrets; print(secrets.token_hex(32))')" || return 1
fi

printf '\nRP_ID ist der öffentliche Hostname ohne https:// und ohne Pfad.\n'
read -r -p 'RP_ID [auth.extrahelden.de]: ' rp_id
rp_id="${rp_id:-auth.extrahelden.de}"

printf '\nORIGIN ist die vollständige öffentliche HTTPS-Adresse des Cloudflare-Tunnels.\n'
read -r -p 'ORIGIN [https://auth.extrahelden.de]: ' origin
origin="${origin:-https://auth.extrahelden.de}"

if [[ "$rp_id" == *"://"* || "$rp_id" == */* ]]; then
  printf 'Fehler: RP_ID darf nur ein Hostname sein.\n' >&2
  return 1
fi
if [[ "$origin" != https://* ]]; then
  printf 'Fehler: ORIGIN muss für WebAuthn mit https:// beginnen.\n' >&2
  return 1
fi

export VAULT_KEY="$vault_key"
export SECRET_KEY="$secret_key"
export RP_ID="$rp_id"
export ORIGIN="$origin"
unset vault_key secret_key rp_id origin

printf '\nKonfiguration geladen:\n'
printf '  RP_ID=%s\n' "$RP_ID"
printf '  ORIGIN=%s\n' "$ORIGIN"
printf 'Die geheimen Schlüssel wurden erzeugt/exportiert, aber nicht angezeigt.\n'
printf 'Jetzt kann der Server mit "python app.py" gestartet werden.\n'
