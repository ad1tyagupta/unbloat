# Security

unbloat runs commands on a person's phone, so some bugs are safety bugs. Please report privately anything that could:

- delete a file outside the phone's shared storage, or a file not listed in an approved plan;
- run a command on the phone that was not written by unbloat (for example, through a crafted file name);
- switch off a package on the never-disable list, or the current keyboard, home screen, phone or messages app;
- apply a plan without a matching approval code, or delete without a verified backup when one was required;
- write outside the chosen backup folder on the computer.

Report through GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability"). Please include the plan file or scan output that triggers it, with personal details removed. You will get a reply within a week.

## Design notes for reviewers

- The host never runs a shell. `adb` is called with an argument list.
- Every argument sent to the phone's shell is wrapped in single quotes (`unbloat/quoting.py`), tested against hostile names on Android 8, 10 and 14 emulators.
- Paths are checked twice: as text (`unbloat/paths.py`), then on the phone just before acting (exact type, size, modified time, and that the path resolves to itself).
- Chat history backups in shared storage (WhatsApp, Signal, Telegram, Viber) are never deleted.
- After each small group of apps is switched off, the phone is checked for a working home screen, keyboard, phone app, messages app and screen software; any failure switches the group back on and stops.
- The only state-changing package command is `pm disable-user --user 0`, undone with `pm enable --user 0`.
- Text from the phone is escaped before display (`unbloat/util.py:show`) and treated as data.
