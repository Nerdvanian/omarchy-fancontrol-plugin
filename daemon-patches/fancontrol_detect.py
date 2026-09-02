#!/usr/bin/env python3
"""One-time hardware detection for Omarchy Fan Control.

Must run as root (sweeps pwm outputs). For each pwm output found on any
hwmon chip, drives it low then high and watches which fan*_input tach
reading responds, to build a pwm -> fan mapping without needing anyone to
identify headers by ear. Skips headers with no fan attached (RPM stays 0).

Run with no flags first to see what it finds (still spins fans during the
sweep, but writes nothing). Pass --apply to write the discovered curves
into the config file (existing file is backed up first).
"""

import argparse
import glob
import os
import re
import shutil
import time

DEFAULT_POINTS = [[30, 20], [50, 35], [65, 55], [80, 100]]
DEFAULT_TEMP_CHIP = "coretemp"
DEFAULT_TEMP_LABEL = "Package id 0"


def read_int(path):
    with open(path) as f:
        return int(f.read().strip())


def write_int(path, value):
    with open(path, "w") as f:
        f.write(str(int(value)))


def read_str(path):
    with open(path) as f:
        return f.read().strip()


def all_hwmon_dirs():
    return sorted(glob.glob("/sys/class/hwmon/hwmon*"))


def all_fan_inputs():
    """{fan_input_path: chip_name} for every fan*_input on the system."""
    out = {}
    for hwmon in all_hwmon_dirs():
        name_path = os.path.join(hwmon, "name")
        if not os.path.exists(name_path):
            continue
        chip = read_str(name_path)
        for fan_path in glob.glob(os.path.join(hwmon, "fan*_input")):
            out[fan_path] = chip
    return out


def read_all_rpm(fan_paths):
    readings = {}
    for p in fan_paths:
        try:
            readings[p] = read_int(p)
        except OSError:
            readings[p] = None
    return readings


def sweep_pwm(pwm_path):
    """Drive pwm_path low then high; return {fan_input_path: rpm_delta}."""
    enable_path = pwm_path + "_enable"
    fan_paths = list(all_fan_inputs().keys())

    original_enable = None
    original_value = None
    try:
        if os.path.exists(enable_path):
            original_enable = read_int(enable_path)
            write_int(enable_path, 1)
        original_value = read_int(pwm_path)

        write_int(pwm_path, 0)
        time.sleep(2)
        low = read_all_rpm(fan_paths)

        write_int(pwm_path, 255)
        time.sleep(3)
        high = read_all_rpm(fan_paths)
    finally:
        try:
            if original_value is not None:
                write_int(pwm_path, original_value)
            if original_enable is not None:
                write_int(enable_path, original_enable)
        except OSError:
            pass

    deltas = {}
    for p in fan_paths:
        if low.get(p) is None or high.get(p) is None:
            continue
        deltas[p] = high[p] - low[p]
    return deltas, high


def discover_pwm_outputs():
    outputs = []
    for hwmon in all_hwmon_dirs():
        name_path = os.path.join(hwmon, "name")
        if not os.path.exists(name_path):
            continue
        chip = read_str(name_path)
        for pwm_path in sorted(glob.glob(os.path.join(hwmon, "pwm[0-9]*"))):
            match = re.fullmatch(r"pwm(\d+)", os.path.basename(pwm_path))
            if not match:
                continue
            index = int(match.group(1))
            outputs.append((chip, index, pwm_path))
    return outputs


def discover_temp_sensors():
    sensors = []
    for hwmon in all_hwmon_dirs():
        name_path = os.path.join(hwmon, "name")
        if not os.path.exists(name_path):
            continue
        chip = read_str(name_path)
        for label_path in sorted(glob.glob(os.path.join(hwmon, "temp*_label"))):
            try:
                label = read_str(label_path)
            except OSError:
                continue
            sensors.append((chip, label))
    return sensors


def default_config_path():
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd

        home = pwd.getpwnam(sudo_user).pw_dir
    else:
        home = os.path.expanduser("~")
    return os.path.join(home, ".config", "omarchy-fancontrol", "config.yaml")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=default_config_path(),
    )
    parser.add_argument("--apply", action="store_true", help="write curves into config")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root (needs to write pwm* files)")

    print("Temperature sensors found:")
    sensors = discover_temp_sensors()
    for chip, label in sensors:
        print(f"  chip={chip!r} label={label!r}")
    print()

    outputs = discover_pwm_outputs()
    if not outputs:
        print("No pwm outputs found on any hwmon chip. Nothing to detect.")
        print("(If you just loaded a Super I/O driver, a reboot may be required.)")
        return

    print(f"Found {len(outputs)} pwm output(s); sweeping each 0%->100% to find its fan (fans will ramp audibly)...")
    curves = []
    for chip, index, pwm_path in outputs:
        print(f"  testing {chip} pwm{index} ({pwm_path}) ...", flush=True)
        deltas, high = sweep_pwm(pwm_path)
        best_path, best_delta = None, 0
        for p, d in deltas.items():
            if d > best_delta:
                best_path, best_delta = p, d

        if best_path is None or best_delta < 50:
            print(f"    no responding fan detected (max delta={best_delta} rpm) -- likely an empty header, skipping")
            continue

        fan_hwmon = os.path.dirname(best_path)
        fan_chip = read_str(os.path.join(fan_hwmon, "name"))
        fan_index = int(os.path.basename(best_path).replace("fan", "").replace("_input", ""))
        print(f"    -> paired with {fan_chip} fan{fan_index} (delta={best_delta} rpm, high={high.get(best_path)} rpm)")

        curves.append({
            "name": f"{chip}_pwm{index}",
            "sensor": {"chip": DEFAULT_TEMP_CHIP, "label": DEFAULT_TEMP_LABEL},
            "pwm": {"chip": chip, "index": index, "fan_index": fan_index},
            "hysteresis_c": 3,
            "points": DEFAULT_POINTS,
        })

    print()
    if not curves:
        print("No populated fan headers detected. Nothing to write.")
        return

    print(f"Detected {len(curves)} usable fan(s):")
    for c in curves:
        print(f"  {c['name']}: pwm chip={c['pwm']['chip']} index={c['pwm']['index']} -> fan{c['pwm']['fan_index']}")

    if not args.apply:
        print("\nDry run only (pass --apply to write these into the config).")
        return

    import yaml

    os.makedirs(os.path.dirname(args.config), exist_ok=True)
    if os.path.exists(args.config):
        backup = args.config + f".bak.{int(time.time())}"
        shutil.copy2(args.config, backup)
        print(f"backed up existing config to {backup}")

    doc = {
        "poll_interval_s": 2,
        "curves": curves,
    }
    with open(args.config, "w") as f:
        f.write(
            "# Omarchy Fan Control curves.\n"
            "# Auto-generated by fancontrol_detect.py -- edit freely, the daemon\n"
            "# hot-reloads this file on save.\n"
            "#\n"
            "# points: list of [temp_c, percent], sorted ascending; linearly\n"
            "#   interpolated between points, clamped to the endpoints outside\n"
            "#   the range. hysteresis_c: temp must drop this many degrees below\n"
            "#   the last speed-up point before the fan is allowed to slow down.\n"
        )
        yaml.safe_dump(doc, f, sort_keys=False)

    # Written as root; hand ownership back to whoever owns the config dir
    # (the desktop user) so it stays editable without sudo.
    dir_stat = os.stat(os.path.dirname(args.config))
    os.chown(args.config, dir_stat.st_uid, dir_stat.st_gid)
    print(f"wrote {args.config}")


if __name__ == "__main__":
    main()
