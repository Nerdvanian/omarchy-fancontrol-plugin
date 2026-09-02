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
import os
import signal
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

_running = True


def _handle_stop(signum, frame):
    global _running
    _running = False


signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)


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
        self.points = [tuple(p) for p in spec["points"]]
        self.hysteresis_c = float(spec.get("hysteresis_c", 3))
        self.min_percent = float(spec.get("min_percent", 0))
        self.max_percent = float(spec.get("max_percent", 100))
        # Optional second curve driven by GPU temp instead of CPU temp.
        # When present, main() decides once per poll whether the CPU or
        # the GPU is hotter (system-wide, not per curve) and every curve
        # that has a gpu profile switches to it while the GPU is hotter;
        # curves without one always stay on the points/hysteresis_c above.
        gpu_spec = spec.get("gpu")
        if gpu_spec:
            self.gpu_points = [tuple(p) for p in gpu_spec["points"]]
            self.gpu_hysteresis_c = float(gpu_spec.get("hysteresis_c", self.hysteresis_c))
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
        self.manual_percent = None if manual_percent is None else float(manual_percent)

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
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    poll_interval_s = float(data.get("poll_interval_s", 2))
    gpu_source_hysteresis_c = float(data.get("gpu_source_hysteresis_c", 2))
    curves = [Curve(c) for c in data.get("curves", [])]
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

    poll_interval_s, gpu_source_hysteresis_c, curves = load_config(args.config)
    last_mtime = os.stat(args.config).st_mtime
    log.info("loaded %d curve(s) from %s", len(curves), args.config)

    hotter_source = "cpu"

    try:
        while _running:
            try:
                mtime = os.stat(args.config).st_mtime
                if mtime != last_mtime:
                    log.info("config changed, reloading")
                    for c in curves:
                        c.restore()
                    poll_interval_s, gpu_source_hysteresis_c, curves = load_config(args.config)
                    last_mtime = mtime
            except OSError:
                pass

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
            time.sleep(poll_interval_s)
    finally:
        for c in curves:
            c.restore()


if __name__ == "__main__":
    main()
