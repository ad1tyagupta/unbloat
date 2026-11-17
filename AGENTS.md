# Instructions for coding assistants

## If a person wants to clean up their phone

Read [PROMPT.md](PROMPT.md) and follow it. It explains the session, the safety rules and the commands. Do not run raw `adb shell` commands that change the phone; everything goes through `python -m unbloat`.

## If you are changing this repository

Layout:

- `unbloat/` - the tool. `cli.py` is the entry point.
  - `adb.py`, `quoting.py` - talking to the phone. Never use a host shell; quote every phone-shell argument with `sh_quote`.
  - `paths.py`, `remote.py` - which phone paths may be deleted, checked as text and then on the phone.
  - `plan.py`, `apply.py`, `journal.py`, `backup.py`, `media.py` - plan validation and execution.
  - `storage/` - read-only storage scan and its parsers.
  - `packages/` - package list, default-app protection, never-disable list (`denylist.py`), knowledge base loader (`kb.py`).
  - `photos.py`, `pick.py`, `restore.py`, `report.py`, `doctor.py`, `connect.py` - the other commands.
  - `data/kb/*.json` - package knowledge, one file per brand family.
- `tests/` - `fakeadb.py` is a pretend phone; `fixtures/` holds output captured from emulators.

Commands:

```
python -m unittest discover -s tests -t .     # whole suite, no phone needed
python -m unbloat kb-check                    # validate the knowledge base
```

Rules: core code is Python 3.9+ standard library only (Pillow optional, photos only). No safety check may gain an off switch. Parsers of phone output must return "unknown" rather than raise. Add a fixture and a test for any new phone output you parse. Conventional commits.
