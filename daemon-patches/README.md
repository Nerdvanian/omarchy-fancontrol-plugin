# Daemon patches

`omarchy-fancontrol`'s daemon (`~/.local/share/omarchy-fancontrol/`) and
its systemd unit (`/etc/systemd/system/omarchy-fancontrol.service`) are a
vendored part of Omarchy itself, not something this plugin can carry as
part of its own install — `omarchy plugin add` only touches
`~/.config/omarchy/plugins/`. These files add everything the panel's
manual override, GPU curves, fan removal, and renaming rely on, a
correctness fix, and a crash/hang safety net:

- **Manual override** (`manual_percent` field) — pins a fan at a fixed
  speed, bypassing the curve entirely.
- **GPU-driven curves** (`gpu` field) — a second curve per fan, driven by
  GPU temp (via `nvidia-smi`, since GPU temp isn't exposed through
  `hwmon`). The daemon compares CPU vs. GPU temp once per poll, with its
  own hysteresis band, and switches every fan that has a `gpu` profile
  onto it while the GPU is hotter.
- **Fan labels** (`label` field) — purely cosmetic, read by the panel.
- **Hysteresis fix** — the stock daemon never initializes its
  "how far has temp dropped since the last peak" tracking on a curve's
  very first evaluation, which can leave a fan stuck at too high a speed
  indefinitely. Fixed here regardless of whether you use manual/GPU
  curves at all.
- **Watchdog + crash recovery** — `fancontrold.py` now pings systemd
  (`sd_notify(WATCHDOG=1)`) once per poll; the unit's `WatchdogSec=15`
  means a daemon that hangs without exiting (rather than crashing
  outright) gets killed and restarted by systemd within ~15s instead of
  silently freezing every fan at its last speed forever. Separately,
  whenever the daemon takes over a pwm header it now persists that
  header's original `pwm_enable` to
  `/run/omarchy-fancontrol/manual-control.json`; the unit's
  `ExecStopPost=fancontrol_recover_pwm.py` reads that file on *every*
  stop — clean, crashed, or watchdog-killed — and puts back whatever's
  left in it. That covers the daemon's own `finally:`-block restore not
  running at all, e.g. on `SIGKILL` or an OOM kill.

  `WatchdogSec=15` assumes the default `poll_interval_s: 2` in
  `config.yaml` (a comfortable ~7x margin over a normal poll). If you set
  `poll_interval_s` much higher than that, re-run the installer after
  editing `daemon-patches/omarchy-fancontrol.service.tmpl`'s
  `WatchdogSec=` to keep a similar margin, or the watchdog may trigger
  spuriously.
- **Input validation** — every value read from `config.yaml` (points,
  `hysteresis_c`, `min_percent`/`max_percent`, `manual_percent`,
  `poll_interval_s`) is now checked as it's loaded: rejected outright if
  it isn't a plain finite number (`NaN`/`Infinity` included — Python's
  `float()` and YAML/JSON both parse those by default), clamped into a
  sane range (0–100% for speeds/temps, 0–30°C for hysteresis, 0.2–60s for
  the poll interval) if it's merely out of range. A curve that fails
  validation is skipped and logged rather than taking the whole daemon
  down with it, and a config reload that fails entirely (bad YAML syntax,
  a top-level value of the wrong type) falls back to the last-known-good
  config instead of crashing — a bad hand-edit to `config.yaml` degrades
  to "that one curve/edit didn't take, logged in `journalctl`," never to
  "no fan control until someone finds and fixes the typo." This applies
  even though `fancontrol-graph-write` already validates the same way on
  the write side, since `config.yaml` can also be hand-edited directly or
  written by something else entirely — the daemon is the actual
  privileged component (runs as root, drives real hardware), so it's the
  one that has to not trust its input.

## Install

```bash
scripts/fancontrol-daemon-install
```

This verifies `fancontrold.py`, `fancontrol_detect.py`,
`fancontrol_recover_pwm.py`, and the systemd unit template against the
hashes pinned in `daemon-patches/manifest.json`, backs up whatever's
currently installed (daemon files and the unit file), applies the patch,
restarts `omarchy-fancontrol.service`, and confirms the daemon comes back
up healthy. If it doesn't, everything is restored and the service
restarted again automatically — nothing is left half-patched. Run
`scripts/fancontrol-daemon-install --dry-run` first to check the hashes
without changing anything.

These replace the stock files outright rather than applying as a diff —
if you've made other local changes to the daemon or unit, review them
against `daemon-patches/` first (the installer's backup will preserve
your current files either way, so they aren't lost). An Omarchy update
that touches these files could overwrite this patch; if `sudo pacman -Syu`
(or equivalent) updates Omarchy, re-run the installer if the fan-control
features stop working.
