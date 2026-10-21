import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:mobile/main.dart';

void main() {
  test('formatDuration reads naturally', () {
    expect(formatDuration(45), '45 sec');
    expect(formatDuration(125), '2 min 05 sec');
    expect(formatDuration(3720), '1 hr 2 min');
  });

  test('formatClock shows minutes and seconds', () {
    expect(formatClock(75), '01:15');
  });

  testWidgets('App theme builds in light and dark and renders a widget', (WidgetTester tester) async {
    for (final dark in [false, true]) {
      await tester.pumpWidget(
        MaterialApp(
          theme: buildAppTheme(dark),
          home: const Scaffold(body: Text('FMCG Field Survey')),
        ),
      );
      expect(find.text('FMCG Field Survey'), findsOneWidget);
    }
  });
}