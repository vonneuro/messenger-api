"""I-V loops of one memristor on the MUX2 array, at increasing amplitudes (PROGRAMMING.md section 6).

    python examples/memristor_iv.py --user francis --channel 3
    python examples/memristor_iv.py --user francis --channel 9 --amplitudes 0.25 0.5 --frequency 5

Stops raising the amplitude when the bench reports more than 40 uA. Writes
memristor_C03.json (every run) and memristor_C03.png (the loops) in the current folder.
The password is asked for, or read from MESSENGER_PASSWORD.
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
    parser.add_argument("--channel", type=int, default=3, help="0-7 device 23, 8-15 device 22")
    parser.add_argument("--amplitudes", type=float, nargs="+", default=[0.25, 0.5],
                        help="peak volts across the device, at most 1; larger ones can switch it")
    parser.add_argument("--frequency", type=float, default=10.0, help="above 0, up to 100 Hz; a sweep is 6 cycles (60 s at 0.1 Hz)")
    args = parser.parse_args()
    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))

    runs = []
    for amplitude in sorted(args.amplitudes):
        try:
            data = client.memristor_array(args.channel, amplitude=amplitude, frequency=args.frequency)
        except MessengerError as error:
            sys.exit(f"Refused: {error.status_code} {error.detail}")
        iv = data["memristor"]
        runs.append(iv)
        current = iv["current_a"]
        print(f"C{args.channel:02d} (device {data['device_id']}, pins {data['pins'][0]}-{data['pins'][1]}) "
              f"±{iv['amplitude_v']:.2f} V at {iv['frequency_hz']:g} Hz: "
              f"{min(current)*1e6:+.3f} .. {max(current)*1e6:+.3f} µA, rest {iv['rest_current_a']*1e9:+.0f} nA "
              f"(job {data['messenger_job']['id']})")
        if iv["over_current"]:
            print("  above 40 µA: not going higher on this channel")
            break

    stem = Path(f"memristor_C{args.channel:02d}")
    stem.with_suffix(".json").write_text(json.dumps(runs, indent=1), encoding="utf-8")
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"Saved {stem.with_suffix('.json')} (pip install matplotlib for the plot)")
        return
    for iv in runs:
        v, i = iv["device_voltage_v"], iv["current_a"]
        plt.plot(v + v[:1], [x * 1e6 for x in i + i[:1]], label=f"±{iv['amplitude_v']:.2f} V")
    plt.axhline(0, color="0.8", lw=.6)
    plt.axvline(0, color="0.8", lw=.6)
    plt.xlabel("device voltage (V)")
    plt.ylabel("current (µA)")
    plt.title(f"Memristor array C{args.channel:02d}, {args.frequency:g} Hz, cycles 3–5 averaged")
    plt.legend()
    plt.savefig(stem.with_suffix(".png"), dpi=120)
    print(f"Saved {stem.with_suffix('.json')} and {stem.with_suffix('.png')}")


if __name__ == "__main__":
    main()
