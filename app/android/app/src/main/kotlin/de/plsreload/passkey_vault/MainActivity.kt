package de.plsreload.passkey_vault

import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.credentials.CreatePublicKeyCredentialRequest
import androidx.credentials.CreatePublicKeyCredentialResponse
import androidx.credentials.CredentialManager
import androidx.credentials.GetCredentialRequest
import androidx.credentials.GetPublicKeyCredentialOption
import androidx.credentials.GetCredentialResponse
import androidx.credentials.PublicKeyCredential
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

class MainActivity : FlutterActivity() {
    companion object {
        private const val CHANNEL = "de.plsreload.passkey_vault/credentials"
        private const val BASE_URL = "https://api.plsreload.de"
        private const val RESULT_ACTION = "de.plsreload.passkey_vault.RESULT"
    }

    private lateinit var credentials: CredentialManager
    private val scope = CoroutineScope(Dispatchers.Main)
    private var pendingIntent: Intent? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        credentials = CredentialManager.create(this)
        pendingIntent = intent
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        pendingIntent = intent
        executeIntent(intent)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            .setMethodCallHandler { call, result ->
                val arguments = call.arguments as? Map<*, *> ?: emptyMap<String, String>()
                if (call.method == "diagnostics") {
                    scope.launch {
                        runCatching { diagnostics() }
                            .onSuccess(result::success)
                            .onFailure { error -> result.error("DIAGNOSTICS_ERROR", error.message, null) }
                    }
                } else {
                    execute(call.method, arguments.mapKeys { it.key.toString() }.mapValues { it.value?.toString().orEmpty() }) {
                        value, error -> if (error == null) result.success(value) else result.error("PASSKEY_ERROR", error, value)
                    }
                }
            }
        pendingIntent?.let(::executeIntent)
        pendingIntent = null
    }

    private fun executeIntent(source: Intent) {
        val operation = source.getStringExtra("operation") ?: when {
            source.action == "de.plsreload.passkey_vault.REGISTER" -> "register"
            source.action == "de.plsreload.passkey_vault.AUTHENTICATE" -> "authenticate"
            source.data?.host == "register" -> "register"
            source.data?.host == "get" -> "authenticate"
            else -> return
        }
        val values = mutableMapOf<String, String>()
        for (name in listOf("username", "password", "type")) {
            values[name] = source.getStringExtra(name) ?: source.data?.getQueryParameter(name).orEmpty()
        }
        execute(operation, values) { value, error ->
            val output = Intent(RESULT_ACTION).apply {
                setPackage("net.dinglisch.android.taskerm")
                putExtra("passkey_result", value.orEmpty())
                putExtra("passkey_error", error.orEmpty())
                putExtra("passkey_status", if (error == null) 200 else 400)
            }
            sendBroadcast(output)
        }
    }

    private fun execute(
        operation: String,
        values: Map<String, String>,
        done: (String?, String?) -> Unit,
    ) = scope.launch {
        try {
            val username = values["username"].orEmpty().trim()
            require(username.isNotEmpty()) { "Benutzername fehlt" }
            val result = when (operation) {
                "register" -> register(username, values["password"].orEmpty(), values["type"] ?: "fingerprint")
                "authenticate", "get" -> authenticate(username)
                else -> error("Unbekannte Operation: $operation")
            }
            done(result, null)
        } catch (error: Exception) {
            val message = error.message ?: error.javaClass.simpleName
            val details = if (message.contains("RP ID", ignoreCase = true)) {
                val diagnostic = runCatching { diagnostics() }
                    .getOrElse { diagnosticError -> "Diagnose nicht verfügbar: ${diagnosticError.message}" }
                "\nDiagnose: $diagnostic"
            } else {
                ""
            }
            done(null, "${error.javaClass.simpleName}: $message$details")
        }
    }

    private suspend fun diagnostics(): String {
        val session = ServerSession()
        val assetLinks = session.get("/.well-known/assetlinks.json")
        val health = session.get("/health")
        val fingerprint = appSigningFingerprint()
        val matches = runCatching {
            val targets = org.json.JSONArray(assetLinks)
            val values = targets.getJSONObject(0).getJSONObject("target")
                .getJSONArray("sha256_cert_fingerprints")
            (0 until values.length()).any { values.getString(it).equals(fingerprint, true) }
        }.getOrDefault(false)
        return JSONObject().apply {
            put("package_name", packageName)
            put("app_signing_sha256", fingerprint)
            put("assetlinks_matches_app_signature", matches)
            put("assetlinks", org.json.JSONArray(assetLinks))
            put("health", JSONObject(health))
        }.toString()
    }

    private fun appSigningFingerprint(): String {
        val packageInfo = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            packageManager.getPackageInfo(packageName, android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES)
        } else {
            @Suppress("DEPRECATION")
            packageManager.getPackageInfo(packageName, android.content.pm.PackageManager.GET_SIGNATURES)
        }
        val signatures = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            requireNotNull(packageInfo.signingInfo) {
                "APK-Signaturinformationen sind nicht verfügbar"
            }.apkContentsSigners
        } else {
            @Suppress("DEPRECATION")
            packageInfo.signatures
        }
        val signature = requireNotNull(signatures).firstOrNull()
            ?: error("Die APK besitzt keine Signatur")
        val bytes = MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())
        return bytes.joinToString(":") { byte -> "%02X".format(byte) }
    }

    private suspend fun register(username: String, password: String, type: String): String {
        require(password.isNotEmpty()) { "Passwort fehlt" }
        require(type == "fido" || type == "fingerprint") { "Typ muss fido oder fingerprint sein" }
        val session = ServerSession()
        val options = session.post("/api/register/options", JSONObject(mapOf(
            "username" to username, "password" to password, "type" to type,
        )).toString())
        val response = credentials.createCredential(this, CreatePublicKeyCredentialRequest(options))
        require(response is CreatePublicKeyCredentialResponse) { "Kein Public-Key-Credential erhalten" }
        return session.post("/api/register/verify", response.registrationResponseJson)
    }

    private suspend fun authenticate(username: String): String {
        val session = ServerSession()
        val options = session.post("/api/authenticate/options", JSONObject(mapOf("username" to username)).toString())
        val response: GetCredentialResponse = credentials.getCredential(
            this,
            GetCredentialRequest(listOf(GetPublicKeyCredentialOption(options))),
        )
        val credential = response.credential
        require(credential is PublicKeyCredential) { "Kein Public-Key-Credential erhalten" }
        return session.post("/api/authenticate/verify", credential.authenticationResponseJson)
    }

    private inner class ServerSession {
        private var cookie: String? = null
        suspend fun get(path: String): String = request(path, null)

        suspend fun post(path: String, body: String): String = request(path, body)

        private suspend fun request(path: String, body: String?): String = withContext(Dispatchers.IO) {
            val connection = URL(BASE_URL + path).openConnection() as HttpURLConnection
            try {
                connection.requestMethod = if (body == null) "GET" else "POST"
                connection.connectTimeout = 15_000
                connection.readTimeout = 30_000
                connection.doOutput = body != null
                connection.setRequestProperty("Accept", "application/json")
                if (body != null) {
                    connection.setRequestProperty("Content-Type", "application/json")
                }
                cookie?.let { connection.setRequestProperty("Cookie", it) }
                if (body != null) {
                    connection.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(body) }
                }
                connection.getHeaderField("Set-Cookie")?.substringBefore(';')?.let { cookie = it }
                val status = connection.responseCode
                val response = (if (status in 200..299) connection.inputStream else connection.errorStream)
                    ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
                if (status !in 200..299) {
                    val message = runCatching { JSONObject(response).optString("error") }.getOrNull()
                    error("HTTP $status: ${message?.ifBlank { response } ?: response}")
                }
                response
            } finally {
                connection.disconnect()
            }
        }
    }
}
