# unbloat

Clean up your Android phone from your computer, with an AI assistant doing the work and you making every decision.

unbloat does two things:

- **Frees up storage.** It finds what is using space: app caches, duplicate files, large videos, forgotten downloads, leftover app installers, WhatsApp and Telegram media, screenshots, and (if you ask) blurry or near-identical photos. It removes only what you approve, and keeps a copy on your computer first.
- **Switches off bloatware.** It switches off preinstalled apps from the phone maker or carrier that you do not want, such as ad services and duplicate app stores. Switching off is reversible with one command. Nothing is uninstalled.

Everything runs over a USB cable (or Wi-Fi) from your computer. No root, nothing installed on the phone.

## What it does not do

- Uninstall apps, root the phone, or touch apps' private data and logins.
- Block ads on websites or in apps.
- Work on iPhones.
- Change anything without asking you first.

## What you need

- An Android phone running Android 8 or newer, and a USB data cable.
- A Mac, Windows or Linux computer with Python 3.9 or newer.
- Android's free **platform-tools** (the `adb` program). `python -m unbloat doctor` tells you how to install it.
- A coding assistant that can run commands in a folder: Claude Code, Codex, Gemini CLI, Cursor or similar. (Or follow the by-hand section below.)
- Free space on your computer for copies of anything you delete.

## Quick start

1. **Get the code.** Download this repository (green "Code" button, then "Download ZIP", and unzip it) or `git clone https://github.com/ad1tyagupta/unbloat`.
2. **Open the folder in your coding assistant** and give it this message:
   > Read PROMPT.md and follow it to help me clean up my Android phone.
3. **Plug in your phone** and answer the assistant's questions. It will walk you through turning on USB debugging.

The assistant scans, explains what it found in plain language, and asks before every batch. You will see the size and examples of what would go before anything happens.

## How your files are protected

- Scans only read. Nothing changes until you approve a plan.
- Every deletion is checked in code: it must be an exact file inside the phone's shared storage (the part you see in a file manager), never inside apps' private folders, never a link pointing elsewhere.
- Before a file is deleted, it is copied to your computer and the copy is checked byte for byte. If the copy fails, the file is not deleted.
- Photos, the camera folder and messaging-app media are never removed unless you were shown the count, total size and examples.
- Core parts of Android (phone, messages, settings, Play Store, your current keyboard and home screen, and many more) can never be switched off, whatever anyone asks.
- Every action is written to a journal with the time and exact command.

Copies of deleted files are kept in `unbloat-data/<phone>/backups/files/` inside this folder.

## Undo

Switch back on every app unbloat switched off:

```
python -m unbloat restore            # shows what it would do
python -m unbloat restore --confirm  # does it
```

To get a deleted file back, copy it from `unbloat-data/<phone>/backups/files/...` to the phone with your file manager, or with `adb push <file> /sdcard/<folder>/`. Each backup folder has a `manifest.jsonl` that lists where every file came from.

## Doing it by hand (no assistant)

```
python -m unbloat doctor                       # check your computer
python -m unbloat connect                      # connect and describe the phone
python -m unbloat backup                       # record the phone's current state
python -m unbloat scan storage                 # what is using space (read-only)
python -m unbloat pick installers --plan plan.json
python -m unbloat pick caches --plan plan.json
python -m unbloat plan plan.json               # dry run: shows exactly what would happen
python -m unbloat apply plan.json --confirm <code printed by the dry run>
python -m unbloat report                       # before and after
python -m unbloat scan packages                # bloatware, with a risk rating for each app
```

`pick` takes a group name from the storage scan's list. For apps, write a plan by hand following the format at the end of [PROMPT.md](PROMPT.md). Run any command with `--help` for its options.

Useful raw `adb` commands if you want to look for yourself (these only read):

```
adb devices
adb shell df -h /storage/emulated/0
adb shell pm list packages -d --user 0         # apps currently switched off
adb shell pm enable --user 0 <package>         # switch one app back on
```

## Risks, honestly

- **Deleted files are gone unless they were backed up.** unbloat backs up by default. If you choose to delete without a copy (it asks twice), there is no undo.
- **Switching off the wrong app can break something.** Each app is rated safe, caution, risky or never, and the assistant only suggests safe ones freely. Ratings are written by hand and can be wrong for your phone model, especially brands that have not been tested (see below).
- **Phone makers sometimes switch apps back on after a system update.** Run `scan packages` after an update to see what came back.

**If the phone misbehaves after switching apps off:**

1. Run `python -m unbloat restore --confirm`.
2. If the computer cannot reach the phone, restart the phone in safe mode (on most phones: hold the power button, then press and hold "Power off" until "Safe mode" appears). In safe mode, go to Settings > Apps, show system apps, and re-enable recently disabled ones, or connect and run `restore`.
3. Worst case, a factory reset restores every app, but erases the phone. Back up first if you can.

## What has been tested

Tested on Android emulators (Google's virtual phones) running Android 8.0, 10 and 14: scanning, deleting files with awkward names after a verified backup, clearing caches, switching apps off and back on, and refusing unsafe plans. The automated tests replay output captured from those emulators.

**Not yet tested on a real phone of any brand.** The bloatware lists for Samsung, Xiaomi, Oppo/realme/OnePlus, Vivo, Motorola, Pixel and Nothing are written from general knowledge, not checked on those phones, so they are deliberately cautious. Reports from real devices are very welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

## FAQ

**Will I lose my WhatsApp chats?** No. unbloat can remove media files WhatsApp saved to shared storage (if you approve), never the chats, which live in WhatsApp's private storage.

**Is clearing caches safe?** Yes. It asks Android to clear temporary files apps can rebuild. Logins and app data are not touched. Apps may load a bit slower the first time.

**Why not just uninstall bloatware?** Switching off is undone with one command, so a wrong guess costs little. Uninstalling system apps over adb is not reliably reversible, so unbloat never does it.

**Does my AI provider see my files?** It sees file names, sizes and app names from the scans, because that is how it helps. It sees photos only if you agree to photo review.

**How much space will I get back?** It depends on the phone. The report shows the amount measured on the phone, not an estimate.

**The phone shows a box asking to "Allow USB debugging?"** Tap Allow. Tick "Always allow from this computer" if it is your own computer.

## License

MIT. See [LICENSE](LICENSE).
