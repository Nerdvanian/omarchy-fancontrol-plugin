# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.0] - 2026-09-05

### Added

- A "?" help button in the panel header, replacing the paragraph of usage
  tips that used to sit at the bottom of the panel. It opens a dismissible
  overlay with quick FAQ-style questions and answers (adding/removing a
  curve point, renaming a fan, pinning a manual speed, adding a GPU
  curve) instead of one long run-on sentence.

## [1.3.0] - 2026-09-04

### Added

- `scripts/fancontrol-daemon-install` / `fancontrol-daemon-uninstall`:
  the daemon patch and its systemd unit are now applied by a transactional
  installer instead of manual `cp`/`sudo systemctl restart` steps —
  hash-verified against `daemon-patches/manifest.json`, backed up before
  every change, and automatically rolled back with the service restarted
  and re-verified if the new daemon doesn't come up healthy.
  `fancontrol-daemon-uninstall --restore`/`--full` can put the daemon (and
  now the unit) back to the pre-plugin snapshot the installer pins on its
  first run.
- `daemon-patches/`: the patched daemon now pings systemd
  (`sd_notify(WATCHDOG=1)`) once per poll, and the unit adds
  `WatchdogSec=15` so a hung (not crashed) daemon gets killed and
  restarted automatically. A new `ExecStopPost=fancontrol_recover_pwm.py`
  restores any pwm header's `pwm_enable` the daemon didn't get to restore
  itself — covers `SIGKILL`/OOM, not just a clean exit.
- `daemon-patches/`: every value read from `config.yaml` (curve points,
  hysteresis, min/max/manual percent, poll interval) is now validated on
  load — rejected if not a finite number, clamped if merely out of range.
  A single malformed curve is skipped and logged instead of taking the
  whole daemon down with it, and a config reload that fails outright
  falls back to the last-known-good config instead of crashing.
  `fancontrol-graph-write` gained the same explicit NaN/Infinity
  rejection on the write side.

## [1.2.0] - 2026-09-02

### Added

- Live tooltip on the curve graph: hovering or dragging a point shows its
  temperature and fan speed, updating in real time as the point moves.

## [1.1.1] - 2026-09-02

### Fixed

- The CPU and GPU curve tabs would freeze on whichever curve was dragged
  first, showing the same graph for both from then on -- the curves were
  actually still being saved separately, but the editor stopped
  reflecting it. Dragging, adding, or removing a point now updates an
  internal copy instead of overwriting the property the panel uses to
  switch between tabs.

## [1.1.0] - 2026-09-02

### Added

- Copy a curve (CPU or GPU) from another fan instead of reshaping every
  fan's curve from scratch — a "Copy curve from:" dropdown next to the
  CPU/GPU toggle.

## [1.0.1] - 2026-09-02

### Fixed

- Panel now opens anchored under the bar icon instead of centered across
  the whole screen.

### Added

- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and GitHub issue
  templates.
- Uninstall instructions in the README.

## [1.0.0] - 2026-09-02

### Added

- Bar pill showing live CPU and GPU temperature, with each fan's speed and
  RPM on hover.
- Draggable graph editor for fan curves: drag a point to reshape the
  curve, double-click empty space to add one, double-click a point to
  remove it.
- Manual speed override per fan, with a slider, independent of any curve.
- Per-fan renaming, so fans aren't stuck showing the auto-detected
  `<chip>_pwmN` name.
- Fan removal (with confirmation) for headers detection paired with
  nothing actually connected — hands that pwm back to firmware/BIOS auto
  control.
- CPU/GPU dual curves: an optional second curve per fan driven by GPU
  temperature (via `nvidia-smi`, since GPU temp isn't exposed through
  `hwmon`). The daemon decides once per poll whether the CPU or GPU is
  hotter, with its own hysteresis band to avoid flapping at the
  crossover, and switches every fan that has a GPU curve onto it
  accordingly.
- `daemon-patches/`: the patched `fancontrold.py` and
  `fancontrol_detect.py` this plugin depends on, plus install
  instructions.

### Fixed

- A hysteresis bug in the stock daemon where a curve's very first
  evaluation never initialized its "how far has temp dropped since the
  peak" tracking, which could leave a fan stuck at too high a speed
  indefinitely regardless of temperature.
- A sticky-speed bug where returning from manual override, or a fan
  switching between its CPU and GPU curve, could leave the fan pinned at
  the old speed instead of re-evaluating fresh against the newly active
  curve.

[Unreleased]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/releases/tag/v1.0.0
