"""Shared helpers for fancontrol-daemon-install and fancontrol-daemon-uninstall.

Not a user-facing script -- imported via sys.path, never invoked directly.
"""

import hashlib
import json
import os
import subprocess
import time

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_DIR = os.path.join(PLUGIN_DIR, "daemon-patches")

# Stock Omarchy install location. This is user-writable (it's under the
# invoking user's own home directory), which is fine for the *stock*
# unpatched daemon since nothing privileged points at it once our unit is
# installed -- but it must never be where the systemd unit executes
# anything from. Used only as: (a) the precondition check that a base
# omarchy-fancontrol install exists, and (b) the source of the very first
# backup, taken before this plugin has patched anything.
DAEMON_DIR = os.path.expanduser("~/.local/share/omarchy-fancontrol")

# Root-owned, non-user-writable location the patched daemon files actually
# run from once installed. /usr/local/lib and everything above it are
# owned by root on any standard install, so only sudo (i.e. this
# installer) can ever change what's here -- an unprivileged user who
# controls their own home directory can no longer overwrite what the
# systemd unit executes as root.
DAEMON_LIB_DIR = "/usr/local/lib/omarchy-fancontrol"

BACKUP_ROOT = os.path.join(DAEMON_DIR, ".fancontrol-plugin-backup")
ORIGINAL_DIR = os.path.join(BACKUP_ROOT, "original")
STATE_PATH = os.path.join(DAEMON_DIR, ".fancontrol-plugin-state.json")
STATUS_PATH = "/run/omarchy-fancontrol/status.json"
CONFIG_DIR = os.path.expanduser("~/.config/omarchy-fancontrol")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
SERVICE = "omarchy-fancontrol.service"

UNIT_PATH = "/etc/systemd/system/omarchy-fancontrol.service"
UNIT_TEMPLATE_NAME = "omarchy-fancontrol.service.tmpl"
UNIT_BACKUP_NAME = "omarchy-fancontrol.service"  # filename used for it inside a backup dir

PATCHED_FILES = ["fancontrold.py", "fancontrol_detect.py", "fancontrol_recover_pwm.py"]

VERIFY_TIMEOUT_S = 15
VERIFY_POLL_S = 0.5


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_sudo(args, description):
    print(f"-> {description} (sudo)")
    result = subprocess.run(["sudo"] + args)
    if result.returncode != 0:
        raise SystemExit(f"'{' '.join(args)}' failed (exit {result.returncode})")


def sudo_install_file(src, dst, mode="0644"):
    """Copy src to dst via sudo, atomically -- writes to dst+'.new' first
    and renames over dst -- so a crash mid-copy never leaves dst
    half-written. For root-owned paths (e.g. the systemd unit, or the
    files under DAEMON_LIB_DIR) we can't write to directly. Explicitly
    (re)sets root:root ownership and a non-group/other-writable mode on
    the staged copy before the rename, rather than trusting whatever `cp`
    and the caller's umask would have produced, so a file the daemon
    executes as root can never end up writable by the invoking user."""
    staged = dst + ".new"
    run_sudo(["cp", src, staged], f"staging {os.path.basename(dst)}")
    run_sudo(["chown", "root:root", staged], f"setting ownership on {os.path.basename(dst)}")
    run_sudo(["chmod", mode, staged], f"setting permissions on {os.path.basename(dst)}")
    run_sudo(["mv", staged, dst], f"installing {os.path.basename(dst)}")


def ensure_lib_dir():
    """Create DAEMON_LIB_DIR if needed, root-owned and not writable by
    anyone else. Idempotent -- safe to call on every install."""
    run_sudo(
        ["install", "-d", "-m", "0755", "-o", "root", "-g", "root", DAEMON_LIB_DIR],
        f"ensuring {DAEMON_LIB_DIR} exists and is root-owned",
    )


def systemctl_daemon_reload():
    run_sudo(["systemctl", "daemon-reload"], "reloading systemd unit files")


def service_is_active():
    result = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE])
    return result.returncode == 0


def status_is_fresh(since):
    try:
        with open(STATUS_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("updated", 0) >= since


def wait_for_healthy(since, timeout_s=VERIFY_TIMEOUT_S):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if service_is_active() and status_is_fresh(since):
            return True
        time.sleep(VERIFY_POLL_S)
    return False


def _current_lib_source_dir():
    """Where the currently-live patched files are, so we know both where
    to read them from for a backup and where a later restore of *this*
    backup needs to write them back to. Prefers the root-owned
    DAEMON_LIB_DIR once this plugin has switched to it; falls back to the
    legacy home-directory location for a first-ever install (nothing has
    patched anything yet, so only Omarchy's stock files exist, under
    DAEMON_DIR) or on a machine still running a pre-fix copy of this
    plugin's patch."""
    return DAEMON_LIB_DIR if os.path.isdir(DAEMON_LIB_DIR) else DAEMON_DIR


def backup_current(reason):
    """Copy whatever's currently installed into a fresh timestamped backup
    dir, plus into original/ the first time this ever runs on this machine.
    Returns the timestamped backup dir."""
    import shutil

    source_dir = _current_lib_source_dir()

    os.makedirs(BACKUP_ROOT, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}"
    backup_dir = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(backup_dir)

    hashes = {}
    for name in PATCHED_FILES:
        src = os.path.join(source_dir, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(backup_dir, name)
        # DAEMON_LIB_DIR's files are root-owned but world-readable
        # (mode 0644), so this read never needs sudo -- only a *write*
        # back into DAEMON_LIB_DIR does (see restore_from).
        shutil.copy2(src, dst)
        hashes[name] = sha256_file(dst)

    if os.path.exists(UNIT_PATH):
        # Just a read of a world-readable /etc file into our own backup
        # dir -- no sudo needed. Writing a *restored* copy back to
        # UNIT_PATH is the part that needs sudo (see restore_unit_from).
        dst = os.path.join(backup_dir, UNIT_BACKUP_NAME)
        shutil.copy2(UNIT_PATH, dst)
        hashes[UNIT_BACKUP_NAME] = sha256_file(dst)

    with open(os.path.join(backup_dir, "meta.json"), "w") as f:
        json.dump(
            {"reason": reason, "taken_at": stamp, "source_dir": source_dir, "hashes": hashes},
            f,
            indent=2,
        )

    if not os.path.exists(ORIGINAL_DIR):
        shutil.copytree(backup_dir, ORIGINAL_DIR)
        print(f"-> pinned first-ever backup as {ORIGINAL_DIR}")

    print(f"-> backed up currently installed files to {backup_dir}")
    return backup_dir


def restore_from(backup_dir):
    """Restore the patched .py files from a backup dir back to wherever
    they were captured from (recorded in that backup's meta.json) --
    DAEMON_LIB_DIR via sudo for any backup taken after this plugin
    switched to the root-owned location, or the legacy user-writable
    DAEMON_DIR (no sudo needed) for the pinned pre-plugin `original/`
    snapshot, since that's where the stock unit being restored alongside
    it expects to find them. Does not touch the systemd unit itself --
    that needs sudo, see restore_unit_from."""
    import shutil

    target_dir = DAEMON_LIB_DIR
    meta_path = os.path.join(backup_dir, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            target_dir = json.load(f).get("source_dir", DAEMON_LIB_DIR)

    for name in PATCHED_FILES:
        src = os.path.join(backup_dir, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(target_dir, name)
        if target_dir == DAEMON_LIB_DIR:
            ensure_lib_dir()
            sudo_install_file(src, dst)
        else:
            shutil.copy2(src, dst)
    print(f"-> restored files from {backup_dir} to {target_dir}")


def restore_unit_from(backup_dir):
    """Restore the systemd unit file from a backup dir, via sudo, and
    daemon-reload so systemd picks it up. Returns False (no-op) if the
    backup doesn't contain a unit snapshot -- e.g. a backup taken before
    this plugin started patching the unit at all."""
    src = os.path.join(backup_dir, UNIT_BACKUP_NAME)
    if not os.path.exists(src):
        return False
    sudo_install_file(src, UNIT_PATH)
    systemctl_daemon_reload()
    print(f"-> restored systemd unit from {backup_dir}")
    return True
