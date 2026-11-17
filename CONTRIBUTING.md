# Contributing

Thanks for helping. The most useful contributions are package knowledge for phones we have not tested and captured command output from real devices.

## Running the tests

No phone needed. A fake adb layer replays output captured from real devices.

```
python -m unittest discover -s tests -t .
python -m unbloat kb-check
```

Photo tests also run if Pillow is installed (`python -m pip install Pillow`). The emulator end-to-end test runs only when `UNBLOAT_E2E_SERIAL` names a connected emulator; see `tests/test_e2e_emulator.py`.

The core must stay standard-library only and run on Python 3.9 on macOS, Linux and Windows. Never run commands through a host shell; never build a phone shell command from phone-sourced text without `sh_quote`.

## Adding or fixing package knowledge

Package knowledge lives in `unbloat/data/kb/<family>.json`, one file per brand family: `common`, `samsung`, `xiaomi`, `oppo`, `vivo`, `motorola`, `pixel`, `nothing`. Write entries yourself from what you know and what you see on your phone. Do not copy lists from other debloat projects; their licences may not allow it.

Each file:

```json
{
  "family": "samsung",
  "description": "Samsung Galaxy phones and tablets running One UI.",
  "packages": [
    {
      "package": "com.samsung.android.app.tips",
      "name": "Tips",
      "what": "Samsung's how-to guide.",
      "tier": "safe",
      "breaks": "Only this app goes away until it is switched back on.",
      "applies_to": "any"
    }
  ]
}
```

Fields:

- `package`: exact package name.
- `name`: what the user would call it.
- `what`: one plain sentence on what it does.
- `tier`: `safe`, `caution`, `risky` or `never`.
  - `safe`: switching it off loses only that app.
  - `caution`: something else stops working; `breaks` must say what.
  - `risky`: could break calls, messages, updates, security, sign-in or the camera. Only switched off when the user names it.
  - `never`: must not be switched off.
- `breaks`: what the user notices once it is off, in plain words.
- `applies_to`: Android or skin versions it applies to, or `any`.

Rules the validator enforces (`python -m unbloat kb-check`): valid JSON, known keys only, no duplicates within or across files, and any package matched by the built-in never-disable list (`unbloat/packages/denylist.py`) must be tier `never`.

Be conservative. If you have not switched it off on a real phone and lived with it for a few days, it is `caution` at most. In your pull request, say which phone model and software version you tested on.

Packages starting with `com.android.` are treated as `never` unless they have a reviewed entry, because they are base Android.

## Capturing fixtures from a real phone

If a scan misreads your phone, the best report is the raw output. These commands only read:

```
adb shell getprop > getprop.txt
adb shell dumpsys diskstats > diskstats.txt
adb shell dumpsys usagestats | head -300 > usagestats.txt
adb shell pm list users > users.txt
adb shell df -k /storage/emulated/0 > df.txt
adb shell dumpsys role > role.txt
```

Check them for anything personal before sharing (usage stats list app names and times; getprop includes the serial number), then add them under `tests/fixtures/<brand>-<model>-api<level>/` with a short README saying where they came from, and a test that parses them.

## Commit style

Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
