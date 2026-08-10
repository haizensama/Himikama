import 'package:flutter/material.dart';

import '../help/help_screen.dart';
import '../home/home_overview_screen.dart';

class AuthenticatedShell extends StatefulWidget {
  const AuthenticatedShell({
    required this.displayName,
    required this.email,
    required this.assessmentsPage,
    required this.onSignOut,
    this.onOpenAccountSettings,
    this.onStartAssessment,
    super.key,
    this.homePage,
    this.helpPage = const HelpScreen(),
  });

  final String displayName;
  final String email;
  final Widget assessmentsPage;
  final Future<void> Function() onSignOut;
  final Future<void> Function()? onOpenAccountSettings;
  final VoidCallback? onStartAssessment;
  final Widget? homePage;
  final Widget helpPage;

  @override
  State<AuthenticatedShell> createState() => _AuthenticatedShellState();
}

class _AuthenticatedShellState extends State<AuthenticatedShell> {
  int _selectedIndex = 0;

  static const _titles = ['Himikama', 'Assessments', 'Help'];

  void _selectTab(int index) {
    if (_selectedIndex == index) return;
    setState(() => _selectedIndex = index);
  }

  Future<void> _closeAccountSheetThen(
    BuildContext sheetContext,
    Future<void> Function() action,
  ) async {
    final sheetRoute = ModalRoute.of(sheetContext);
    Navigator.of(sheetContext).pop();
    if (sheetRoute != null) {
      await sheetRoute.completed;
    }
    if (!mounted) return;
    await action();
  }

  Future<void> _showAccount() async {
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 4, 24, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  CircleAvatar(
                    radius: 24,
                    backgroundColor: Theme.of(
                      sheetContext,
                    ).colorScheme.primaryContainer,
                    child: Icon(
                      Icons.person_outline,
                      color: Theme.of(
                        sheetContext,
                      ).colorScheme.onPrimaryContainer,
                    ),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          widget.displayName,
                          style: Theme.of(sheetContext).textTheme.titleMedium
                              ?.copyWith(fontWeight: FontWeight.w700),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          widget.email,
                          style: Theme.of(sheetContext).textTheme.bodySmall,
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 22),
              if (widget.onOpenAccountSettings != null) ...[
                FilledButton.tonalIcon(
                  key: const Key('account-privacy-settings'),
                  onPressed: () async {
                    await _closeAccountSheetThen(
                      sheetContext,
                      widget.onOpenAccountSettings!,
                    );
                  },
                  icon: const Icon(Icons.manage_accounts_outlined),
                  label: const Text('Account & Privacy'),
                ),
                const SizedBox(height: 10),
              ],
              OutlinedButton.icon(
                key: const Key('account-sign-out'),
                onPressed: () async {
                  await _closeAccountSheetThen(sheetContext, widget.onSignOut);
                },
                icon: const Icon(Icons.logout),
                label: const Text('Sign out'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final pages = <Widget>[
      widget.homePage ??
          HomeOverviewScreen(
            displayName: widget.displayName,
            onStartAssessment: widget.onStartAssessment ?? () => _selectTab(1),
          ),
      widget.assessmentsPage,
      widget.helpPage,
    ];

    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[_selectedIndex]),
        actions: [
          IconButton(
            key: const Key('account-menu-button'),
            tooltip: 'Account',
            onPressed: _showAccount,
            icon: const Icon(Icons.account_circle_outlined),
          ),
          const SizedBox(width: 4),
        ],
      ),
      body: IndexedStack(
        key: const Key('authenticated-tab-stack'),
        index: _selectedIndex,
        children: pages,
      ),
      bottomNavigationBar: NavigationBar(
        key: const Key('main-bottom-navigation'),
        selectedIndex: _selectedIndex,
        onDestinationSelected: _selectTab,
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.home_outlined),
            selectedIcon: Icon(Icons.home),
            label: 'Home',
          ),
          NavigationDestination(
            icon: Icon(Icons.assignment_outlined),
            selectedIcon: Icon(Icons.assignment),
            label: 'Assessments',
          ),
          NavigationDestination(
            icon: Icon(Icons.help_outline),
            selectedIcon: Icon(Icons.help),
            label: 'Help',
          ),
        ],
      ),
    );
  }
}
