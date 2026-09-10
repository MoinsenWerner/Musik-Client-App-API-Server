import 'package:flutter_test/flutter_test.dart';
import 'package:passkey_vault/main.dart';

void main() {
  testWidgets('shows both passkey operations', (tester) async {
    await tester.pumpWidget(const PasskeyVaultApp());
    expect(find.text('Passkey registrieren'), findsOneWidget);
    expect(find.text('Passwort abrufen'), findsOneWidget);
    expect(find.text('Benutzername'), findsOneWidget);
  });
}
