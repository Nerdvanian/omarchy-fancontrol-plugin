# Security Policy

## Supported versions

This is a small, single-maintainer hobby project. Only the latest release
is supported — please update before reporting an issue.

| Version | Supported |
|---|---|
| latest (`main`) | ✅ |
| older tagged releases | ❌ |

## Reporting a vulnerability

**Please don't open a public issue for a security report.**

Use GitHub's private vulnerability reporting instead:

1. Go to the [Security tab](../../security) of this repository.
2. Click **Report a vulnerability**.

This opens a private draft advisory only visible to the repo owner, so
nothing is disclosed publicly until a fix is ready.

If that's not available to you for any reason, opening a normal GitHub
issue with as few technical details as possible (just "I think there's a
security issue, please contact me") is an acceptable fallback — I'll follow
up privately for the details.

There's no bug bounty and no formal SLA — this is maintained best-effort
in spare time — but I'll acknowledge a report within a few days and credit
you in the fix unless you'd rather stay anonymous.

## Scope

Worth understanding what "vulnerability" realistically means for this
project, since it isn't a typical networked app:

- This plugin runs as **unsandboxed QML/JS inside `omarchy-shell`**, the
  same trust model every Omarchy plugin uses — it already runs with your
  full user privileges. A real finding here would be something like: a
  crafted fan name, curve value, or IPC payload that lets the panel's
  scripts execute arbitrary shell commands beyond what's intended (the
  `scripts/fancontrol-graph-write` / `-data` helpers, or the daemon
  patches under `daemon-patches/`, injecting into a subprocess call, for
  example), or a bug that writes outside `~/.config/omarchy-fancontrol/`.
- The daemon (`daemon-patches/fancontrold.py`) runs **as root** (it must,
  to write `hwmon` `pwm*` files) — anything that lets *unprivileged* input
  reach it in a way that escalates beyond "control a fan speed" is very
  much in scope and taken seriously.
- Out of scope: the security of Omarchy's own shell/plugin loader itself
  (report that to [basecamp/omarchy](https://github.com/basecamp/omarchy)
  instead), and anything that already requires root or physical access to
  exploit.
