# Himikama Flutter frontend

The authenticated mobile client for Himikama's guided Sri Lankan Fundamental
Rights assessment flow.

## Run with the local backend

Start FastAPI on the host at `127.0.0.1:8000`, then run:

```bash
flutter pub get
flutter run -d emulator-5554
```

Android debug builds use `http://10.0.2.2:8000` by default, so no API URL
override is needed for the standard emulator setup.

## Developer diagnostics

Backend health, Firebase UID, and attempt-ownership controls are hidden in the
normal interface. Enable them only for a diagnostic run:

```bash
flutter run -d emulator-5554 \
  --dart-define=SHOW_DEVELOPER_TOOLS=true
```

Firebase App Check remains disabled for ordinary local debug runs unless
`APP_CHECK_ENABLED=true` is supplied and the emulator's debug token has been
registered.

## Checks

```bash
flutter analyze
flutter test
```
