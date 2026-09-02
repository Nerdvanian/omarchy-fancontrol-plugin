# Daemon patches

`omarchy-fancontrol`'s daemon (`~/.local/share/omarchy-fancontrol/`) is a
vendored part of Omarchy itself, not something this plugin can carry as
part of its own install — `omarchy plugin add` only touches
`~/.config/omarchy/plugins/`. These two files add everything the panel's
manual override, GPU curves, fan removal, and renaming rely on, plus a
correctness fix:

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

## Install

```bash
cp daemon-patches/fancontrold.py ~/.local/share/omarchy-fancontrol/fancontrold.py
cp daemon-patches/fancontrol_detect.py ~/.local/share/omarchy-fancontrol/fancontrol_detect.py
sudo systemctl restart omarchy-fancontrol.service
```

These replace the stock files outright rather than applying as a diff —
review them against your installed copy first if you've made other local
changes to the daemon. An Omarchy update that touches these files could
overwrite this patch; if `sudo pacman -Syu` (or equivalent) updates
Omarchy, re-apply if the fan-control features stop working.
