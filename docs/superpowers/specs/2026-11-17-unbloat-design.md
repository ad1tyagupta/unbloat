# unbloat design

Date: 2026-11-17. Approved by the project owner in the kickoff session.

## Purpose

A toolkit that lets a non-technical person clean an Android phone from a
computer while an AI coding assistant runs the commands. Two jobs: free up
storage, and switch off preinstalled bloatware. Everything goes over ADB. No
root, nothing installed on the phone.

## The split that everything follows from

1. Scripts gather facts. Read-only commands, structured JSON out.
2. The assistant judges. It reads the JSON and writes a plan file.
3. The user approves, batch by batch.
4. Scripts execute the plan. Every action is checked against the safety rules
   in code and written to a journal.

The safety rules are code, not prompt text. There is no flag that disables
them. A plan edited by hand goes through the same checks, and the approval
code printed by the dry run stops matching once the plan changes.

## Decisions

- Python 3.9+, standard library only. Pillow is optional, photos only.
- Hostile file names: every argument sent to the phone's shell is wrapped in
  single quotes with embedded quotes escaped, and sent as one argv element, so
  the host never runs a shell. File copies use `adb pull`, which does not
  involve the phone's shell at all. Rejected alternatives: pushing a helper
  script to the phone (the brief says nothing is installed on the phone) and
  one adb call per file with no batching (too slow for 50,000 files).
- One recursive listing of shared storage drives every storage finding. The
  analysis happens on the computer.
- Deletion is pull, verify size and SHA-256, then delete, then confirm gone.
- Debloat uses `pm disable-user --user 0` only, in small batches with a health
  check and automatic rollback.
- Package knowledge is JSON data, one file per brand family, written from
  scratch and validated in tests.
- Tests use the standard `unittest` runner so contributors need nothing extra.

## Testing

- A fake adb layer for the whole suite, with fixtures captured from devices.
- Android emulator for destructive end-to-end checks.
- One Samsung phone for read-only checks.
- GitHub Actions on macOS, Linux and Windows.

## Build order

Storage first, debloat after, docs and CI last. One commit per stage.
