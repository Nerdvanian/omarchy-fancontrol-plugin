# Fan Control (Omarchy plugin)

A graphical fan-curve editor for [Omarchy](https://omarchy.org)'s
`omarchy-fancontrol` daemon. Windows-FanControl-style graph: drag points to reshape a curve,
manual speed override, per-fan renaming, and add a second curve per fan that
switches in automatically depending on whether the CPU or GPU is a higher temp.

![Fan Control panel](preview.png)

## Features

- Bar pill shows live CPU and GPU temperature at a glance; hover for each
  fan's speed and RPM.
- Click the pill to open the graph editor: drag a point to reshape the
  curve, double-click empty space to add one, double-click a point to
  remove it.
- **Manual override** — pin a fan at a fixed speed, independent of any
  curve.
- **Rename fans** — the auto-detected `<chip>_pwmN` names aren't very
  useful; give each fan a real name ("Top Exhaust", "AIO Fan", ...).
- **Remove a fan** from the config (with confirmation) if detection paired
  it with a header nothing is actually plugged into — hands that pwm back
  to firmware/BIOS auto control.
- **CPU/GPU dual curves** — give a fan a second curve driven by GPU temp;
  the daemon compares CPU vs. GPU temp once per poll (with its own
  hysteresis band, so it doesn't flap right at the crossover) and switches
  every fan that has a GPU curve onto it while the GPU is the hotter one.
  Fans without a GPU curve just keep responding to CPU temp as normal.
- **Copy a curve between fans** — pick another fan from the dropdown and
  copy its CPU (or GPU) curve onto the one you're editing, instead of
  reshaping every fan's curve from scratch.

## Requirements

**This plugin needs a patched `omarchy-fancontrol` daemon.** The stock
daemon that ships with Omarchy doesn't know about manual override, GPU
curves, or fan removal/labels — those are daemon-side features, not just
UI. See [`daemon-patches/`](daemon-patches) for the patched
`fancontrold.py` and `fancontrol_detect.py` (a diff against the stock
files, plus install steps). Until/unless this lands upstream in Omarchy
itself, you need to apply those patches manually.

## Install

```bash
omarchy plugin add https://github.com/Nerdvanian/omarchy-fancontrol-plugin.git --enable --yes
```

Then apply the daemon patches (see [`daemon-patches/README.md`](daemon-patches/README.md)) and restart the daemon:

```bash
sudo systemctl restart omarchy-fancontrol.service
```

## Uninstall

To remove just the plugin (bar pill + panel), leaving the daemon and your
curves running exactly as they are — you'll lose the graphical editor, but
`omarchy-fancontrol` keeps applying whatever's in `config.yaml`, and you
can still edit it by hand or with `~/.local/share/omarchy-fancontrol/fancontrol-watch`:

```bash
omarchy plugin remove nerdvanian.fancontrol
```

To undo everything, including fan control itself — this hands every pwm
header back to firmware/BIOS auto control:

```bash
omarchy plugin remove nerdvanian.fancontrol
sudo systemctl disable --now omarchy-fancontrol.service
rm -rf ~/.config/omarchy-fancontrol
```

Stopping the service restores each pwm's original `pwm_enable` value
automatically (that's built into the daemon, not something this cleanup
does) — the fans return to normal firmware control the moment it stops.

Note there's no known way to revert `~/.local/share/omarchy-fancontrol/`
(`fancontrold.py`, `fancontrol_detect.py`) to a "stock" version on its
own — nothing on a stock Omarchy install owns those files as a package, so
there's nothing to reinstall over them. If you disable the service as
above they're simply inert; delete
`~/.local/share/omarchy-fancontrol/` too if you want them gone entirely.

## Files

| Path | What it is |
|---|---|
| `manifest.json` | Plugin registration |
| `BarWidget.qml` | The bar pill |
| `Panel.qml` | The popup: tabs, graph wiring, manual/rename/remove controls |
| `CurveGraph.qml` | The draggable curve graph itself |
| `scripts/fancontrol-graph-data` | Reads `config.yaml` + daemon status for the panel |
| `scripts/fancontrol-graph-write` | Writes panel edits back to `config.yaml` |
| `daemon-patches/` | Patched daemon files this plugin depends on (see above) |
| `backup/config.yaml` | A worked example (the author's own curves) — not applied automatically |

## Development notes

- Structural QML changes (new properties, new child items) inside an
  already-open panel don't reliably hot-reload — Quickshell's
  "Local plugin changed" live-reload only refreshes the plugin registry,
  not an already-mounted `Loader`'s content. Run `omarchy restart shell`
  after editing `Panel.qml`/`CurveGraph.qml`/`BarWidget.qml` before
  judging whether a change worked.
- The daemon (`fancontrold.py`) only reloads `config.yaml` on save (hot);
  it does **not** reload its own source — restart the systemd service
  after editing it.
