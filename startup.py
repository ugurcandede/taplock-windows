"""Launch at login -- the Windows answer to macOS's SMAppService.

A value under HKCU's Run key, which is the documented per-user autostart hook
and needs no elevation. Frozen builds register their own executable; running
from source registers pythonw.exe so no console window appears at login.
"""

import sys
import winreg
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "TapLock"


def _command():
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return f'"{executable}"'
    # pythonw.exe sits next to python.exe in the same environment and runs
    # without a console; fall back to whatever we were started with.
    windowless = executable.with_name("pythonw.exe")
    launcher = windowless if windowless.exists() else executable
    return f'"{launcher}" "{Path(sys.argv[0]).resolve()}"'


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE)
            return True
    except OSError:
        return False


def set_enabled(enabled):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE)
                except FileNotFoundError:
                    pass
    except OSError:
        # Not worth taking the app down: the toggle simply does not stick.
        pass
