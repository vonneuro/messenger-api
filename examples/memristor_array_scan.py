"""Scan all 16 memristors of the MUX2 array at one amplitude (PROGRAMMING.md section 6).

    python examples/memristor_array_scan.py --user YOUR_NAME
    python examples/memristor_array_scan.py --user YOUR_NAME --amplitude 0.25 --channels 8 9 10 11

For each channel: peak currents, a small-signal resistance (straight line through the origin over
the inner half of the swing), the rectification ratio |I+|/|I-| and the signed loop area (zero for
a plain resistor). Writes array_scan.csv and array_scan.png (a 4x4 grid of I-V loops).
"""
import argparse
import csv
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError


def describe(iv):
    v, i = iv["device_voltage_v"], iv["current_a"]
    span = max(abs(x) for x in v) or 1.0
    inner = [(a, b) for a, b in zip(v, i) if abs(a) <= span / 2]
    num, den = sum(a * b for a, b in inner), sum(a * a for a, _ in inner)
    n = len(v)
    area = .5 * sum(v[k] * i[(k + 1) % n] - v[(k + 1) % n] * i[k] for k in range(n))
    pos, neg = max(i), min(i)
    return {"i_max_uA": pos * 1e6, "i_min_uA": neg * 1e6,
            "r_small_signal_kohm": den / num / 1e3 if num > 0 else None,
            "rectification": abs(pos) / abs(neg) if abs(neg) > 1e-12 else None,
            "loop_area_nVA": area * 1e9}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--channels", type=int, nargs="+", default=list(range(16)))
    parser.add_argument("--amplitude", type=float, default=0.25)
    parser.add_argument("--frequency", type=float, default=10.0)
    args = parser.parse_args()
    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))

    rows, loops = [], {}
    for channel in args.channels:
        try:
            data = client.memristor_array(channel, amplitude=args.amplitude, frequency=args.frequency)
        except MessengerError as error:
            print(f"C{channel:02d}: refused ({error.status_code} {error.detail})")
            continue
        iv = data["memristor"]
        loops[channel] = iv
        row = {"channel": channel, "device": data["device_id"], "pins": "-".join(map(str, data["pins"])),
               **describe(iv), "rest_nA": iv["rest_current_a"] * 1e9, "over_current": iv["over_current"]}
        rows.append(row)
        r = row["r_small_signal_kohm"]
        print(f"C{channel:02d} dev{row['device']} pins {row['pins']:>5}: {row['i_min_uA']:+8.3f} .. {row['i_max_uA']:+8.3f} µA"
              f"  R {r:9.1f} kΩ" if r else f"C{channel:02d}: no positive conductance")

    if not rows:
        sys.exit("Nothing measured")
    with open("array_scan.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Saved array_scan.csv (pip install matplotlib for the grid)")
        return
    fig, axes = plt.subplots(4, 4, figsize=(14, 11))
    for k, ax in enumerate(axes.flat):
        iv = loops.get(k)
        if iv is None:
            ax.set_axis_off()
            continue
        v, i = iv["device_voltage_v"], iv["current_a"]
        ax.plot(v + v[:1], [x * 1e6 for x in i + i[:1]], lw=1)
        ax.axhline(0, color="0.85", lw=.5)
        ax.axvline(0, color="0.85", lw=.5)
        ax.set_title(f"C{k:02d} · device {'23' if k < 8 else '22'}", fontsize=9)
        ax.tick_params(labelsize=7)
    fig.supxlabel("device voltage (V)")
    fig.supylabel("current (µA)")
    fig.suptitle(f"Memristor array at ±{args.amplitude:g} V, {args.frequency:g} Hz")
    fig.tight_layout()
    fig.savefig("array_scan.png", dpi=110)
    print("Saved array_scan.csv and array_scan.png")


if __name__ == "__main__":
    main()
