"""A thin, careful wrapper around the user's `adb` binary.

Rules this module keeps:
- The host never runs a shell. Commands are argv lists passed to subprocess.
- Anything sent to the phone's shell is quoted argument by argument.
- Failures are sorted into clear error types so callers can explain them.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .quoting import sh_join


class AdbError(Exception):
    """Base error. `hint` is plain-language advice for the user."""

    kind = "adb_error"

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class AdbNotFound(AdbError):
    kind = "adb_not_found"


class NoDevice(AdbError):
    kind = "no_device"


class MultipleDevices(AdbError):
    kind = "multiple_devices"


class Unauthorized(AdbError):
    kind = "unauthorized"


class DeviceOffline(AdbError):
    kind = "device_offline"


class AdbTimeout(AdbError):
    kind = "timeout"


class CommandFailed(AdbError):
    kind = "command_failed"


@dataclass
class Result:
    argv: List[str]
    code: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", "surrogateescape")

    @property
    def err(self) -> str:
        return self.stderr.decode("utf-8", "replace")

    @property
    def ok(self) -> bool:
        return self.code == 0


Runner = Callable[[List[str], float], Result]


def subprocess_runner(argv: List[str], timeout: float) -> Result:
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise AdbTimeout(
            "The phone did not answer in time.",
            "Check the cable or Wi-Fi, make sure the phone is unlocked, then try again.",
        )
    except FileNotFoundError:
        raise AdbNotFound("Could not start adb at %s." % argv[0], "Run: python -m unbloat doctor")
    return Result(list(argv), proc.returncode, proc.stdout, proc.stderr)


def find_adb(explicit: Optional[str] = None) -> Optional[str]:
    """Locate adb without guessing wildly. Returns None when it is not found."""
    exe = "adb.exe" if os.name == "nt" else "adb"
    candidates: List[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("UNBLOAT_ADB")
    if env:
        candidates.append(env)
    on_path = shutil.which("adb")
    if on_path:
        candidates.append(on_path)
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(var)
        if root:
            candidates.append(os.path.join(root, "platform-tools", exe))
    home = os.path.expanduser("~")
    candidates.append(os.path.join(os.getcwd(), "platform-tools", exe))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(os.path.join(local, "Android", "Sdk", "platform-tools", exe))
        candidates.append(os.path.join(home, "platform-tools", exe))
        candidates.append(os.path.join("C:\\", "platform-tools", exe))
    elif sys.platform == "darwin":
        candidates.append(os.path.join(home, "Library", "Android", "sdk", "platform-tools", exe))
        candidates.append("/opt/homebrew/bin/adb")
        candidates.append("/opt/homebrew/share/android-commandlinetools/platform-tools/adb")
        candidates.append("/usr/local/bin/adb")
        candidates.append("/usr/local/share/android-commandlinetools/platform-tools/adb")
    else:
        candidates.append(os.path.join(home, "Android", "Sdk", "platform-tools", exe))
        candidates.append("/usr/bin/adb")
        candidates.append("/usr/lib/android-sdk/platform-tools/adb")
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


_TRANSIENT = (
    "device offline",
    "error: closed",
    "protocol fault",
    "connection reset",
    "broken pipe",
    "device still authorizing",
    "device still connecting",
)


def _classify(stderr: str, stdout: str) -> Optional[AdbError]:
    """Turn adb's own error text into a typed error, or None if it is not one."""
    blob = (stderr + "\n" + stdout).lower()
    if "more than one device" in blob:
        return MultipleDevices(
            "More than one phone is connected.",
            "Run: python -m unbloat connect   then pass --serial with the one you want.",
        )
    if "unauthorized" in blob:
        return Unauthorized(
            "The phone has not allowed this computer yet.",
            "Unlock the phone and tap Allow on the 'Allow USB debugging?' box.",
        )
    if "no devices/emulators found" in blob or ("device '" in blob and "not found" in blob):
        return NoDevice(
            "No phone is connected.",
            "Plug the phone in, unlock it, and run: python -m unbloat connect",
        )
    for marker in _TRANSIENT:
        if marker in blob:
            return DeviceOffline(
                "The connection to the phone dropped.",
                "Unlock the phone, check the cable or Wi-Fi, then run the command again.",
            )
    return None


class Adb:
    def __init__(
        self,
        adb_path: Optional[str] = None,
        serial: Optional[str] = None,
        runner: Optional[Runner] = None,
        timeout: float = 60.0,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._runner = runner or subprocess_runner
        self._explicit_path = adb_path
        self._path: Optional[str] = adb_path if runner else None
        self.serial = serial
        self.timeout = timeout
        self.retries = retries
        self._sleep = sleep

    # -- binary -----------------------------------------------------------
    @property
    def path(self) -> str:
        if self._path is None:
            found = find_adb(self._explicit_path)
            if not found:
                raise AdbNotFound(
                    "adb is not installed on this computer.",
                    "Run: python -m unbloat doctor   for install steps.",
                )
            self._path = found
        return self._path

    # -- low level --------------------------------------------------------
    def host(self, args: Sequence[str], timeout: Optional[float] = None) -> Result:
        """Run an adb command that is not aimed at one device (devices, pair...)."""
        return self._runner([self.path] + list(args), timeout or self.timeout)

    def device(
        self,
        args: Sequence[str],
        timeout: Optional[float] = None,
        retry: bool = True,
    ) -> Result:
        """Run an adb command against the chosen device, with typed errors."""
        serial = self.require_serial()
        argv = [self.path, "-s", serial] + list(args)
        attempts = (self.retries + 1) if retry else 1
        last: Optional[AdbError] = None
        for attempt in range(attempts):
            try:
                res = self._runner(argv, timeout or self.timeout)
            except AdbTimeout as exc:
                last = exc
            else:
                err = None
                if res.code != 0 or res.stderr:
                    head = res.err.lstrip().lower()
                    if head.startswith(("error:", "adb:", "adb.exe:", "* daemon")) or res.code == 255:
                        err = _classify(res.err, "")
                if err is None:
                    return res
                if not isinstance(err, DeviceOffline):
                    raise err
                last = err
            if attempt + 1 < attempts:
                self._sleep(1.5 * (attempt + 1))
        assert last is not None
        raise last

    def shell(
        self,
        args: Sequence[str],
        timeout: Optional[float] = None,
        retry: bool = True,
    ) -> Result:
        """Run one command on the phone. Every argument is quoted."""
        return self.device(["shell", sh_join(args)], timeout=timeout, retry=retry)

    def shell_script(self, script: str, timeout: Optional[float] = None) -> Result:
        """Run a fixed script. Only for constant text written in this repo;
        never build `script` from anything that came from the phone."""
        return self.device(["shell", script], timeout=timeout)

    def shell_text(self, args: Sequence[str], timeout: Optional[float] = None) -> str:
        """Output of a read-only command, or '' if it failed."""
        try:
            res = self.shell(args, timeout=timeout)
        except (DeviceOffline, AdbTimeout, NoDevice, Unauthorized, MultipleDevices):
            raise
        except AdbError:
            return ""
        return res.text if res.code == 0 else ""

    def pull(self, remote: str, local: str, timeout: float = 600.0) -> Result:
        """Copy one file from the phone. Uses the sync protocol, not the shell."""
        return self.device(["pull", "-a", remote, local], timeout=timeout)

    # -- devices ----------------------------------------------------------
    def devices(self) -> List[Dict[str, str]]:
        res = self.host(["devices", "-l"], timeout=20)
        out: List[Dict[str, str]] = []
        for line in res.text.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices") or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            entry = {"serial": parts[0], "state": parts[1]}
            # "no permissions (...)" on Linux spans several words
            if parts[1] == "no":
                entry["state"] = "no permissions"
            for token in parts[2:]:
                if ":" in token:
                    key, _, value = token.partition(":")
                    if key in ("model", "device", "product", "transport_id", "usb"):
                        entry[key] = value
            out.append(entry)
        return out

    def require_serial(self) -> str:
        """The serial to talk to. Never guesses between several devices."""
        if self.serial:
            return self.serial
        env = os.environ.get("ANDROID_SERIAL")
        if env:
            self.serial = env
            return env
        devs = self.devices()
        if not devs:
            raise NoDevice(
                "No phone is connected.",
                "Plug the phone in, unlock it, and run: python -m unbloat connect",
            )
        if len(devs) > 1:
            names = ", ".join("%s (%s)" % (d["serial"], d.get("model", d["state"])) for d in devs)
            raise MultipleDevices(
                "More than one phone is connected: %s." % names,
                "Add --serial <one of them> to the command.",
            )
        only = devs[0]
        if only["state"] == "unauthorized":
            raise Unauthorized(
                "The phone has not allowed this computer yet.",
                "Unlock the phone and tap Allow on the 'Allow USB debugging?' box.",
            )
        if only["state"] != "device":
            raise DeviceOffline(
                "The phone is connected but not ready (state: %s)." % only["state"],
                "Unplug and replug the cable, unlock the phone, then try again.",
            )
        self.serial = only["serial"]
        return self.serial
