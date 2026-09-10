import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

void main() => runApp(const PasskeyVaultApp());

class PasskeyVaultApp extends StatelessWidget {
  const PasskeyVaultApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Passkey Vault',
        theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
        home: const VaultPage(),
      );
}

class VaultPage extends StatefulWidget {
  const VaultPage({super.key});
  @override
  State<VaultPage> createState() => _VaultPageState();
}

class _VaultPageState extends State<VaultPage> {
  static const channel = MethodChannel('de.plsreload.passkey_vault/credentials');
  final username = TextEditingController();
  final password = TextEditingController();
  String type = 'fingerprint';
  String result = 'Bereit';
  bool busy = false;

  Future<void> run(String operation) async {
    setState(() { busy = true; result = 'Credential Manager wird gestartet …'; });
    try {
      final value = await channel.invokeMethod<String>(operation, {
        'username': username.text.trim(),
        'password': password.text,
        'type': type,
      });
      setState(() => result = const JsonEncoder.withIndent('  ').convert(jsonDecode(value!)));
    } on PlatformException catch (error) {
      setState(() => result = 'Fehler: ${error.message ?? error.code}');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  void dispose() { username.dispose(); password.dispose(); super.dispose(); }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Passkey Vault')),
    body: ListView(padding: const EdgeInsets.all(20), children: [
      TextField(controller: username, decoration: const InputDecoration(labelText: 'Benutzername')),
      TextField(controller: password, obscureText: true, decoration: const InputDecoration(labelText: 'Passwort (nur Registrierung)')),
      DropdownButtonFormField<String>(initialValue: type, decoration: const InputDecoration(labelText: 'Passkey-Typ'), items: const [
        DropdownMenuItem(value: 'fingerprint', child: Text('Fingerprint / Geräte-PIN')),
        DropdownMenuItem(value: 'fido', child: Text('FIDO-Sicherheitsschlüssel')),
      ], onChanged: (value) => setState(() => type = value!)),
      const SizedBox(height: 20),
      FilledButton(onPressed: busy ? null : () => run('register'), child: const Text('Passkey registrieren')),
      OutlinedButton(onPressed: busy ? null : () => run('authenticate'), child: const Text('Passwort abrufen')),
      const SizedBox(height: 20),
      SelectableText(result, key: const Key('result')),
    ]),
  );
}
