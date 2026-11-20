// Every dropdown has a search box (widgets/search_dropdown.dart).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/widgets/search_dropdown.dart';

void main() {
  Future<String?> pump(WidgetTester tester, {String? initial}) async {
    String? picked;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: StatefulBuilder(
          builder: (context, setState) => SearchDropdownField<String>(
            initialValue: initial,
            decoration: const InputDecoration(labelText: 'City'),
            items: [
              for (final c in ['Greater Noida', 'Noida', 'Hyderabad', 'Pune', 'Delhi', 'Mumbai'])
                DropdownMenuItem(value: c, child: Text(c)),
              const DropdownMenuItem(value: 'x', enabled: false, child: Row(children: [Icon(Icons.block), Text('Closed city')])),
            ],
            onChanged: (v) => setState(() => picked = v),
          ),
        ),
      ),
    ));
    return picked;
  }

  testWidgets('shows the chosen value and searches the list', (tester) async {
    await pump(tester, initial: 'Pune');
    expect(find.text('Pune'), findsOneWidget);

    await tester.tap(find.byType(SearchDropdownField<String>));
    await tester.pumpAndSettle();
    expect(find.text('Search…'), findsOneWidget);
    expect(find.byType(ListTile), findsNWidgets(7));

    await tester.enterText(find.byType(TextField), 'noi');
    await tester.pumpAndSettle();
    expect(find.byType(ListTile), findsNWidgets(2), reason: 'Greater Noida and Noida');

    await tester.tap(find.widgetWithText(ListTile, 'Greater Noida'));
    await tester.pumpAndSettle();
    expect(find.text('Search…'), findsNothing, reason: 'the sheet closed');
    expect(find.text('Greater Noida'), findsOneWidget, reason: 'the field shows the choice');
  });

  testWidgets('no match, and items inside rows are searchable', (tester) async {
    await pump(tester);
    await tester.tap(find.byType(SearchDropdownField<String>));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'zzz');
    await tester.pumpAndSettle();
    expect(find.textContaining('No match'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'closed');
    await tester.pumpAndSettle();
    expect(find.byType(ListTile), findsOneWidget);
    expect(tester.widget<ListTile>(find.byType(ListTile)).enabled, isFalse);
  });

  test('item text for searching', () {
    expect(dropdownItemText(const Text('Survey 1 · v2')), 'Survey 1 · v2');
    expect(dropdownItemText(const Padding(padding: EdgeInsets.zero, child: Text('Delhi'))), 'Delhi');
    expect(dropdownItemText(const Row(children: [Icon(Icons.star), Text('A'), Text('B')])), 'A B');
  });
}
