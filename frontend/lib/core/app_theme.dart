import 'package:flutter/material.dart';

abstract final class AppPalette {
  static const primary = Color(0xFF0B2948);
  static const primaryDark = Color(0xFF071F38);
  static const primaryContainer = Color(0xFFE6EDF3);
  static const onPrimaryContainer = Color(0xFF102F4D);
  static const gold = Color(0xFFB77916);
  static const goldContainer = Color(0xFFF6E8C9);
  static const scaffold = Color(0xFFF8F6F1);
  static const surface = Color(0xFFFFFFFF);
  static const outline = Color(0xFFD9DEE3);
  static const outlineVariant = Color(0xFFE6E9EC);
  static const text = Color(0xFF17212B);
  static const textMuted = Color(0xFF5E6872);

  static const success = Color(0xFF2F6B55);
  static const successContainer = Color(0xFFE2F0E9);
  static const warning = Color(0xFF8A5C12);
  static const warningContainer = Color(0xFFF8EBCF);
  static const danger = Color(0xFFA13D3D);
  static const neutral = Color(0xFF5E6872);
  static const neutralContainer = Color(0xFFEDF0F2);
}

abstract final class AppTheme {
  static ThemeData get light {
    final generated = ColorScheme.fromSeed(
      seedColor: AppPalette.primary,
      brightness: Brightness.light,
    );
    final colorScheme = generated.copyWith(
      primary: AppPalette.primary,
      onPrimary: Colors.white,
      primaryContainer: AppPalette.primaryContainer,
      onPrimaryContainer: AppPalette.onPrimaryContainer,
      secondary: AppPalette.gold,
      onSecondary: Colors.white,
      secondaryContainer: AppPalette.goldContainer,
      onSecondaryContainer: const Color(0xFF4C3105),
      tertiary: AppPalette.success,
      tertiaryContainer: AppPalette.successContainer,
      surface: AppPalette.surface,
      onSurface: AppPalette.text,
      onSurfaceVariant: AppPalette.textMuted,
      outline: AppPalette.outline,
      outlineVariant: AppPalette.outlineVariant,
      error: AppPalette.danger,
    );

    const roundedRectangle = RoundedRectangleBorder(
      borderRadius: BorderRadius.all(Radius.circular(12)),
    );
    final baseTextTheme = ThemeData.light(useMaterial3: true).textTheme;

    return ThemeData(
      colorScheme: colorScheme,
      useMaterial3: true,
      scaffoldBackgroundColor: AppPalette.scaffold,
      textTheme: baseTextTheme.copyWith(
        headlineLarge: baseTextTheme.headlineLarge?.copyWith(
          color: AppPalette.text,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.6,
        ),
        headlineMedium: baseTextTheme.headlineMedium?.copyWith(
          color: AppPalette.text,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.4,
        ),
        headlineSmall: baseTextTheme.headlineSmall?.copyWith(
          color: AppPalette.text,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.2,
        ),
        titleLarge: baseTextTheme.titleLarge?.copyWith(
          color: AppPalette.text,
          fontWeight: FontWeight.w700,
        ),
        titleMedium: baseTextTheme.titleMedium?.copyWith(
          color: AppPalette.text,
          fontWeight: FontWeight.w600,
        ),
        bodyLarge: baseTextTheme.bodyLarge?.copyWith(
          color: AppPalette.text,
          height: 1.45,
        ),
        bodyMedium: baseTextTheme.bodyMedium?.copyWith(
          color: AppPalette.text,
          height: 1.4,
        ),
        bodySmall: baseTextTheme.bodySmall?.copyWith(
          color: AppPalette.textMuted,
          height: 1.35,
        ),
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: AppPalette.scaffold,
        foregroundColor: AppPalette.primary,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        centerTitle: false,
        titleTextStyle: TextStyle(
          color: AppPalette.primary,
          fontSize: 20,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.2,
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppPalette.outline),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppPalette.outline),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppPalette.primary, width: 2),
        ),
        filled: true,
        fillColor: AppPalette.surface,
      ),
      cardTheme: const CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        color: AppPalette.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(16)),
          side: BorderSide(color: AppPalette.outline),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(52),
          shape: roundedRectangle,
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size.fromHeight(50),
          shape: roundedRectangle,
          side: const BorderSide(color: AppPalette.primary),
          textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
        ),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: colorScheme.surfaceContainerLow,
        side: const BorderSide(color: AppPalette.outline),
        shape: const StadiumBorder(),
        labelStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
      dividerTheme: const DividerThemeData(color: AppPalette.outline),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppPalette.primary,
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: AppPalette.surface,
        indicatorColor: AppPalette.primaryContainer,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        labelTextStyle: WidgetStateProperty.resolveWith(
          (states) => TextStyle(
            color: states.contains(WidgetState.selected)
                ? AppPalette.primaryDark
                : AppPalette.neutral,
            fontWeight: states.contains(WidgetState.selected)
                ? FontWeight.w700
                : FontWeight.w500,
          ),
        ),
      ),
    );
  }
}
