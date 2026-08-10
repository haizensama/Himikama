import 'dart:convert';
import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

class PendingAttemptReference {
  const PendingAttemptReference({
    required this.ownerUid,
    required this.attemptId,
  });

  final String ownerUid;
  final String attemptId;
}

abstract interface class PendingAttemptStore {
  Future<PendingAttemptReference?> readForUser(String ownerUid);

  Future<void> save(PendingAttemptReference reference);

  Future<void> clear({required String ownerUid, required String attemptId});
}

class SharedPreferencesPendingAttemptStore implements PendingAttemptStore {
  SharedPreferencesPendingAttemptStore({SharedPreferencesAsync? preferences})
    : _preferences = preferences ?? SharedPreferencesAsync();

  static const _storageKey = 'himikama.pending_attempt.v1';

  final SharedPreferencesAsync _preferences;

  @override
  Future<PendingAttemptReference?> readForUser(String ownerUid) async {
    final encoded = await _preferences.getString(_storageKey);
    if (encoded == null || encoded.isEmpty) return null;
    try {
      final decoded = jsonDecode(encoded);
      if (decoded is! Map) return null;
      final storedOwner = decoded['owner_uid']?.toString() ?? '';
      final attemptId = decoded['attempt_id']?.toString() ?? '';
      if (storedOwner != ownerUid || !_looksLikeUuid(attemptId)) return null;
      return PendingAttemptReference(
        ownerUid: storedOwner,
        attemptId: attemptId,
      );
    } on FormatException {
      return null;
    }
  }

  @override
  Future<void> save(PendingAttemptReference reference) async {
    if (reference.ownerUid.trim().isEmpty ||
        !_looksLikeUuid(reference.attemptId)) {
      throw ArgumentError('Pending attempt reference is invalid.');
    }
    await _preferences.setString(
      _storageKey,
      jsonEncode({
        'owner_uid': reference.ownerUid,
        'attempt_id': reference.attemptId,
      }),
    );
  }

  @override
  Future<void> clear({
    required String ownerUid,
    required String attemptId,
  }) async {
    final current = await readForUser(ownerUid);
    if (current?.attemptId == attemptId) {
      await _preferences.remove(_storageKey);
    }
  }
}

class MemoryPendingAttemptStore implements PendingAttemptStore {
  MemoryPendingAttemptStore([this.value]);

  PendingAttemptReference? value;

  @override
  Future<PendingAttemptReference?> readForUser(String ownerUid) async {
    return value?.ownerUid == ownerUid ? value : null;
  }

  @override
  Future<void> save(PendingAttemptReference reference) async {
    value = reference;
  }

  @override
  Future<void> clear({
    required String ownerUid,
    required String attemptId,
  }) async {
    if (value?.ownerUid == ownerUid && value?.attemptId == attemptId) {
      value = null;
    }
  }
}

String createAttemptUuid() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  final hex = bytes
      .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
      .join();
  return '${hex.substring(0, 8)}-'
      '${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-'
      '${hex.substring(16, 20)}-'
      '${hex.substring(20)}';
}

bool _looksLikeUuid(String value) {
  return RegExp(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-'
    r'[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$',
  ).hasMatch(value);
}
