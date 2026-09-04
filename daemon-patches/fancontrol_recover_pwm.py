#!/usr/bin/env python3
"""Best-effort pwm_enable recovery, run by omarchy-fancontrol.service's
ExecStopPost= on every stop -- clean, crashed, or watchdog-killed.

fancontrold.py persists {pwm_enable_path: original_value} to
/run/omarchy-fancontrol/manual-control.json whenever it takes control of a
pwm header, and clears each entry as it restores that header on a clean
exit. If the daemon dies before it can clean up -- SIGKILL, OOM, a
systemd watchdog kill after a hang -- this puts back whatever's left in
that file, so a pwm never stays stuck pinned in manual mode with nothing
left to drive it.

Runs unconditionally on every stop and is idempotent: an empty or missing
state file (the normal case, since the daemon's own restore() already
cleared it) is a no-op, not an error.
"""

import json
import os

STATE_PATH = "/run/omarchy-fancontrol/manual-control.json"


def write_int(path, value):
    with open(path, "w") as f:
        f.write(str(int(value)))


def main():
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return

    for pwm_enable_path, original_value in state.items():
        if original_value is None:
            continue
        try:
            write_int(pwm_enable_path, original_value)
            print(f"fancontrol_recover_pwm: restored pwm_enable={original_value} on {pwm_enable_path}")
        except OSError as e:
            print(f"fancontrol_recover_pwm: could not restore {pwm_enable_path}: {e}")

    try:
        os.remove(STATE_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    main()
