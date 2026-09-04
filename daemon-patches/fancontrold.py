#!/usr/bin/env python3
"""Omarchy Fan Control daemon.

Applies temperature -> PWM curves to hwmon fan headers, FanControl-style.
Curves are defined in a YAML config and hot-reload on save. Must run as
root (hwmon pwm* writes require it). Restores each pwm's original
pwm_enable value on exit so fans always fall back to firmware/auto control
if this daemon isn't running.
"""

import argparse
import glob
import json
import logging
import math
import os
import signal
import socket
import subprocess
import sys
import time

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("omarchy-fancontrold")

STATUS_PATH = "/run/omarchy-fancontrol/status.json"
MANUAL_CONTROL_PATH = "/run/omarchy-fancontrol/manual-control.json"

_running = True


def _handle_stop(signum, frame):
    global _running
    _running = False


signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)


_notify_socket = os.environ.get("NOTIFY_SOCKET")


def sd_notify(message):
    """Best-effort sd_notify(3), reimplemented without a systemd dependency
    (just the documented AF_UNIX datagram protocol). No-op if not launched
    under systemd (NOTIFY_SOCKET unset) or if the send fails for any
    reason -- notifying systemd is a nice-to-have for READY=1/WATCHDOG=1,
    never something worth crashing the daemon over."""
    if not _notify_socket:
        return
    addr = _notify_socket
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode())
    except OSError:
        pass


def load_manual_control_state():
    try:
        with open(MANUAL_CONTROL_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_manual_control_state(state):
    tmp_path = MANUAL_CONTROL_PATH + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(state, f)
        os.replace(tmp_path, MANUAL_CONTROL_PATH)
    except OSError as e:
        log.warning("failed to persist manual-control state: %s", e)


def _record_manual_control(pwm_enable_path, original_value):
    """Persist {pwm_enable_path: original_value} to disk as each pwm is
    taken over, so fancontrol_recover_pwm.py (run by the service's
    ExecStopPost=) can still put it back even if this process is killed
    before its own finally/restore() ever runs."""
    if pwm_enable_path is None:
        return
    state = load_manual_control_state()
    state[pwm_enable_path] = original_value
    save_manual_control_state(state)


def _clear_manual_control(pwm_enable_path):
    if pwm_enable_path is None:
        return
    state = load_manual_control_state()
    if pwm_enable_path in state:
        del state[pwm_enable_path]
        save_manual_control_state(state)


def find_hwmon_by_chip(chip_name):
    for name_path in glob.glob("/sys/class/hwmon/hwmon*/name"):
        try:
            with open(name_path) as f:
                if f.read().strip() == chip_name:
                    return os.path.dirname(name_path)
        except OSError:
            continue
    return None


def read_int(path):
    with open(path) as f:
        return int(f.read().strip())


def write_int(path, value):
    with open(path, "w") as f:
        f.write(str(int(value)))


def find_temp_input(hwmon_dir, label):
    for label_path in glob.glob(os.path.join(hwmon_dir, "temp*_label")):
        try:
            with open(label_path) as f:
                if f.read().strip() == label:
                    input_path = label_path.replace("_label", "_input")
                    if os.path.exists(input_path):
                        return input_path
        except OSError:
            continue
    return None


def read_gpu_temp_c():
    """Current NVIDIA GPU temp via nvidia-smi, or None if unavailable.
    GPU temps aren't exposed through hwmon on this system (no chip for it
    shows up under /sys/class/hwmon at all with the proprietary driver),
    so this is the one sensor read in the daemon that isn't a plain file
    read -- nvidia-smi is a real subprocess call each poll."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=1.5,
        )
        if out.returncode != 0:
            return None
        return float(out.stdout.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


_cpu_ref_path = None


def read_cpu_ref_temp_c():
    """CPU package temp, resolved once and cached, independent of any
    particular curve's own sensor -- used as the "is the CPU or the GPU
    hotter" reference regardless of which curves exist."""
    global _cpu_ref_path
    if _cpu_ref_path is None:
        hwmon = find_hwmon_by_chip("coretemp")
        if hwmon:
            _cpu_ref_path = find_temp_input(hwmon, "Package id 0")
    if not _cpu_ref_path:
        return None
    try:
        return read_int(_cpu_ref_path) / 1000.0
    except OSError:
        return None


def finite_float(value, low, high):
    """Coerce value to a float, reject NaN/Infinity outright (Python's
    float() and JSON both happily parse them, but a curve built from one
    would silently misbehave -- see clamp() in fancontrol-graph-write for
    the same concern on the write side), and clamp anything merely
    out-of-range into [low, high]. Raises TypeError/ValueError for
    anything that isn't a plain finite number -- callers that construct a
    whole Curve from a config entry let that propagate so the curve gets
    skipped rather than silently running with a bogus value."""
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"expected a finite number, got {value!r}")
    return max(low, min(high, v))


def validate_points(raw_points):
    """A curve's points list, each pair clamped to the same [0, 100]
    temp/percent range fancontrol-graph-write enforces on the write side
    -- this is the belt to that suspenders, since config.yaml can also be
    hand-edited or written by something else entirely."""
    if not isinstance(raw_points, list) or len(raw_points) < 2:
        raise ValueError("points must be a list of at least 2 [temp_c, percent] pairs")
    points = []
    for p in raw_points:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError(f"each point must be [temp_c, percent], got {p!r}")
        points.append((finite_float(p[0], 0, 100), finite_float(p[1], 0, 100)))
    return points


def interpolate(points, temp_c):
    pts = sorted(points, key=lambda p: p[0])
    if temp_c <= pts[0][0]:
        return pts[0][1]
    if temp_c >= pts[-1][0]:
        return pts[-1][1]
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        if t0 <= temp_c <= t1:
            if t1 == t0:
                return p1
            frac = (temp_c - t0) / (t1 - t0)
            return p0 + frac * (p1 - p0)
    return pts[-1][1]


class Curve:
    def __init__(self, spec):
        self.name = spec["name"]
        self.sensor_chip = spec["sensor"]["chip"]
        self.sensor_label = spec["sensor"]["label"]
        self.pwm_chip = spec["pwm"]["chip"]
        self.pwm_index = int(spec["pwm"]["index"])
        self.fan_index = int(spec["pwm"].get("fan_index", self.pwm_index))
        self.points = validate_points(spec["points"])
        self.hysteresis_c = finite_float(spec.get("hysteresis_c", 3), 0, 30)
        self.min_percent = finite_float(spec.get("min_percent", 0), 0, 100)
        self.max_percent = finite_float(spec.get("max_percent", 100), 0, 100)
        # Optional second curve driven by GPU temp instead of CPU temp.
        # When present, main() decides once per poll whether the CPU or
        # the GPU is hotter (system-wide, not per curve) and every curve
        # that has a gpu profile switches to it while the GPU is hotter;
        # curves without one always stay on the points/hysteresis_c above.
        gpu_spec = spec.get("gpu")
        if gpu_spec:
            self.gpu_points = validate_points(gpu_spec["points"])
            self.gpu_hysteresis_c = finite_float(gpu_spec.get("hysteresis_c", self.hysteresis_c), 0, 30)
        else:
            self.gpu_points = None
            self.gpu_hysteresis_c = None
        self._active_source = "cpu"  # "cpu" or "gpu" -- which profile last drove this curve
        # Manual speed override: when set, step() applies this percent
        # directly instead of evaluating the curve. None means auto
        # (curve-controlled). Distinct from take_manual_control()/
        # took_manual_control below, which is about owning the hwmon
        # pwm*_enable file rather than the temp curve.
        manual_percent = spec.get("manual_percent")
        self.manual_percent = None if manual_percent is None else finite_float(manual_percent, 0, 100)

        self.temp_input = None
        self.pwm_path = None
        self.pwm_enable_path = None
        self.fan_input_path = None
        self.original_pwm_enable = None
        self.took_manual_control = False

        self.applied_percent = None
        self.peak_temp = None
        self._was_manual = False

    def resolve(self):
        if self.temp_input is None:
            hwmon = find_hwmon_by_chip(self.sensor_chip)
            if hwmon:
                self.temp_input = find_temp_input(hwmon, self.sensor_label)
        if self.pwm_path is None:
            hwmon = find_hwmon_by_chip(self.pwm_chip)
            if hwmon:
                candidate = os.path.join(hwmon, f"pwm{self.pwm_index}")
                if os.path.exists(candidate):
                    self.pwm_path = candidate
                    self.pwm_enable_path = candidate + "_enable"
                    fan_candidate = os.path.join(hwmon, f"fan{self.fan_index}_input")
                    if os.path.exists(fan_candidate):
                        self.fan_input_path = fan_candidate
        return self.ready()

    def ready(self):
        return self.temp_input is not None and self.pwm_path is not None

    def take_manual_control(self):
        if self.took_manual_control or not self.ready():
            return
        try:
            if os.path.exists(self.pwm_enable_path):
                self.original_pwm_enable = read_int(self.pwm_enable_path)
                write_int(self.pwm_enable_path, 1)
            self.took_manual_control = True
            _record_manual_control(self.pwm_enable_path, self.original_pwm_enable)
            log.info("%s: took manual control of %s", self.name, self.pwm_path)
        except OSError as e:
            log.warning("%s: could not take manual control: %s", self.name, e)

    def restore(self):
        if not self.took_manual_control:
            return
        try:
            if self.original_pwm_enable is not None:
                write_int(self.pwm_enable_path, self.original_pwm_enable)
                log.info(
                    "%s: restored pwm_enable=%d on %s",
                    self.name,
                    self.original_pwm_enable,
                    self.pwm_path,
                )
        except OSError as e:
            log.warning("%s: could not restore pwm_enable: %s", self.name, e)
        self.took_manual_control = False
        _clear_manual_control(self.pwm_enable_path)

    def read_temp_c(self):
        return read_int(self.temp_input) / 1000.0

    def read_rpm(self):
        if not self.fan_input_path:
            return None
        try:
            return read_int(self.fan_input_path)
        except OSError:
            return None

    def step(self, hotter_source="cpu", gpu_temp_c=None):
        if not self.ready():
            return None
        try:
            cpu_temp_c = self.read_temp_c()
        except OSError as e:
            log.warning("%s: failed to read temp: %s", self.name, e)
            return None

        manual = self.manual_percent is not None
        if manual:
            # Manual override: apply directly, no curve/hysteresis logic.
            target = max(self.min_percent, min(self.max_percent, self.manual_percent))
            apply = target
            temp_c = cpu_temp_c
            source = None
            self.peak_temp = None
            self._was_manual = True
        else:
            if self._was_manual:
                # Returning to curve control: forget the manual speed and
                # any stale hysteresis peak, so the curve can move down to
                # meet the current temp immediately instead of getting
                # stuck at the last manual speed (the peak-tracking below
                # only ever lowers apply once temp drops below a peak, and
                # there is no meaningful peak left over from manual mode).
                self.applied_percent = None
                self.peak_temp = None
                self._was_manual = False

            # Only curves with their own gpu profile ever switch off cpu;
            # everything else ignores hotter_source entirely.
            use_gpu = self.gpu_points is not None and hotter_source == "gpu" and gpu_temp_c is not None
            source = "gpu" if use_gpu else "cpu"
            if source != self._active_source:
                # Switching which sensor/curve drives this fan: the old
                # peak/applied state was tracked against a different curve
                # and a different temperature, so it isn't a meaningful
                # baseline for hysteresis any more -- start fresh, same as
                # returning from manual mode above.
                self.applied_percent = None
                self.peak_temp = None
                self._active_source = source

            if source == "gpu":
                temp_c = gpu_temp_c
                points = self.gpu_points
                hysteresis_c = self.gpu_hysteresis_c
            else:
                temp_c = cpu_temp_c
                points = self.points
                hysteresis_c = self.hysteresis_c

            target = interpolate(points, temp_c)
            target = max(self.min_percent, min(self.max_percent, target))

            if self.applied_percent is None:
                apply = target
                self.peak_temp = temp_c
            elif target >= self.applied_percent:
                apply = target
                self.peak_temp = temp_c
            else:
                # Only allow stepping down once temp has dropped enough below
                # the peak that produced the current speed (hysteresis).
                if self.peak_temp is not None and temp_c <= self.peak_temp - hysteresis_c:
                    apply = target
                    self.peak_temp = temp_c
                else:
                    apply = self.applied_percent

        if self.applied_percent is None or abs(apply - self.applied_percent) >= 1:
            self.take_manual_control()
            try:
                write_int(self.pwm_path, round(apply / 100 * 255))
                self.applied_percent = apply
            except OSError as e:
                log.warning("%s: failed to write pwm: %s", self.name, e)

        return {
            "name": self.name,
            "temp_c": round(temp_c, 1) if temp_c is not None else None,
            "cpu_temp_c": round(cpu_temp_c, 1),
            "gpu_temp_c": round(gpu_temp_c, 1) if gpu_temp_c is not None else None,
            "target_percent": round(target, 1),
            "applied_percent": round(self.applied_percent, 1) if self.applied_percent is not None else None,
            "rpm": self.read_rpm(),
            "manual": manual,
            "active_source": source,
        }


def decide_hotter_source(current, cpu_temp_c, gpu_temp_c, hysteresis_c):
    """System-wide "is the CPU or the GPU hotter" decision, with its own
    hysteresis band so two temps sitting close to each other don't flip
    the decision (and every gpu-profiled curve's fan speed with it) back
    and forth every poll. `current` is the previous decision ("cpu" or
    "gpu"); returns the new one."""
    if gpu_temp_c is None or cpu_temp_c is None:
        return "cpu"
    if current == "cpu" and gpu_temp_c > cpu_temp_c + hysteresis_c:
        return "gpu"
    if current == "gpu" and cpu_temp_c > gpu_temp_c + hysteresis_c:
        return "cpu"
    return current


def load_config(path):
    """Raises on a malformed top-level config -- unreadable file, invalid
    YAML, or a poll_interval_s/gpu_source_hysteresis_c that isn't a plain
    finite number -- so callers can fall back to the last-known-good
    config instead of crashing the whole daemon over one bad edit (see
    main()). An individual malformed *curve* is narrower: it's skipped
    and logged rather than failing the whole load, since one bad curve
    shouldn't take every other fan's control away."""
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"config must be a YAML mapping, got {type(data).__name__}")

    poll_interval_s = finite_float(data.get("poll_interval_s", 2), 0.2, 60)
    gpu_source_hysteresis_c = finite_float(data.get("gpu_source_hysteresis_c", 2), 0, 30)

    curves = []
    for spec in data.get("curves") or []:
        try:
            curves.append(Curve(spec))
        except Exception as e:
            name = spec.get("name", "?") if isinstance(spec, dict) else "?"
            log.warning("skipping invalid curve %r: %s", name, e)

    return poll_interval_s, gpu_source_hysteresis_c, curves


def write_status(curves_status, hotter_source, cpu_temp_c, gpu_temp_c):
    tmp_path = STATUS_PATH + ".tmp"
    payload = {
        "updated": time.time(),
        "hotter_source": hotter_source,
        "cpu_temp_c": round(cpu_temp_c, 1) if cpu_temp_c is not None else None,
        "gpu_temp_c": round(gpu_temp_c, 1) if gpu_temp_c is not None else None,
        "curves": curves_status,
    }
    try:
        with open(tmp_path, "w") as f:
            json.dump(payload, f)
        os.replace(tmp_path, STATUS_PATH)
        os.chmod(STATUS_PATH, 0o644)
    except OSError as e:
        log.warning("failed to write status file: %s", e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=os.path.expanduser("~/.config/omarchy-fancontrol/config.yaml"),
    )
    args = parser.parse_args()

    if not os.path.exists(args.config):
        log.error("config not found: %s", args.config)
        sys.exit(1)

    try:
        poll_interval_s, gpu_source_hysteresis_c, curves = load_config(args.config)
    except Exception as e:
        log.error("failed to load %s: %s", args.config, e)
        sys.exit(1)
    last_mtime = os.stat(args.config).st_mtime
    log.info("loaded %d curve(s) from %s", len(curves), args.config)

    hotter_source = "cpu"
    sd_notify("READY=1")

    try:
        while _running:
            try:
                mtime = os.stat(args.config).st_mtime
            except OSError:
                mtime = last_mtime

            if mtime != last_mtime:
                log.info("config changed, reloading")
                for c in curves:
                    c.restore()
                try:
                    poll_interval_s, gpu_source_hysteresis_c, curves = load_config(args.config)
                except Exception as e:
                    # Keep running on the previous (already-validated) config
                    # rather than crash the daemon over one bad edit -- the
                    # curves above were just restore()'d, so they'll simply
                    # retake control on the next step() with their old
                    # settings, same as before this reload attempt.
                    log.error("failed to reload %s, keeping previous config: %s", args.config, e)
                last_mtime = mtime

            cpu_ref_temp_c = read_cpu_ref_temp_c()
            gpu_temp_c = read_gpu_temp_c()
            hotter_source = decide_hotter_source(hotter_source, cpu_ref_temp_c, gpu_temp_c, gpu_source_hysteresis_c)

            statuses = []
            for c in curves:
                if not c.ready():
                    if not c.resolve():
                        continue
                result = c.step(hotter_source, gpu_temp_c)
                if result:
                    statuses.append(result)

            write_status(statuses, hotter_source, cpu_ref_temp_c, gpu_temp_c)
            sd_notify("WATCHDOG=1")
            time.sleep(poll_interval_s)
    finally:
        sd_notify("STOPPING=1")
        for c in curves:
            c.restore()


if __name__ == "__main__":
    main()
