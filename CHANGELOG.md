# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Nerdvanian/omarchy-fancontrol-plugin/releases/tag/v1.0.0
