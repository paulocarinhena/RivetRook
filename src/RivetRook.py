#!/usr/bin/env python3
"""RivetRook — AI toolchain manager / Gerenciador de ferramentas de IA.

Install, update, uninstall and configure popular AI coding tools (Claude,
Codex, Gemini, OpenCode, Cline, …) from a single interactive CLI menu.

Instala, atualiza, desinstala e configura ferramentas populares de IA
(Claude, Codex, Gemini, OpenCode, Cline, …) a partir de um menu interativo.

Platforms / Plataformas: Windows, macOS, Linux
Requires / Requer: Python 3.8+, Node.js 20+ (auto-installed)
Config: src/config.json (tools, i18n strings, prerequisite)
Author: Paulo Carinhena (https://github.com/paulocarinhena)
"""

import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import which
from typing import Callable, Dict, List, Optional, Tuple


# ─── Terminal / ANSI ──────────────────────────────────────────────────────────


def _enable_win_ansi() -> bool:
    try:
        import ctypes

        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
        return True
    except Exception:
        return False


# Set to True when an install places a binary outside the parent shell's PATH.
# Checked at exit to guarantee the shell is reloaded before leaving the installer.
_pending_reload: bool = False

# Windows: suppress the console window flash when spawning PowerShell for
# captured/background commands (e.g. parallel version probes). Live/interactive
# commands must NOT use this — they need a visible attached console.
_WIN_NO_WINDOW: int = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)

# Windows: prefer PowerShell 7+ (pwsh) over the legacy Windows PowerShell 5.1.
# Some install scripts (e.g. claude) use cmdlets like Get-FileHash that may be
# unavailable on older Windows PowerShell versions.
_PS_EXE: str = "pwsh" if which("pwsh") else "powershell"

_USE_COLOR: bool = (
    os.environ.get("NO_COLOR", "") == ""
    and os.environ.get("TERM", "x") != "dumb"
    and hasattr(sys.stdout, "isatty")
    and sys.stdout.isatty()
    and (sys.platform != "win32" or _enable_win_ansi())
)


class _A:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BRED = "\033[91m"
    BGREEN = "\033[92m"
    BYELLOW = "\033[93m"
    BBLUE = "\033[94m"
    BMAGENTA = "\033[95m"
    BCYAN = "\033[96m"
    BWHITE = "\033[97m"


def _c(code: str, text: str) -> str:
    return "{}{}{}".format(code, text, _A.RESET) if _USE_COLOR else text


def _ok(t: str) -> str:
    return _c(_A.BGREEN, t)


def _err(t: str) -> str:
    return _c(_A.BRED, t)


def _warn(t: str) -> str:
    return _c(_A.BYELLOW, t)


def _info(t: str) -> str:
    return _c(_A.BCYAN, t)


def _dim(t: str) -> str:
    return _c(_A.DIM, t)


def _bold(t: str) -> str:
    return _c(_A.BOLD, t)


def _hi(t: str) -> str:
    return _c(_A.BMAGENTA, t)


def _blue(t: str) -> str:
    return _c(_A.BBLUE, t)


def _strip_ansi(s: str) -> str:
    return re.sub(r"\033\[[^m]*m", "", s)


def _press_any_key() -> None:
    """Print the 'done, press any key' prompt and wait for a single keystroke."""
    print("\n  {} {}".format(_ok("✔"), _t("press_key")))
    if sys.platform == "win32":
        try:
            import msvcrt
            msvcrt.getch()
            return
        except Exception:
            pass
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def _vlen(s: str) -> int:
    """Visible length of a string (ignores ANSI escape codes)."""
    return len(_strip_ansi(s))


def _ljust(s: str, width: int, fill: str = " ") -> str:
    return s + fill * max(0, width - _vlen(s))


def _rjust(s: str, width: int, fill: str = " ") -> str:
    return fill * max(0, width - _vlen(s)) + s


# ─── Spinner (thread) ─────────────────────────────────────────────────────────

_SPINNER = (
    ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    if _USE_COLOR
    else ["|", "/", "-", "\\"]
)


def _run_spinner(label: str, stop: threading.Event) -> None:
    i = 0
    while not stop.is_set():
        f = _SPINNER[i % len(_SPINNER)]
        if _USE_COLOR:
            print(
                "\r  {} {}".format(_c(_A.BCYAN, f), _dim(label + "…")),
                end="",
                flush=True,
            )
        else:
            print("\r  [{}] {}...".format(f, label), end="", flush=True)
        i += 1
        time.sleep(0.09)
    print("\r" + " " * (_vlen(label) + 12) + "\r", end="", flush=True)


# ─── Config ───────────────────────────────────────────────────────────────────


def load_config(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── i18n ─────────────────────────────────────────────────────────────────────

_STRINGS: Dict = {}
_CURRENT_LANG: str = "pt-br"


def _t(key: str, *args) -> str:
    """Return the translated string for key, formatting with args if provided."""
    s = _STRINGS.get(key, key)
    if args:
        return s.format(*args)
    return s


def _desc(block: Dict) -> str:
    """Return the tool description in the current language."""
    if _CURRENT_LANG == "en":
        return block.get("description_en", block.get("description", ""))
    return block.get("description", "")


def _prompt_text(cfg: Dict) -> str:
    """Return the configure prompt in the current language."""
    if _CURRENT_LANG == "en":
        return cfg.get("prompt_en", cfg.get("prompt", _t("paste_api_key")))
    return cfg.get("prompt", _t("paste_api_key"))


def ask_language(config: Dict) -> str:
    """Ask user to choose language at startup. Returns 'pt-br' or 'en'."""
    global _STRINGS, _CURRENT_LANG
    i18n = config.get("i18n", {})
    pt = i18n.get("pt-br", {})

    print()
    print("  {} {}".format(
        _bold("?"),
        pt.get("lang_prompt", "Escolha o idioma / Choose language:")
    ))
    print("    1. {}".format(pt.get("lang_option_pt", "Português (Brasil)")))
    print("    2. {}".format(pt.get("lang_option_en", "English")))

    while True:
        raw = input("\n  {} (1/2): ".format(_bold("›"))).strip()
        if raw in ("1", ""):
            _CURRENT_LANG = "pt-br"
            _STRINGS = pt
            return "pt-br"
        if raw == "2":
            _CURRENT_LANG = "en"
            _STRINGS = i18n.get("en", {})
            return "en"
        print("  {} Invalid / Inválido.".format(_warn("⚠")))


# ─── OS Detection ─────────────────────────────────────────────────────────────


def detect_os() -> str:
    """Return a normalised OS identifier: ``"windows"``, ``"macos"``, or ``"linux"``.

    Raises RuntimeError for unsupported platforms.
    """
    p = sys.platform
    if p.startswith("win"):
        return "windows"
    if p == "darwin":
        return "macos"
    if p.startswith("linux"):
        return "linux"
    raise RuntimeError(_t("os_not_supported", p))


def detect_linux_family() -> str:
    """Return the Linux distribution family: ``"debian"``, ``"fedora"``, ``"arch"``, or ``"unknown"``.

    Parses ``/etc/os-release`` first, then falls back to probing
    which package manager is available.
    """
    os_release = Path("/etc/os-release")
    if os_release.exists():
        data: Dict[str, str] = {}
        for line in os_release.read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip().lower()] = v.strip().strip('"').lower()

        text = "{} {}".format(data.get("id", ""), data.get("id_like", ""))
        if any(x in text for x in ["debian", "ubuntu", "mint", "pop"]):
            return "debian"
        if any(x in text for x in ["fedora", "rhel", "centos", "rocky", "almalinux"]):
            return "fedora"
        if "arch" in text:
            return "arch"

    if which("apt-get"):
        return "debian"
    if which("dnf") or which("yum"):
        return "fedora"
    if which("pacman"):
        return "arch"
    return "unknown"


# ─── Command Execution ────────────────────────────────────────────────────────


def run_command(
    command: str,
    os_name: str,
    live: bool = False,
    show_command: bool = True,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run *command* via the platform shell and return a CompletedProcess.

    On Windows the command is passed to PowerShell (pwsh or powershell).
    On Linux/macOS it is executed through ``/bin/sh`` (``shell=True``).

    Args:
        command:      Shell command string to run.
        os_name:      Platform identifier: "windows", "macos", or "linux".
        live:         When True, inherit stdin/stdout/stderr (no capture).
                      Use for interactive commands; stdout/stderr are None.
        show_command: Print the command to stdout before running it.
        timeout:      Optional timeout in seconds passed to subprocess.

    Returns:
        ``subprocess.CompletedProcess`` with returncode and, when not live,
        stdout/stderr captured as text.
    """
    if show_command:
        print("\n  {} {}".format(_dim("$"), _dim(command)), flush=True)

    if os_name == "windows":
        cmd = [
            _PS_EXE,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
        if live:
            return subprocess.run(cmd, check=False, timeout=timeout)
        return subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            creationflags=_WIN_NO_WINDOW,
        )

    if live:
        return subprocess.run(command, shell=True, check=False, timeout=timeout)
    return subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def run_install_command(
    command: str, os_name: str, label: str
) -> subprocess.CompletedProcess:
    """Choose live vs progress mode automatically based on the command type."""
    if _is_live_cmd(command):
        return run_command_live(command, os_name, label)
    return run_command_progress(command, os_name, label)


def command_ok(command: str, os_name: str) -> bool:
    """Return True if *command* exits with code 0 within 8 seconds."""
    try:
        return (
            run_command(command, os_name, show_command=False, timeout=8).returncode == 0
        )
    except Exception:
        return False


# ─── Progress Bar ─────────────────────────────────────────────────────────────

_BAR_W = 30
_BAR_FILL = "█"
_BAR_EMPTY = "░"


def _render_bar(label: str, percent: int, spinner: Optional[str] = None) -> str:
    p = max(0, min(100, percent))
    filled = int((p / 100.0) * _BAR_W)
    if _USE_COLOR:
        bar = _c(_A.BGREEN, _BAR_FILL * filled) + _c(
            _A.DIM, _BAR_EMPTY * (_BAR_W - filled)
        )
        pct = _bold("{:3d}%".format(p))
    else:
        bar = "#" * filled + "-" * (_BAR_W - filled)
        pct = "{:3d}%".format(p)
    if spinner:
        spin = _c(_A.BCYAN, spinner) if _USE_COLOR else spinner
        return "\r  {} {} [{}] {}".format(spin, label, bar, pct)
    return "\r  {} [{}] {}".format(label, bar, pct)


def _is_live_cmd(command: str) -> bool:
    """
    Return True for commands that need a real TTY (curl-to-bash installers,
    interactive shell scripts). Capturing their output via PIPE causes them
    to hang or buffer silently. They must run in pass-through (live) mode.
    """
    lower = command.lower()
    # curl … | bash  /  curl … | sh
    if re.search(r"curl\b.*\|\s*(bash|sh)\b", lower):
        return True
    # bash -c "$(curl ...)"  or  bash -c '$(curl ...)'
    if re.search(r"bash\s+-c\s+.+curl", lower):
        return True
    # PowerShell iex / irm (Windows)
    if re.search(r"\birm\b.*\|\s*iex\b", lower):
        return True
    return False


def run_command_live(
    command: str, os_name: str, label: str
) -> subprocess.CompletedProcess:
    """
    Run command with stdin/stdout/stderr passed directly to the terminal.
    Used for interactive shell scripts that break when captured via PIPE.

    Some installers (e.g. qwen) launch the tool interactively at the end of
    their setup. Exit code 130 (SIGINT / Ctrl+C) is treated as success because
    the binary is already installed — the user just exited that interactive session.
    """
    # Show spinner immediately while the process is being created
    stop_ev = threading.Event()
    spin_thread = threading.Thread(
        target=_run_spinner, args=(label, stop_ev), daemon=True,
    )
    spin_thread.start()

    if os_name == "windows":
        cmd = [
            _PS_EXE,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
        process = subprocess.Popen(cmd)
    else:
        process = subprocess.Popen(command, shell=True)

    # Stop the spinner now that the process is running and about to produce output
    stop_ev.set()
    spin_thread.join()

    print("  {} {} {}".format(_info("▶"), label, _dim(_t("live_output"))))
    print(
        "  {} {}".format(
            _dim("ℹ"),
            _dim(_t("live_hint")),
        )
    )
    print(_dim("  " + "─" * 52))

    rc = process.wait()

    print(_dim("  " + "─" * 52))
    # 130 = SIGINT (Ctrl+C): user exited the interactive session the installer launched.
    # The binary is already installed — treat this as success.
    if rc == 130:
        rc = 0

    sym = _ok("✔") if rc == 0 else _err("✘")
    status = _ok(_t("done")) if rc == 0 else _err(_t("failed"))
    print("  {} {} {}".format(sym, label, status), flush=True)

    return subprocess.CompletedProcess(
        args=command, returncode=rc, stdout=None, stderr=None
    )


def run_command_progress(
    command: str, os_name: str, label: str
) -> subprocess.CompletedProcess:
    """Run *command* with a spinner/progress-bar overlay in the terminal.

    Captures stdout+stderr from the child process and uses the output to
    drive a real-time progress bar when percentage strings (e.g. "47%") are
    detected, or a spinner otherwise.  The last 20 lines of output are kept
    and printed on failure to help with debugging.

    Returns a ``CompletedProcess`` with returncode; stdout/stderr are None
    (consumed internally to drive the progress display).
    """
    stop_ev = threading.Event()
    spin_thread = threading.Thread(
        target=_run_spinner, args=(label, stop_ev), daemon=True,
    )
    spin_thread.start()

    if os_name == "windows":
        cmd = [
            _PS_EXE,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            creationflags=_WIN_NO_WINDOW,
        )
    else:
        process = subprocess.Popen(
            command,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    percent_re = re.compile(r"(\d{1,3})%")
    last_lines = deque(maxlen=20)
    last_percent = 0
    has_percent = False
    spin_idx = 0
    early_spinner_stopped = False

    if process.stdout is not None:
        for line in process.stdout:
            # Stop the early spinner on the first output line
            if not early_spinner_stopped:
                stop_ev.set()
                spin_thread.join()
                early_spinner_stopped = True

            clean = line.rstrip("\n")
            if clean:
                last_lines.append(clean)

            matches = percent_re.findall(line)
            if matches:
                current = int(matches[-1])
                if 0 <= current <= 100:
                    has_percent = True
                    current = min(current, 99)
                    last_percent = max(last_percent, current)
                    print(_render_bar(label, last_percent), end="", flush=True)
            else:
                f = (
                    _SPINNER[spin_idx % len(_SPINNER)]
                    if _USE_COLOR
                    else "|/-\\"[spin_idx % 4]
                )
                spin_idx += 1
                if _USE_COLOR:
                    print(
                        "\r  {} {}".format(_c(_A.BCYAN, f), _dim(label + "…")),
                        end="",
                        flush=True,
                    )
                else:
                    print("\r  [{}] {}...".format(f, label), end="", flush=True)

    # Ensure spinner is stopped even if process had no output at all
    if not early_spinner_stopped:
        stop_ev.set()
        spin_thread.join()

    rc = process.wait()

    if has_percent:
        if rc == 0:
            print(_render_bar(label, 100))
        else:
            print()
    else:
        sym = _ok("✔") if rc == 0 else _err("✘")
        status = _ok(_t("done")) if rc == 0 else _err(_t("failed"))
        print("\r  {} {} {}{}".format(sym, label, status, " " * 20), flush=True)

    if rc != 0 and last_lines:
        print("\n  {}".format(_warn(_t("last_output_lines"))))
        for ln in list(last_lines)[-8:]:
            print("    {}".format(_dim(ln)))

    return subprocess.CompletedProcess(
        args=command, returncode=rc, stdout=None, stderr=None
    )


# ─── Path / Shell Helpers ─────────────────────────────────────────────────────


def first_command_token(command: str) -> Optional[str]:
    """Return the first whitespace-delimited token of *command* (the binary name)."""
    try:
        parts = shlex.split(command)
    except Exception:
        return None
    return parts[0].strip() if parts else None


def command_needs_linux_root(command: str) -> bool:
    """Match by first token only — avoids false positives from path substrings."""
    ROOT_CMDS = {"apt-get", "apt", "dnf", "yum", "pacman", "zypper"}
    token = first_command_token(command)
    return token is not None and token.lower() in ROOT_CMDS


def with_linux_elevation(
    command: str, os_name: str, force: bool = False
) -> Optional[str]:
    if os_name != "linux":
        return command
    if not force and not command_needs_linux_root(command):
        return command
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return command
    if command.lstrip().startswith("sudo "):
        return command
    if not which("sudo"):
        return None
    return "sudo sh -c {}".format(shlex.quote(command))


def command_in_profile_shell(command: str, os_name: str) -> str:
    """Wrap *command* so it runs inside a login bash shell on Linux/macOS.

    Sources ~/.profile, ~/.bashrc and ~/.zshrc before running the command,
    making binaries installed to profile-sourced directories (e.g. ~/.local/bin)
    visible even when they are not in the current process PATH.
    On Windows (or when bash is unavailable) returns *command* unchanged.
    """
    if os_name not in ("linux", "macos") or not which("bash"):
        return command
    wrapped = (
        "source ~/.profile >/dev/null 2>&1; "
        "source ~/.bashrc >/dev/null 2>&1; "
        "source ~/.zshrc >/dev/null 2>&1; " + command
    )
    return "bash -lc {}".format(shlex.quote(wrapped))


def binary_available_in_profile_shell(binary: str, os_name: str) -> bool:
    """Return True if *binary* is reachable inside a login bash shell.

    Used on Linux/macOS to detect tools installed to profile-sourced paths
    (e.g. ~/.local/bin) that are not visible in the current process PATH.
    Always returns False on Windows.
    """
    if os_name not in ("linux", "macos") or not which("bash"):
        return False
    inner = (
        "source ~/.profile >/dev/null 2>&1; "
        "source ~/.bashrc >/dev/null 2>&1; "
        "source ~/.zshrc >/dev/null 2>&1; "
        "command -v {} >/dev/null 2>&1".format(shlex.quote(binary))
    )
    check_cmd = "bash -lc {}".format(shlex.quote(inner))
    try:
        result = run_command(check_cmd, os_name, show_command=False, timeout=10)
    except Exception:
        return False
    return result.returncode == 0


def bootstrap_user_bin_path(os_name: str) -> List[str]:
    """Prepend user-local bin directories to PATH for the current process.

    Adds ``~/.local/bin`` and ``~/.npm-global/bin`` to PATH when they exist
    but are missing from the environment (common on fresh Linux/macOS setups).
    Returns the list of directories that were actually added.
    No-op on Windows.
    """
    if os_name not in ("linux", "macos"):
        return []
    current_path = os.environ.get("PATH", "")
    path_parts = [p for p in current_path.split(os.pathsep) if p]
    existing = set(path_parts)
    candidates = [Path.home() / ".local" / "bin", Path.home() / ".npm-global" / "bin"]
    added: List[str] = []
    for candidate in candidates:
        c_str = str(candidate)
        if candidate.is_dir() and c_str not in existing:
            path_parts.insert(0, c_str)
            existing.add(c_str)
            added.append(c_str)
    if added:
        os.environ["PATH"] = os.pathsep.join(path_parts)
    return added


def _ensure_path_line(file_path: Path, export_line: str) -> bool:
    """Append *export_line* to *file_path* if it is not already present.

    Returns True when the line was added, False when it was already there.
    Creates the file if it does not exist.
    """
    existing = ""
    if file_path.exists():
        existing = file_path.read_text(encoding="utf-8", errors="ignore")
        if export_line in existing:
            return False
    if existing and not existing.endswith("\n"):
        existing += "\n"
    file_path.write_text(existing + export_line + "\n", encoding="utf-8")
    return True


def refresh_windows_path_from_registry() -> bool:
    """
    Re-read PATH from the Windows registry (HKCU\\Environment and
    HKLM\\...\\Session Manager\\Environment) and update os.environ["PATH"].
    After winget/installer runs, the registry reflects the new bin directories
    but the running Python process still holds the PATH snapshot from startup —
    so shutil.which() and any subprocess it spawns can't see the new binaries
    until the user reopens the shell. This fixes that for the current process.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False

    def _read(root, subkey: str) -> str:
        try:
            with winreg.OpenKey(root, subkey) as k:
                val, _ = winreg.QueryValueEx(k, "Path")
                return val or ""
        except OSError:
            return ""

    machine = _read(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    )
    user = _read(winreg.HKEY_CURRENT_USER, r"Environment")

    parts = [p for p in (machine, user) if p]
    if not parts:
        return False
    merged = os.path.expandvars(";".join(parts))

    if merged == os.environ.get("PATH", ""):
        return False
    os.environ["PATH"] = merged
    return True


def persist_user_bin_path(os_name: str) -> List[str]:
    """Write PATH export lines for user-local bin directories to shell RC files.

    Ensures ``~/.local/bin`` and ``~/.npm-global/bin`` are exported in
    ``~/.profile``, ``~/.bashrc`` and ``~/.zshrc`` so they survive new
    terminal sessions.  Returns the list of paths that were newly persisted.
    No-op on Windows.
    """
    if os_name not in ("linux", "macos"):
        return []
    persisted: List[str] = []
    entries = [
        ("$HOME/.local/bin", Path.home() / ".local" / "bin"),
        ("$HOME/.npm-global/bin", Path.home() / ".npm-global" / "bin"),
    ]
    rc_files = [
        Path.home() / ".profile",
        Path.home() / ".bashrc",
        Path.home() / ".zshrc",
    ]
    for shell_path, real_path in entries:
        if not real_path.exists():
            continue
        export_line = 'export PATH="{}:$PATH"'.format(shell_path)
        changed_any = False
        for rc in rc_files:
            try:
                changed_any = _ensure_path_line(rc, export_line) or changed_any
            except Exception:
                continue
        if changed_any:
            persisted.append(shell_path)
    return persisted


# ─── Config Resolver ──────────────────────────────────────────────────────────


def resolve_command(block, os_name: str, linux_family: Optional[str]) -> Optional[str]:
    """Resolve an install/upgrade/uninstall command for the current platform.

    *block* may be:
    - A plain string → returned as-is.
    - A dict with keys ``"all"``, ``"windows"``, ``"macos"``, ``"linux"`` →
      the value matching *os_name* is returned, falling back to ``"all"``.
    - A dict whose ``"linux"`` value is itself a dict keyed by distro family
      (``"debian"``, ``"fedora"``, ``"arch"``, ``"default"``, etc.) →
      the entry for *linux_family* is returned, falling back to ``"default"``.

    Returns ``None`` when no matching command is found.
    """
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return None

    if os_name == "linux" and "linux" in block:
        linux_block = block["linux"]
        if isinstance(linux_block, str):
            return linux_block
        if isinstance(linux_block, dict):
            alias_map = {
                "debian": ["debian", "apt"],
                "fedora": ["fedora", "rpm", "dnf", "yum"],
                "arch": ["arch", "pacman"],
            }
            keys = alias_map.get(
                linux_family or "", [linux_family] if linux_family else []
            )
            for key in keys:
                cmd = linux_block.get(key)
                if cmd:
                    return cmd
            return linux_block.get("default") or None

    return block.get(os_name) or block.get("all") or None


# ─── Version Detection ────────────────────────────────────────────────────────


def _probe_version(
    probe: str, os_name: str, use_profile: bool = False
) -> Optional[str]:
    """Run *probe* and return the first line of its output, or None on failure.

    When *use_profile* is True the command is wrapped in a login shell so
    profile-sourced directories are on PATH.
    """
    cmd = command_in_profile_shell(probe, os_name) if use_profile else probe
    try:
        result = run_command(cmd, os_name, show_command=False, timeout=4)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    text = out if out else err
    return text.splitlines()[0].strip() if text else _t("installed")


def detect_tool_version(
    tool_name: str, tool_block: Dict, os_name: str
) -> Optional[str]:
    """Return a version string for *tool_name*, or None if not installed.

    Detection strategy (in order):
    1. If ``version_cmd`` is set: run it, optionally filter with
       ``version_regex``, and return the result.  Falls back to PATH check.
    2. If ``skip_version_probe`` is set: only check PATH/profile presence
       (avoids accidentally launching GUI apps).
    3. Otherwise: try ``--version``, ``-v``, and ``version`` sub-commands.
       - First checks PATH (fast).
       - Then checks a login-shell profile on Linux/macOS (slower, only when
         the binary is confirmed reachable there).
    """
    run_cmd = tool_block.get("run")
    if not run_cmd:
        return None

    # If a safe alternative command is provided, use it to get the version
    # without risking launching a GUI app (e.g. a specific CLI binary on Windows).
    # version_cmd can be a string or a dict with OS-specific keys (windows/macos/linux/all).
    version_cmd_raw = tool_block.get("version_cmd")
    version_regex = tool_block.get("version_regex")
    if version_cmd_raw:
        if isinstance(version_cmd_raw, dict):
            version_cmd = (
                version_cmd_raw.get(os_name)
                or version_cmd_raw.get("all")
            )
        else:
            version_cmd = version_cmd_raw
        if version_cmd:
            raw = _probe_version(version_cmd, os_name, use_profile=False)
            if raw and version_regex:
                m = re.search(version_regex, raw)
                if m:
                    return m.group(1)
            if raw:
                return raw
        # Fallback: check PATH presence only
        token = first_command_token(run_cmd)
        if token and (which(token) or binary_available_in_profile_shell(token, os_name)):
            return _t("installed")
        return None

    # Some GUI apps don't provide a safe CLI version command and may launch
    # the desktop window when probed. For these tools, only check PATH presence.
    if tool_block.get("skip_version_probe"):
        token = first_command_token(run_cmd)
        if token and (
            which(token) or binary_available_in_profile_shell(token, os_name)
        ):
            return _t("installed")
        return None

    probes = [
        "{} --version".format(run_cmd),
        "{} -v".format(run_cmd),
        "{} version".format(run_cmd),
    ]

    # Fast rejection: a shutil.which() call is microseconds; a probe via
    # PowerShell on Windows is ~300–500 ms. Skip all probes when the binary
    # is not even on PATH — saves ~3 doomed subprocesses per missing tool.
    token = first_command_token(run_cmd)
    in_path = bool(token and which(token))

    if in_path:
        for probe in probes:
            v = _probe_version(probe, os_name, use_profile=False)
            if v:
                return v

    # Linux/macOS: tool may be installed via a profile RC (e.g. ~/.local/bin
    # sourced from .profile) and not visible in the current PATH. Only pay
    # the cost of a login-shell probe when a profile check confirms the
    # binary is reachable there.
    if (
        os_name in ("linux", "macos")
        and token
        and which("bash")
        and binary_available_in_profile_shell(token, os_name)
    ):
        for probe in probes:
            v = _probe_version(probe, os_name, use_profile=True)
            if v:
                return v
        return _t("installed")

    # GUI apps that are on PATH but don't respond to --version/-v/version.
    if in_path:
        return _t("installed")

    return None


def tool_access_state(tool_block: Dict, os_name: str) -> str:
    """Return how the tool binary is currently accessible.

    Returns:
        ``"current"``  — binary is on the current process PATH.
        ``"profile"``  — binary is only reachable inside a login shell.
        ``"missing"``  — binary not found anywhere.
        ``"unknown"``  — tool block has no ``"run"`` field.
    """
    run_cmd = tool_block.get("run")
    if not run_cmd:
        return "unknown"
    token = first_command_token(run_cmd)
    if not token:
        return "unknown"
    if which(token):
        return "current"
    if binary_available_in_profile_shell(token, os_name):
        return "profile"
    return "missing"


# ─── Node.js Version Check ────────────────────────────────────────────────────

# Minimum Node.js major version required by modern CLI tools (regex /v flag = v20+)
_NODE_MIN_MAJOR = 20


def _node_major_version(os_name: str) -> Optional[int]:
    """Return the current Node.js major version, or None if undetectable."""
    try:
        r = run_command("node --version", os_name, show_command=False, timeout=8)
        if r.returncode != 0:
            return None
        m = re.search(r"v?(\d+)", (r.stdout or "").strip())
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _node_upgrade_cmd(os_name: str, linux_family: Optional[str]) -> Optional[str]:
    """Return the command to upgrade Node.js to LTS for the current platform."""
    if os_name == "linux":
        family = linux_family or ""
        if family in ("debian",):
            # apt path: install via n (cross-distro, works on any Debian/Ubuntu)
            return "npm install -g n && sudo n lts"
        if family in ("fedora",):
            return "npm install -g n && sudo n lts"
        if family in ("arch",):
            return "sudo pacman -Sy --noconfirm nodejs npm"
        # Unknown distro: fallback to n
        return "npm install -g n && sudo n lts"
    if os_name == "macos":
        return "npm install -g n && sudo n lts"
    if os_name == "windows":
        return "winget install -e --id OpenJS.NodeJS.LTS"
    return None


def check_node_version_for_npm(os_name: str, linux_family: Optional[str]) -> bool:
    """
    Check Node.js version. If too old, offer to upgrade automatically.
    Returns True when the version is acceptable (or was successfully upgraded).
    """
    major = _node_major_version(os_name)
    if major is None or major >= _NODE_MIN_MAJOR:
        return True

    print(
        "  {} {}".format(
            _warn("⚠"), _t("node_old_version", _warn("v" + str(major)), _NODE_MIN_MAJOR)
        )
    )
    print(
        "  {}   {}".format(
            _dim(" "), _t("node_old_hint")
        )
    )

    upgrade_cmd = _node_upgrade_cmd(os_name, linux_family)

    if upgrade_cmd and ask_yes_no(
        _t("upgrade_node_now"), default_yes=True
    ):
        print()
        result = run_command_progress(upgrade_cmd, os_name, _t("updating_node"))
        if result.returncode != 0:
            print("  {} {}".format(_err("✘"), _t("node_upgrade_failed")))
            print("  {} {}".format(_dim("↳"), _t("try_manually_cmd", _info(upgrade_cmd))))
            return False

        # Re-check version with fresh lookup (n installs to /usr/local/bin)
        new_major = _node_major_version(os_name)
        if new_major and new_major >= _NODE_MIN_MAJOR:
            print("  {} {}".format(_ok("✔"), _t("node_updated", new_major)))
            return True

        # n may have updated the binary but the current PATH still points to the
        # old one; try a login-shell probe before giving up.
        try:
            r = run_command(
                command_in_profile_shell("node --version", os_name),
                os_name,
                show_command=False,
                timeout=10,
            )
            m = re.search(r"v?(\d+)", (r.stdout or "").strip())
            if m and int(m.group(1)) >= _NODE_MIN_MAJOR:
                print(
                    "  {} {}".format(
                        _ok("✔"), _t("node_updated_reload", _info("exec $SHELL -l"))
                    )
                )
                return True
        except Exception:
            pass

        print(
            "  {} {}".format(
                _warn("⚠"), _t("node_updated_not_detected")
            )
        )
        print(
            "  {} {}".format(_dim("↳"), _t("run_cmd_and_retry", _info("exec $SHELL -l")))
        )
        return False

    # User declined upgrade — ask whether to proceed anyway
    return ask_yes_no(_t("continue_anyway"), default_yes=False)


# ─── Git Dependency (Windows) ─────────────────────────────────────────────────


def _git_available() -> bool:
    """Return True if git is accessible in the current PATH."""
    return bool(which("git"))


def _add_git_to_path() -> bool:
    """Try to find Git in standard Windows install paths and add to PATH."""
    candidates = [
        Path(r"C:\Program Files\Git\bin"),
        Path(r"C:\Program Files (x86)\Git\bin"),
    ]
    for candidate in candidates:
        if (candidate / "git.exe").exists():
            os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
            return True
    return False


def _set_git_bash_env() -> None:
    """Set CLAUDE_CODE_GIT_BASH_PATH if not already defined."""
    if os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        return
    bash = which("bash")
    if bash:
        os.environ["CLAUDE_CODE_GIT_BASH_PATH"] = bash
        return
    fallback = Path(r"C:\Program Files\Git\bin\bash.exe")
    if fallback.exists():
        os.environ["CLAUDE_CODE_GIT_BASH_PATH"] = str(fallback)


def ensure_git_for_tool(tool_name: str, os_name: str) -> bool:
    """
    Ensure Git is available on Windows for tools that require it (needs_git).
    Attempts to locate Git in standard paths, then offers to install via winget.
    Returns True when Git is ready, False if installation failed / was declined.
    """
    if os_name != "windows":
        return True

    # Already in PATH?
    if _git_available():
        _set_git_bash_env()
        return True

    # Try standard install paths
    if _add_git_to_path():
        _set_git_bash_env()
        return True

    # Git not found anywhere — inform user
    print(
        "\n  {} {}".format(
            _warn("⚠"), _t("git_not_found", _bold(tool_name))
        )
    )

    if not ask_yes_no(_t("git_install_prompt"), default_yes=True):
        print("  {} {}".format(_err("✘"), _t("git_required_skip", _bold(tool_name))))
        return False

    # Check winget
    if not which("winget"):
        print("  {} {}".format(_err("✘"), _t("git_winget_missing")))
        return False

    # Install via winget
    result = run_install_command(
        "winget install -e --id Git.Git", os_name, _t("git_installing")
    )
    if result.returncode != 0:
        print("  {} {}".format(_err("✘"), _t("git_install_failed")))
        print("  {} {}".format(_dim("↳"), _t("git_install_manual")))
        return False

    # Refresh PATH from registry so we can see git immediately
    refresh_windows_path_from_registry()

    # Also try standard paths in case registry refresh didn't cover it
    if not _git_available():
        _add_git_to_path()

    if _git_available():
        _set_git_bash_env()
        print("  {} {}".format(_ok("✔"), _t("git_installed_ok")))
        return True

    print("  {} {}".format(_err("✘"), _t("git_install_failed")))
    print("  {} {}".format(_dim("↳"), _t("git_install_manual")))
    return False


# ─── Windows PATH Entry Helper ────────────────────────────────────────────────


def _ensure_path_entry(directory: str, os_name: str) -> bool:
    """
    Ensure *directory* is in the Windows user PATH — fully automatic, no
    prompts.  Adds to HKCU registry, broadcasts WM_SETTINGCHANGE, and
    updates the current session PATH.

    Returns True when the directory is (or was already) in the PATH.
    """
    if os_name != "windows":
        return True

    expanded = os.path.expandvars(os.path.expanduser(directory))
    expanded_lower = expanded.lower().rstrip("\\")

    # Already in the current session?
    current_parts = [p.lower().rstrip("\\") for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if expanded_lower in current_parts:
        return True

    # Read the user PATH from the registry
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Environment", 0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            try:
                reg_value, reg_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                reg_value, reg_type = "", winreg.REG_EXPAND_SZ

            reg_parts = [p.lower().rstrip("\\") for p in reg_value.split(";") if p.strip()]

            if expanded_lower not in reg_parts:
                # Append to the registry value
                new_value = (reg_value.rstrip(";") + ";" + expanded) if reg_value.strip() else expanded
                winreg.SetValueEx(key, "Path", 0, reg_type, new_value)

        # Broadcast WM_SETTINGCHANGE so Explorer / new terminals see it
        try:
            import ctypes
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0,
                "Environment", SMTO_ABORTIFHUNG, 5000, ctypes.byref(ctypes.c_ulong(0)),
            )
        except Exception:
            pass  # Non-critical — user can restart terminal manually

        # Update the current session
        os.environ["PATH"] = expanded + os.pathsep + os.environ.get("PATH", "")
        print("  {} {}".format(_ok("✔"), _t("path_added_ok", _bold(expanded))))
        return True

    except Exception:
        print("  {} {}".format(_err("✘"), _t("path_add_failed")))
        print("  {} {}".format(_dim("↳"), _t("path_manual_hint")))
        return False


# ─── npm Prefix Helper ────────────────────────────────────────────────────────


def _is_npm_global_cmd(command: str) -> bool:
    """Return True if the command is an npm/npx global install."""
    parts = command.lower().split()
    if not parts or parts[0] not in ("npm", "npx"):
        return False
    return "-g" in parts or "--global" in parts


def ensure_npm_user_prefix(os_name: str) -> bool:
    """
    Guarantee that npm's global prefix is a directory writable by the current
    user.  If the current prefix is root-owned, we redirect it to
    ~/.npm-global and update PATH immediately.

    Returns True when the environment is ready for npm global installs.
    """
    if os_name == "windows" or not which("npm"):
        return True

    try:
        r = run_command("npm config get prefix", os_name, show_command=False, timeout=8)
        if r.returncode != 0:
            return True  # Can't tell — let npm fail naturally
        prefix = (r.stdout or "").strip()
        if not prefix:
            return True

        prefix_path = Path(prefix)
        if prefix_path.exists() and os.access(str(prefix_path), os.W_OK):
            return True  # Already writable — nothing to do

        # Prefix is not user-writable: redirect to ~/.npm-global
        npm_global = Path.home() / ".npm-global"
        npm_global.mkdir(parents=True, exist_ok=True)

        r2 = run_command(
            "npm config set prefix {}".format(shlex.quote(str(npm_global))),
            os_name,
            show_command=False,
            timeout=8,
        )
        if r2.returncode != 0:
            return False

        # Inject bin dir into current session PATH
        bin_dir = str(npm_global / "bin")
        current = os.environ.get("PATH", "")
        parts = [p for p in current.split(os.pathsep) if p]
        if bin_dir not in parts:
            os.environ["PATH"] = bin_dir + os.pathsep + current

        print(
            "  {} {}".format(
                _info("ℹ"),
                _t("npm_prefix_redirect", _dim(str(npm_global))),
            )
        )
        return True
    except Exception:
        return False


# ─── Parallel Version Scan ────────────────────────────────────────────────────

VersionMap = Dict[str, Optional[str]]


def scan_all_versions(
    tools: Dict,
    os_name: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> VersionMap:
    """Detect all tool versions concurrently.

    If ``on_progress`` is provided, it is called from the main thread as each
    future completes with ``(done, total)`` so the caller can render a
    progress bar.
    """

    def _detect(name: str, block: Dict) -> Tuple[str, Optional[str]]:
        return name, detect_tool_version(name, block, os_name)

    results: VersionMap = {}
    total = len(tools)
    workers = min(16, max(1, total))
    if on_progress:
        on_progress(0, total)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_detect, n, b): n for n, b in tools.items()}
        done = 0
        for fut in as_completed(futures):
            name, ver = fut.result()
            results[name] = ver
            done += 1
            if on_progress:
                on_progress(done, total)
    return results


# ─── UI Primitives ────────────────────────────────────────────────────────────


def print_banner() -> None:
    print()
    if _USE_COLOR:
        w = _A.BWHITE
        d = _A.DIM
        print(_c(w + _A.BOLD, "  RivetRook"))
        print(_c(d, "  AI toolchain manager"))
    else:
        print("  RivetRook")
        print("  AI toolchain manager")
    print()


def print_section(title: str) -> None:
    print("\n  {}".format(_bold(title)))


def _exec_shell_reload(os_name: str) -> None:
    """Replace the current process with a fresh login shell (updates PATH instantly)."""
    if os_name not in ("linux", "macos"):
        print(
            "  {} {}".format(
                _info("ℹ"), _t("open_new_terminal")
            )
        )
        return
    shell = os.environ.get("SHELL", "/bin/bash")
    print("\n  {} {}\n".format(_info("→"), _t("reloading_shell", _dim(shell))))
    sys.stdout.flush()
    os.execlp(shell, shell, "-l")  # replaces this process — never returns


def ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    y_short = _t("yes_short")
    n_short = _t("no_short")
    yes = _ok(y_short) if _USE_COLOR else y_short
    no = _err(n_short) if _USE_COLOR else n_short
    if default_yes:
        suffix = "[{}/{}]".format(yes, _dim(n_short) if _USE_COLOR else n_short)
    else:
        suffix = "[{}/{}]".format(_dim(y_short.lower()) if _USE_COLOR else y_short.lower(), no)
    ans = (
        input("\n  {} {} {} ".format(_bold("?"), prompt, _dim(suffix))).strip().lower()
    )
    if ans == "":
        return default_yes
    yes_words = _STRINGS.get("yes_words", ["s", "sim", "y", "yes"])
    return ans in yes_words


# ─── API Key Configuration ────────────────────────────────────────────────────


def _api_key_configured(cfg: Dict) -> bool:
    """Return True if the tool's API key is already set (env var or settings file)."""
    env_var = cfg.get("env_var", "")
    if env_var and os.environ.get(env_var):
        return True
    sf = Path(cfg.get("settings_file", "~/.nonexistent")).expanduser()
    sk = cfg.get("settings_key", "")
    if sf.exists() and sk:
        try:
            return bool(json.loads(sf.read_text(encoding="utf-8")).get(sk))
        except Exception:
            pass
    return False


def configure_api_key(tool_name: str, cfg: Dict, os_name: str) -> None:
    """Prompt for an API key, save it to the settings file, and persist to shell profiles."""
    env_var = cfg.get("env_var", "")
    settings_file = Path(cfg.get("settings_file", "")).expanduser()
    settings_key = cfg.get("settings_key", "")
    prompt_text = _prompt_text(cfg)

    if _api_key_configured(cfg):
        print("  {} [{}] {}".format(_info("ℹ"), _bold(tool_name), _t("api_key_already_set")))
        if not ask_yes_no(_t("replace_key"), default_yes=False):
            return

    print()
    api_key = input("  {} {} ".format(_bold("›"), prompt_text)).strip()
    if not api_key:
        print("  {} {}".format(_warn("⚠"), _t("no_key_provided")))
        return

    # ── Save to settings file (JSON) ────────────────────────────────────────
    if settings_file and settings_key:
        try:
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            existing: Dict = {}
            if settings_file.exists():
                try:
                    existing = json.loads(settings_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            existing[settings_key] = api_key
            settings_file.write_text(
                json.dumps(existing, indent=2) + "\n", encoding="utf-8"
            )
            print("  {} {}".format(_ok("✔"), _t("saved_at", _dim(str(settings_file)))))
        except Exception as exc:
            print("  {} {}".format(_err("✘"), _t("error_saving", settings_file, exc)))

    # ── Persist export in shell RC files ────────────────────────────────────
    if env_var and os_name in ("linux", "macos"):
        export_line = 'export {}="{}"'.format(env_var, api_key)
        rc_files = [Path.home() / f for f in (".bashrc", ".zshrc", ".profile")]
        changed = False
        for rc in rc_files:
            if not rc.exists():
                continue
            try:
                lines = rc.read_text(encoding="utf-8", errors="ignore").splitlines()
                # Remove any previous value for this var
                lines = [
                    l
                    for l in lines
                    if not re.match(r"^\s*export\s+{}=".format(re.escape(env_var)), l)
                ]
                lines.append(export_line)
                rc.write_text("\n".join(lines) + "\n", encoding="utf-8")
                changed = True
            except Exception:
                continue
        if changed:
            print(
                "  {} {}".format(
                    _ok("✔"), _t("key_persisted", _dim(env_var))
                )
            )

    # ── Activate in the current session ─────────────────────────────────────
    if env_var:
        os.environ[env_var] = api_key
        print("  {} {}".format(_ok("✔"), _t("key_active", _dim(env_var))))


# ─── Tools Table ──────────────────────────────────────────────────────────────


def _status_cell(version: Optional[str], access: str) -> Tuple[str, str]:
    """Return (icon, colored-label) for a tool row."""
    if version:
        if access == "profile":
            return _warn("◉"), _warn(_t("shell_profile"))
        return _ok("●"), _ok(_t("status_installed"))
    return _err("○"), _err(_t("status_not_installed"))


def _tool_row(
    idx: int,
    name: str,
    block: Dict,
    version: Optional[str],
    os_name: str,
    name_w: int,
    stat_w: int,
    ver_w: int,
) -> None:
    """Print a single compact tool row."""

    access = tool_access_state(block, os_name)
    icon, status_label = _status_cell(version, access)

    cfg = block.get("configure")
    if version and cfg:
        key_badge = " " + (_ok("⚿") if _api_key_configured(cfg) else _warn("⚿"))
    else:
        key_badge = ""

    author = block.get("author", "")
    num_str = _rjust(_bold(str(idx)), 3)
    name_lbl = _hi(name) if version else name
    if author:
        name_lbl = "{} {}".format(name_lbl, _dim("· " + author))
    name_str = _ljust(name_lbl, name_w)
    stat_str = _ljust(icon + " " + status_label + key_badge, stat_w + 2)
    ver_str = _dim((version or "—")[:ver_w])

    print("  {} {} {} {}".format(num_str, name_str, stat_str, ver_str))


def _section_divider(label: str, total_w: int) -> None:
    """Print a category section divider (e.g. CLI Tools / IDEs)."""
    bar = "━" * total_w
    print("\n  " + _c(_A.BBLUE + _A.BOLD, "◈ " + label))
    print("  " + _dim(bar))


def print_tools_table(
    tools: Dict, ides: Dict, version_map: VersionMap, os_name: str
) -> None:
    """Render the full tools table to stdout, split into CLI Tools and IDEs sections.

    Installed tools are listed first within each section, followed by
    not-installed ones.  Column widths are computed dynamically to fit the
    longest name.
    """
    all_tools = {**tools, **ides}
    name_labels = []
    for n, b in all_tools.items():
        author = b.get("author", "")
        if author:
            name_labels.append("{} · {}".format(n, author))
        else:
            name_labels.append(n)
    name_w = max(len(lbl) for lbl in name_labels) + 1
    stat_w = 16
    stat_col_w = stat_w + 2
    ver_w = 36
    total_w = 3 + 1 + name_w + 1 + stat_col_w + 1 + ver_w

    def _render_group(group_names: List[Tuple[int, str]], src: Dict) -> None:
        for idx, name in group_names:
            _tool_row(
                idx,
                name,
                src[name],
                version_map.get(name),
                os_name,
                name_w,
                stat_w,
                ver_w,
            )

    # ── CLI Tools section ────────────────────────────────────────────────────
    cli_names = list(tools.keys())
    cli_offset = 0  # CLI tools are indices 1..len(cli_names)

    cli_installed = [(i + 1, n) for i, n in enumerate(cli_names) if version_map.get(n)]
    cli_not_installed = [
        (i + 1, n) for i, n in enumerate(cli_names) if not version_map.get(n)
    ]

    _section_divider("CLI Tools", total_w)

    if cli_installed:
        _render_group(cli_installed, tools)

    if cli_not_installed:
        _render_group(cli_not_installed, tools)

    # ── IDEs section ─────────────────────────────────────────────────────────
    if ides:
        ide_names = list(ides.keys())
        ide_offset = len(cli_names)  # IDE indices follow CLI indices

        ide_installed = [
            (ide_offset + i + 1, n)
            for i, n in enumerate(ide_names)
            if version_map.get(n)
        ]
        ide_not_installed = [
            (ide_offset + i + 1, n)
            for i, n in enumerate(ide_names)
            if not version_map.get(n)
        ]

        _section_divider("IDEs", total_w)

        if ide_installed:
            _render_group(ide_installed, ides)

        if ide_not_installed:
            _render_group(ide_not_installed, ides)

    print()


# ─── Prerequisite ─────────────────────────────────────────────────────────────


def ensure_prerequisite(
    config: Dict, os_name: str, linux_family: Optional[str]
) -> None:
    """Verify the required prerequisite (Node.js) is installed, installing it if needed.

    Reads the ``"prerequisite"`` block from *config*, runs the specified check
    commands, and installs via the platform-specific command when missing.
    Calls ``sys.exit(1)`` if installation fails or is declined by the user.
    """
    pre = config.get("prerequisite", {})
    pre_name = pre.get("name", "Prerequisito")
    checks = pre.get("check", [])

    all_ok = all(command_ok(ch, os_name) for ch in checks)

    if all_ok:
        print("  {} {}".format(_ok("✔"), _t("already_installed", _bold(pre_name))))
        return

    print("  {} {}".format(_warn("⚠"), _t("not_found", _bold(pre_name))))
    if not ask_yes_no(
        _t("install_now", _bold(pre_name)), default_yes=True
    ):
        print("  {} {}".format(_err("✘"), _t("prerequisite_missing")))
        sys.exit(1)

    install_cmd = resolve_command(pre.get("install", {}), os_name, linux_family)
    if not install_cmd:
        print("  {} {}".format(_err("✘"), _t("no_install_cmd")))
        sys.exit(1)

    elevated = with_linux_elevation(install_cmd, os_name)
    if elevated is None:
        print(
            "  {} {}".format(
                _err("✘"), _t("sudo_not_found")
            )
        )
        sys.exit(1)
    if elevated != install_cmd:
        print("  {} {}".format(_warn("⚠"), _t("elevating_sudo")))

    result = run_install_command(elevated, os_name, _t("installing", pre_name))

    if os_name == "windows":
        refresh_windows_path_from_registry()

    if any(not command_ok(ch, os_name) for ch in checks):
        print(
            "  {} {}".format(
                _err("✘"), _t("prereq_install_failed")
            )
        )
        sys.exit(1)

    # If Node/npm was just installed, configure a user-writable prefix right away
    ensure_npm_user_prefix(os_name)

    persisted = persist_user_bin_path(os_name)
    if persisted:
        print("  {} {}".format(_info("ℹ"), _t("path_persisted", ", ".join(persisted))))
    print("  {} {}".format(_ok("✔"), _t("installed_successfully", _bold(pre_name))))


# ─── Tool Management ──────────────────────────────────────────────────────────


def choose_tool(names: List[str]) -> Optional[List[str]]:
    """
    Prompt the user to pick one or more tools by number or name.
    Accepts a single value ("1", "claude") or a comma/space-separated list ("1,3,5" or "1 3 5").
    Returns a list of tool names, or None to quit.
    Re-prompts on invalid input instead of returning a sentinel.
    """
    while True:
        raw = input(
            "  {} {} {} ".format(
                _bold("›"), _t("choose_tool"), _dim(_t("enter_to_exit"))
            )
        ).strip()

        if not raw:
            return None

        # Split on commas and/or spaces to support "1,3,5" or "1 3 5" or "1, 3, 5"
        tokens = [t.strip() for t in re.split(r"[,\s]+", raw) if t.strip()]

        resolved: List[str] = []
        error = False
        for token in tokens:
            token_lower = token.lower()
            if token.isdigit():
                pos = int(token) - 1
                if 0 <= pos < len(names):
                    tool = names[pos]
                    if tool not in resolved:
                        resolved.append(tool)
                else:
                    print("  {} {}".format(_warn("⚠"), _t("number_out_of_range", len(names))))
                    error = True
                    break
            else:
                match = next((n for n in names if token_lower == n.lower()), None)
                if match:
                    if match not in resolved:
                        resolved.append(match)
                else:
                    print("  {} {}".format(_warn("⚠"), _t("invalid_option_tool")))
                    error = True
                    break

        if error:
            continue

        if resolved:
            return resolved


def manage_tools(config: Dict, os_name: str, linux_family: Optional[str]) -> None:
    """Main interactive loop: scan tool versions, show the menu, execute actions.

    Repeatedly:
    1. Scans all tool versions in parallel.
    2. Renders the tools table.
    3. Prompts the user to choose a tool (or multiple) and an action.
    4. Executes install / upgrade / uninstall / configure.
    5. On success, pauses for a keypress then clears the screen before re-scanning.

    Returns when the user presses Enter at the tool-selection prompt (exit).
    """
    tools = config.get("tools", {})
    ides = config.get("ides", {})
    # Unified ordered map: CLI tools first, then IDEs (indices follow in sequence)
    all_tools: Dict = {**tools, **ides}
    names = list(all_tools.keys())

    def _infer_uninstall_cmd_from_install(install_cmd: Optional[str]) -> Optional[str]:
        if not install_cmd:
            return None

        cmd = install_cmd.strip()

        def _strip_pkg_version(spec: str) -> str:
            spec = spec.strip()
            if not spec:
                return spec
            # Scoped package: @scope/name@version -> @scope/name
            if spec.startswith("@"):
                at = spec.rfind("@")
                if at > 0:
                    return spec[:at]
                return spec
            # Unscoped package: name@version -> name
            return spec.split("@", 1)[0]

        # winget install -e --id Vendor.Package
        m = re.search(
            r"winget\s+install(?:\s+-e)?\s+--id\s+([^\s]+)", cmd, re.IGNORECASE
        )
        if m:
            return "winget uninstall -e --id {}".format(m.group(1))

        # brew install --cask name
        m = re.search(r"brew\s+install\s+--cask\s+([^\s]+)", cmd, re.IGNORECASE)
        if m:
            return "brew uninstall --cask {}".format(m.group(1))

        # brew install name
        m = re.search(r"brew\s+install\s+([^\s]+)", cmd, re.IGNORECASE)
        if m:
            return "brew uninstall {}".format(m.group(1))

        # npm i -g package
        m = re.search(r"npm\s+(?:install|i)\s+-g\s+([^\s]+)", cmd, re.IGNORECASE)
        if m:
            pkg = _strip_pkg_version(m.group(1))
            return "npm uninstall -g {}".format(pkg)

        # bun add -g package
        m = re.search(r"bun\s+add\s+-g\s+([^\s]+)", cmd, re.IGNORECASE)
        if m:
            pkg = _strip_pkg_version(m.group(1))
            return "bun remove -g {}".format(pkg)

        # apt-get install -y pkgs
        m = re.search(r"apt-get\s+install\s+-y\s+(.+)", cmd, re.IGNORECASE)
        if m:
            pkgs = m.group(1).split("&&", 1)[0].strip()
            if pkgs:
                return "apt-get remove -y {}".format(pkgs)

        # dnf/yum install -y pkgs
        m = re.search(r"(?:dnf|yum)\s+install\s+-y\s+(.+)", cmd, re.IGNORECASE)
        if m:
            pkgs = m.group(1).split("&&", 1)[0].strip()
            if pkgs:
                mgr = "dnf" if "dnf" in cmd.lower() else "yum"
                return "{} remove -y {}".format(mgr, pkgs)

        # pacman -Sy --noconfirm pkgs
        m = re.search(r"pacman\s+-S[^\s]*\s+--noconfirm\s+(.+)", cmd, re.IGNORECASE)
        if m:
            pkgs = m.group(1).split("&&", 1)[0].strip()
            if pkgs:
                return "pacman -R --noconfirm {}".format(pkgs)

        return None

    def _resolve_action_command(action_key: str, tool_block: Dict) -> Optional[str]:
        if action_key in ("install", "upgrade"):
            return resolve_command(
                tool_block.get(action_key, {}), os_name, linux_family
            )

        if action_key == "uninstall":
            explicit = resolve_command(
                tool_block.get("uninstall", {}), os_name, linux_family
            )
            if explicit:
                return explicit
            install_cmd = resolve_command(
                tool_block.get("install", {}), os_name, linux_family
            )
            return _infer_uninstall_cmd_from_install(install_cmd)

        return None

    def _choose_action(
        selected: str, current_version: Optional[str], cfg_block: Optional[Dict]
    ) -> Optional[str]:
        print(
            "\n  {} {} {}".format(
                _bold(selected), _dim("(" + (current_version or _t("not_installed_label")) + ")"),
                _t("choose_action")
            )
        )
        print("  {}  1. {}".format(_dim(" "), _t("action_install")))
        print("  {}  2. {}".format(_dim(" "), _t("action_upgrade")))
        print("  {}  3. {}".format(_dim(" "), _t("action_uninstall")))

        if cfg_block:
            key_ok = _api_key_configured(cfg_block)
            key_label = _t("key_configured") if key_ok else _t("key_not_configured")
            print(
                "  {}  4. {} {}".format(
                    _dim(" "), _t("configure_api_key"), _dim("(" + key_label + ")")
                )
            )
            valid = {"1": "install", "2": "upgrade", "3": "uninstall", "4": "configure"}
            hint = _t("action_hint_4")
        else:
            valid = {"1": "install", "2": "upgrade", "3": "uninstall"}
            hint = _t("action_hint_3")

        while True:
            raw = input("\n  {} {} {} ".format(_bold("›"), _t("action_label"), _dim(hint))).strip()
            if not raw:
                return None
            if raw in valid:
                return valid[raw]
            print("  {} {}".format(_warn("⚠"), _t("invalid_option")))

    def _choose_action_multi(selections: List[str]) -> Optional[str]:
        """Ask a single action to apply to multiple tools (configure excluded)."""
        print(
            "\n  {} {}".format(
                _info("ℹ"), _t("multi_choose_action", len(selections))
            )
        )
        print("  {}  1. {}".format(_dim(" "), _t("action_install")))
        print("  {}  2. {}".format(_dim(" "), _t("action_upgrade")))
        print("  {}  3. {}".format(_dim(" "), _t("action_uninstall")))
        valid = {"1": "install", "2": "upgrade", "3": "uninstall"}
        hint = _t("multi_action_hint_3")
        while True:
            raw = input("\n  {} {} {} ".format(_bold("›"), _t("action_label"), _dim(hint))).strip()
            if not raw:
                return None
            if raw in valid:
                return valid[raw]
            print("  {} {}".format(_warn("⚠"), _t("invalid_option")))

    def _execute_action(selected: str, action_key: str) -> None:
        """Execute action_key for a single tool. Mutates _pending_reload as needed."""
        global _pending_reload

        tool_block = all_tools[selected]
        cfg_block = tool_block.get("configure")

        action_name_map = {
            "install": _t("action_name_install"),
            "upgrade": _t("action_name_upgrade"),
            "uninstall": _t("action_name_uninstall"),
        }
        action_label_map = {
            "install": _t("action_label_install"),
            "upgrade": _t("action_label_upgrade"),
            "uninstall": _t("action_label_uninstall"),
        }
        action_name = action_name_map[action_key]

        action_cmd = _resolve_action_command(action_key, tool_block)
        if not action_cmd:
            print(
                "\n  {} [{}] {}".format(
                    _err("✘"), _bold(selected), _t("no_action_cmd", action_name)
                )
            )
            return

        # For tools that require Git on Windows (e.g. Claude Code needs git-bash)
        if (
            action_key in ("install", "upgrade")
            and tool_block.get("needs_git")
            and os_name == "windows"
        ):
            if not ensure_git_for_tool(selected, os_name):
                return

        # For npm global installs/upgrades: check Node version and configure user prefix
        if action_key in ("install", "upgrade") and _is_npm_global_cmd(action_cmd):
            if not check_node_version_for_npm(os_name, linux_family):
                return

            if not ensure_npm_user_prefix(os_name):
                print("  {} {}".format(_warn("⚠"), _t("npm_prefix_failed")))
                print("  {} {}".format(_dim("↳"), _t("try_manually", _info(action_cmd))))
                return

        # Wrap in profile shell if binary is only available there
        token = first_command_token(action_cmd)
        if token and os_name in ("linux", "macos"):
            if not which(token) and binary_available_in_profile_shell(token, os_name):
                action_cmd = command_in_profile_shell(action_cmd, os_name)

        elevated = with_linux_elevation(action_cmd, os_name)
        if elevated is None:
            print(
                "\n  {} [{}] {}".format(
                    _err("✘"), _bold(selected), _t("sudo_not_found")
                )
            )
            return
        if elevated != action_cmd:
            print("  {} [{}] {}".format(_warn("⚠"), selected, _t("using_sudo")))
        action_cmd = elevated

        is_live = _is_live_cmd(action_cmd)
        label = "{} {}".format(action_label_map[action_key], selected)
        result = run_install_command(action_cmd, os_name, label)

        if result.returncode == 0:
            if action_key == "uninstall":
                post_state = tool_access_state(tool_block, os_name)
                run_bin = tool_block.get("run", selected)
                bin_path = which(run_bin)
                if post_state == "missing":
                    print(
                        "  {} [{}] {}".format(
                            _ok("✔"), _bold(selected), _t("uninstalled_success")
                        )
                    )
                else:
                    print(
                        "  {} [{}] {}".format(
                            _warn("⚠"), _bold(selected), _t("uninstall_but_binary")
                        )
                    )
                    if bin_path:
                        print(
                            "  {}   {}".format(
                                _dim(" "), _t("remaining_binary", _dim(bin_path))
                            )
                        )
            else:
                persisted = persist_user_bin_path(os_name)
                if persisted:
                    print(
                        "  {} {}".format(
                            _info("ℹ"), _t("path_persisted", ", ".join(persisted))
                        )
                    )

                # Windows: add tool-specific directories to user PATH if needed
                path_entry_block = tool_block.get("path_entry")
                if path_entry_block and os_name in path_entry_block:
                    _ensure_path_entry(path_entry_block[os_name], os_name)

                # Windows: refresh PATH from registry so we can detect newly
                # installed binaries (winget, msi, etc. update the registry but
                # the running Python process still holds the old PATH snapshot).
                if os_name == "windows":
                    refresh_windows_path_from_registry()

                new_version = detect_tool_version(selected, tool_block, os_name)

                needs_reload = (
                    is_live
                    or bool(persisted)
                    or tool_access_state(tool_block, os_name) == "profile"
                )

                run_bin = tool_block.get("run", selected)
                bin_path = which(run_bin)

                if new_version:
                    if needs_reload:
                        print(
                            "  {} [{}] {} {}".format(
                                _ok("✔"), _bold(selected), _t("installed_excl"), _dim("(" + new_version + ")")
                            )
                        )
                    else:
                        print(
                            "  {} [{}] {} {}".format(
                                _ok("✔"), _bold(selected), _t("ready_excl"), _dim("(" + new_version + ")")
                            )
                        )
                else:
                    if needs_reload or bin_path:
                        print("  {} [{}] {}".format(_ok("✔"), _bold(selected), _t("installed_excl")))
                    else:
                        print(
                            "  {} [{}] {}".format(
                                _warn("⚠"), _bold(selected), _t("installed_but_no_path")
                            )
                        )

                if needs_reload or (not new_version and bin_path):
                    if bin_path:
                        print("  {}   {}".format(_dim(" "), _t("binary_label", _dim(bin_path))))
                    _pending_reload = True
                    if ask_yes_no(
                        _t("reload_shell", _bold(run_bin)),
                        default_yes=True,
                    ):
                        _exec_shell_reload(os_name)

                # ── Offer API key setup right after a fresh install ──────────────
                if (
                    action_key == "install"
                    and cfg_block
                    and not _api_key_configured(cfg_block)
                ):
                    if ask_yes_no(
                        _t("configure_key_now", _bold(selected)),
                        default_yes=True,
                    ):
                        configure_api_key(selected, cfg_block, os_name)
                return True
        else:
            print(
                "  {} [{}] {}".format(
                    _err("✘"), _bold(selected), _t("failed_code", result.returncode)
                )
            )
        return False

    _clear_before_scan = False

    while True:
        if _clear_before_scan:
            os.system("cls" if sys.platform == "win32" else "clear")
        _clear_before_scan = False

        # ── Scan versions in parallel with a live animated progress bar ──
        # A ticker thread advances the spinner frame every ~90ms while reading
        # the shared percent value that the scan callback keeps up to date.
        # Only the ticker writes to stdout so there is no interleaving.
        scan_label = _t("checking_tools")
        scan_percent = [0]
        scan_stop = threading.Event()

        def _on_scan_progress(done: int, total: int) -> None:
            scan_percent[0] = int(done * 100 / total) if total else 100

        def _scan_ticker() -> None:
            i = 0
            while not scan_stop.is_set():
                frame = _SPINNER[i % len(_SPINNER)]
                print(
                    _render_bar(scan_label, scan_percent[0], spinner=frame),
                    end="",
                    flush=True,
                )
                i += 1
                time.sleep(0.09)

        ticker = threading.Thread(target=_scan_ticker, daemon=True)
        ticker.start()
        version_map = scan_all_versions(
            all_tools, os_name, on_progress=_on_scan_progress
        )
        scan_stop.set()
        ticker.join()
        # Final frame at 100% then clear the line before the table.
        print(
            _render_bar(scan_label, 100, spinner=_SPINNER[0]),
            end="",
            flush=True,
        )
        print(
            "\r" + " " * (_vlen(scan_label) + _BAR_W + 16) + "\r",
            end="",
            flush=True,
        )

        print_tools_table(tools, ides, version_map, os_name)

        selections = choose_tool(names)
        if selections is None:
            print("\n  {} {}".format(_dim("→"), _t("exiting")))
            return

        if len(selections) == 1:
            # ── Single tool: show info + full action menu (incl. configure) ──
            selected = selections[0]
            tool_block = all_tools[selected]
            current_version = version_map.get(selected)
            cfg_block = tool_block.get("configure")
            author = tool_block.get("author", "")
            description = _desc(tool_block)

            if author or description:
                info_parts = []
                if author:
                    info_parts.append(author)
                if description:
                    info_parts.append(description)
                print("\n  {} {}".format(_info("ℹ"), _dim(" — ".join(info_parts))))

            action_key = _choose_action(selected, current_version, cfg_block)
            if action_key is None:
                continue

            if action_key == "configure":
                configure_api_key(selected, cfg_block, os_name)
                continue

            if _execute_action(selected, action_key):
                _press_any_key()
                _clear_before_scan = True

        else:
            # ── Multiple tools: ask action once, run each in sequence ──
            action_key = _choose_action_multi(selections)
            if action_key is None:
                continue

            print(
                "\n  {} {}".format(
                    _info("→"),
                    _t("multi_starting", len(selections), ", ".join(_bold(s) for s in selections))
                )
            )
            any_success = False
            for i, selected in enumerate(selections, 1):
                print(
                    "\n  {} {}".format(
                        _dim(_t("multi_progress", i, len(selections), selected)),
                        ""
                    )
                )
                if _execute_action(selected, action_key):
                    any_success = True
            if any_success:
                _press_any_key()
                _clear_before_scan = True


# ─── Entry Point ──────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point: bootstrap the environment, then run the interactive tool manager."""
    os.system("cls" if sys.platform == "win32" else "clear")
    script_dir = Path(__file__).resolve().parent
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else script_dir / "config.json"
    config = load_config(str(config_path))
    print_banner()
    ask_language(config)
    os_name = detect_os()
    linux_family = detect_linux_family() if os_name == "linux" else None

    added_paths = bootstrap_user_bin_path(os_name)
    persisted_paths = persist_user_bin_path(os_name)

    sys_label = "{} ({})".format(os_name, linux_family) if linux_family else os_name
    print("  {} {}".format(_info("ℹ"), _t("system_label", _bold(sys_label))))

    if added_paths:
        print(
            "  {} {}".format(
                _info("ℹ"), _t("path_adjusted", ", ".join(added_paths))
            )
        )
    if persisted_paths:
        print(
            "  {} {}".format(
                _info("ℹ"), _t("path_persisted_next", ", ".join(persisted_paths))
            )
        )

    print_section(_t("prerequisite_section"))
    ensure_prerequisite(config, os_name, linux_family)

    manage_tools(config, os_name, linux_family)

    print("\n  {} {}\n".format(_ok("✔"), _t("completed")))

    # Always reload the shell at exit when an install touched the PATH —
    # guarantees binaries are immediately usable without manual action.
    if _pending_reload:
        _exec_shell_reload(os_name)


if __name__ == "__main__":
    main()
