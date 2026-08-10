import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/app.dart';
import 'package:mobile/core/auth_validators.dart';

void main() {
  testWidgets('builds the Himikama application shell', (tester) async {
    await tester.pumpWidget(
      const HimikamaApp(home: Scaffold(body: Text('Authentication test'))),
    );

    expect(find.text('Authentication test'), findsOneWidget);
  });

  test('password validator accepts the required strong format', () {
    expect(AuthValidators.password('LegalRights!2026'), isNull);
  });

  test('password validator rejects a weak password', () {
    expect(AuthValidators.password('password'), isNotNull);
  });
}
