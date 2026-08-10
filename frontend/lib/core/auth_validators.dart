abstract final class AuthValidators {
  static String? displayName(String? value) {
    final name = value?.trim() ?? '';
    if (name.isEmpty) {
      return 'Enter your name.';
    }
    if (name.length < 2) {
      return 'Your name must contain at least 2 characters.';
    }
    if (name.length > 80) {
      return 'Your name must contain no more than 80 characters.';
    }
    return null;
  }

  static String? email(String? value) {
    final email = value?.trim() ?? '';
    if (email.isEmpty) {
      return 'Enter your email address.';
    }
    final emailPattern = RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$');
    if (!emailPattern.hasMatch(email)) {
      return 'Enter a valid email address.';
    }
    return null;
  }

  static String? password(String? value) {
    final password = value ?? '';
    if (password.length < 12) {
      return 'Use at least 12 characters.';
    }
    if (!RegExp('[a-z]').hasMatch(password)) {
      return 'Add at least one lowercase letter.';
    }
    if (!RegExp('[A-Z]').hasMatch(password)) {
      return 'Add at least one uppercase letter.';
    }
    if (!RegExp(r'\d').hasMatch(password)) {
      return 'Add at least one number.';
    }
    if (!RegExp(r'[^A-Za-z0-9]').hasMatch(password)) {
      return 'Add at least one symbol.';
    }
    return null;
  }

  static String? requiredPassword(String? value) {
    if ((value ?? '').isEmpty) {
      return 'Enter your password.';
    }
    return null;
  }

  static String? confirmedPassword(String? value, String password) {
    if ((value ?? '').isEmpty) {
      return 'Confirm your password.';
    }
    if (value != password) {
      return 'The passwords do not match.';
    }
    return null;
  }
}
