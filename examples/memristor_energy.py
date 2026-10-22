"""How much energy does a memristor sweep cost? Energy and apparent resistance against amplitude.

    python examples/memristor_energy.py --user YOUR_NAME
    python examples/memristor_energy.py --user YOUR_NAME --channel 13 --amplitudes 0.1 0.2 0.4 --frequency 5

The password is asked for, or read from MESSENGER_PASSWORD. Every memristor_array result carries
`energy`: the electrical work delivered to the memristor (`device_j`) and to its 10 kOhm sense
resistor (`sense_resistor_j`), from the measured voltage and current over the 6 cycles. The
instruments' own power is not included. For a linear resistor the device energy grows as A^2;
departures show the device's nonlinearity. Writes memristor_energy_C04.json and .png.
"""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--channel", type=int, default=4, help="memristor 0-15")
    parser.add_argument("--amplitudes", type=float, nargs="+", default=[0.1, 0.2, 0.3, 0.4],
                        help="volts around the bias, at most 1 (larger ones can switch the device)")
    parser.add_argument("--frequency", type=float, default=10.0)
    args = parser.parse_args()
    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))

    rows = []
    print(f"{'A (V)':>6} {'device (nJ)':>12} {'10 kΩ (nJ)':>11} {'R at +peak':>11} {'R at −peak':>11}")
    for amplitude in sorted(args.amplitudes):
        try:
            data = client.memristor_array(args.channel, amplitude=amplitude, frequency=args.frequency)
        except MessengerError as error:
            sys.exit(f"Refused: {error.status_code} {error.detail}")
        iv, energy = data["memristor"], data["energy"]
        v, i = iv["device_voltage_v"], iv["current_a"]
        top = max(range(len(v)), key=lambda k: v[k])
        bottom = min(range(len(v)), key=lambda k: v[k])
        r_pos = v[top] / i[top] if i[top] else float("inf")           # V / I at the positive peak
        r_neg = v[bottom] / i[bottom] if i[bottom] else float("inf")  # and at the negative one
        rows.append({"amplitude_v": iv["amplitude_v"], "device_j": energy["device_j"],
                     "sense_resistor_j": energy["sense_resistor_j"], "r_pos_ohm": r_pos, "r_neg_ohm": r_neg,
                     "job": data["messenger_job"]["id"]})
        print(f"{iv['amplitude_v']:6.2f} {energy['device_j']*1e9:12.3f} {energy['sense_resistor_j']*1e9:11.3f} "
              f"{r_pos/1e3:9.1f} kΩ {r_neg/1e3:9.1f} kΩ")
        if iv["over_current"]:
            print("  above 40 µA: stopping here")
            break

    stem = Path(f"memristor_energy_C{args.channel:02d}")
    stem.with_suffix(".json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"Saved {stem.with_suffix('.json')}")
        return
    a = [r["amplitude_v"] for r in rows]
    plt.loglog(a, [r["device_j"] for r in rows], "o-", label="memristor")
    plt.loglog(a, [r["sense_resistor_j"] for r in rows], "s--", label="10 kΩ sense")
    scale = rows[0]["device_j"] / a[0] ** 2
    plt.loglog(a, [scale * x ** 2 for x in a], ":", color="0.6", label="∝ A² (linear device)")
    plt.xlabel("amplitude around the bias (V)")
    plt.ylabel("energy per sweep (J)")
    plt.title(f"Memristor C{args.channel:02d}: energy of a 6-cycle sweep at {args.frequency:g} Hz")
    plt.legend()
    plt.savefig(stem.with_suffix(".png"), dpi=120)
    print(f"Saved {stem.with_suffix('.json')} and {stem.with_suffix('.png')}")


if __name__ == "__main__":
    main()
