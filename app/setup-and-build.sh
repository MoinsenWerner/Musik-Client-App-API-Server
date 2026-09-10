#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLUTTER_DIR="${FLUTTER_DIR:-/opt/flutter}"
ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-/opt/android-sdk}"
OUTPUT_DIR=/home/passkey-apk
KEYSTORE="$OUTPUT_DIR/passkey-release.jks"
KEY_INFO="$OUTPUT_DIR/signing.env"

if [[ $EUID -eq 0 ]]; then SUDO=; else SUDO=sudo; fi
$SUDO apt-get update
$SUDO apt-get install -y curl git unzip xz-utils zip ca-certificates openssl

export PATH="$FLUTTER_DIR/bin:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools:$PATH"
export ANDROID_SDK_ROOT
export ANDROID_HOME="$ANDROID_SDK_ROOT"
git config --global --add safe.directory "$FLUTTER_DIR" || true

if [[ ! -x "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" ]]; then
  archive="$(mktemp)"
  curl -fL https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -o "$archive"
  temp="$(mktemp -d)"
  unzip -q "$archive" -d "$temp"
  $SUDO mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools/latest"
  $SUDO cp -a "$temp/cmdline-tools/." "$ANDROID_SDK_ROOT/cmdline-tools/latest/"
  $SUDO chown -R "$(id -u):$(id -g)" "$ANDROID_SDK_ROOT"
  rm -rf "$archive" "$temp"
fi

yes | sdkmanager --licenses >/dev/null || true
sdkmanager "platform-tools" "platforms;android-35" "platforms;android-36" "build-tools;35.0.0"
flutter config --android-sdk "$ANDROID_SDK_ROOT"
flutter precache --android

# Flutter's generated launcher/wrapper files are copied without replacing app sources.
if [[ ! -f "$APP_DIR/android/gradlew" || ! -f "$APP_DIR/android/gradle/wrapper/gradle-wrapper.jar" ]]; then
  temp_project="$(mktemp -d)"
  flutter create --platforms=android --project-name passkey_vault "$temp_project/project"
  cp "$temp_project/project/android/gradlew" "$APP_DIR/android/gradlew"
  cp "$temp_project/project/android/gradlew.bat" "$APP_DIR/android/gradlew.bat"
  cp -a "$temp_project/project/android/gradle/wrapper/." "$APP_DIR/android/gradle/wrapper/"
  rm -rf "$temp_project"
fi
chmod +x "$APP_DIR/android/gradlew"
mkdir -p "$OUTPUT_DIR"
if [[ ! -f "$KEYSTORE" ]]; then
  STORE_PASSWORD="$(openssl rand -hex 24)"
  KEY_PASSWORD="$(openssl rand -hex 24)"
  keytool -genkeypair -v -keystore "$KEYSTORE" -alias passkey-vault \
    -storetype JKS -keyalg RSA -keysize 4096 -validity 10000 \
    -storepass "$STORE_PASSWORD" -keypass "$KEY_PASSWORD" \
    -dname "CN=Passkey Vault, O=plsreload, C=DE"
  umask 077
  printf 'STORE_PASSWORD=%q\nKEY_PASSWORD=%q\n' "$STORE_PASSWORD" "$KEY_PASSWORD" > "$KEY_INFO"
fi
# shellcheck disable=SC1090
source "$KEY_INFO"
cat > "$APP_DIR/android/key.properties" <<PROPERTIES
storePassword=$STORE_PASSWORD
keyPassword=$KEY_PASSWORD
keyAlias=passkey-vault
storeFile=$KEYSTORE
PROPERTIES
chmod 600 "$APP_DIR/android/key.properties" "$KEY_INFO"
printf 'flutter.sdk=%s\nsdk.dir=%s\n' "$FLUTTER_DIR" "$ANDROID_SDK_ROOT" > "$APP_DIR/android/local.properties"

cd "$APP_DIR"
flutter pub get
flutter analyze
flutter test
flutter build apk --release
install -m 0644 build/app/outputs/flutter-apk/app-release.apk "$OUTPUT_DIR/output.apk"

FINGERPRINT="$(keytool -list -v -keystore "$KEYSTORE" -alias passkey-vault -storepass "$STORE_PASSWORD" | sed -n 's/^[[:space:]]*SHA256: //p' | head -1)"
echo "APK erstellt: $OUTPUT_DIR/output.apk"
echo "ANDROID_APP_PACKAGE=de.plsreload.passkey_vault"
echo "ANDROID_CERT_SHA256=$FINGERPRINT"
echo "Setze beide Werte beim Flask-Server und starte ihn neu."
