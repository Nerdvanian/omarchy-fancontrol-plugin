# Contributing

Thanks for considering it — this is a small hobby project, so keep
expectations proportionate: no CI, no test suite, one maintainer. That said,
contributions are welcome, and this doc should get you productive quickly.

## Setup

Omarchy plugins live at `~/.config/omarchy/plugins/<plugin-id>/` and are
loaded straight from there — there's no build step. To develop against a
fork:

```bash
git clone https://github.com/<you>/omarchy-fancontrol-plugin.git \
  ~/.config/omarchy/plugins/nerdvanian.fancontrol
omarchy-shell shell rescanPlugins
omarchy plugin enable nerdvanian.fancontrol
```

You'll also need the daemon patches in place (manual override, GPU curves,
labels, and fan removal are daemon-side, not UI) — see
[`daemon-patches/README.md`](daemon-patches/README.md).

## Repo layout

See the table in [`README.md`](README.md#files) for what each file does.
The short version: `BarWidget.qml` is the bar pill, `Panel.qml` is the
popup and owns all the panel-level state/logic, `CurveGraph.qml` is the
self-contained draggable graph, and the two `scripts/` are the only things
that touch `config.yaml` (the panel never edits it directly — everything
goes through `fancontrol-graph-write`).

## The QML hot-reload gotcha

This will bite you at least once: Quickshell's "Local plugin changed,
reloading" live-reload only refreshes the plugin *registry* — it does
**not** recreate an already-open panel's `Loader` content. If you edit
`Panel.qml`/`CurveGraph.qml`/`BarWidget.qml` and the panel doesn't reflect
your change (even a trivial one, like a test `Rectangle` with a fixed
color), that's very likely why — not a bug in your change. Run:

```bash
omarchy restart shell
```

then reopen the panel, before concluding a change didn't work.

The daemon is the opposite problem: it hot-reloads `config.yaml` on save,
but never reloads its own source. After editing `daemon-patches/fancontrold.py`
(or its live copy at `~/.local/share/omarchy-fancontrol/fancontrold.py`):

```bash
sudo systemctl restart omarchy-fancontrol.service
```

## Testing

There's no test framework wired up, but daemon-side logic (curve
evaluation, hysteresis, manual override, CPU/GPU switching) is
straightforward to unit test without touching real hardware: point a
`Curve`'s `temp_input`/`pwm_path` at plain files in a scratch directory
instead of real `/sys/class/hwmon` paths, then call `.step()` directly and
assert on the returned dict. That's how every daemon change in this
project's history was verified before it touched the real system —
worth doing the same for any daemon PR, since a bad hysteresis change can
leave someone's fan pinned at an unexpected speed.

For QML changes: run `/usr/lib/qt6/bin/qmllint <file>.qml` (ignore the
`qs.Commons`/`qs.Ui` unresolved-import noise — that's expected outside a
full Quickshell environment; anything else is worth a look), then verify
visually after an `omarchy restart shell`.

Before opening a PR, also run:

```bash
omarchy plugin validate ~/.config/omarchy/plugins/nerdvanian.fancontrol
```

## Style

- No comments explaining *what* code does — names should already say
  that. A comment earns its place only when it explains a non-obvious
  *why*: a workaround (see the hot-reload note above, or the
  `_was_manual`/`_active_source` reset logic in the daemon), a hidden
  constraint, or something that would otherwise surprise a reader.
- Match the existing QML idiom for dragging: a single `MouseArea` reading
  coordinates relative to a fixed-size item (see `CurveGraph.qml` /
  `PanelSlider.qml`), not `drag.target` — the latter fights declarative
  bindings once a handle's position is data-driven.
- Keep daemon changes backward-compatible with existing `config.yaml`
  files where reasonably possible (e.g. the `gpu` curve block and
  `manual_percent` are both optional, additive fields).

## Pull requests

- Small, focused changes are much easier to review than a bundle of
  unrelated ones — split them up if you can.
- For anything beyond a small fix, open an
  [issue](../../issues) or [discussion](../../discussions) first so we're
  not both surprised at review time.
- Reference what you tested (see Testing above) in the PR description —
  there's no CI to fall back on, so that's the only evidence a reviewer
  has.

## Reporting bugs vs. security issues

Regular bugs: open an [issue](../../issues). Security vulnerabilities:
see [`SECURITY.md`](SECURITY.md) instead — please don't file those as
public issues.
