"""The memristor fingerprint: the pinched I-V loop narrows as the frequency rises.

    python examples/memristor_frequency.py --user YOUR_NAME --channel 3
    python examples/memristor_frequency.py --user YOUR_NAME --channel 12 --frequencies 1 3 10 30 100

Measures one memristor of the MUX2 array at the same amplitude and several frequencies, and
prints the signed loop area at each (PROGRAMMING.md section 6). A memristor's loop area shrinks
towards zero at high frequency, because its internal state has no time to follow; a capacitor's
would grow instead. Writes memristor_frequency_C03.png.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError


def loop_area(iv):
    v, i = iv["device_voltage_v"], iv["current_a"]
    n = len(v)
    return .5 * sum(v[k] * i[(k + 1) % n] - v[(k + 1) % n] * i[k] for k in range(n))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--channel", type=int, default=3)
    parser.add_argument("--amplitude", type=float, default=0.5)
    parser.add_argument("--frequencies", type=float, nargs="+", default=[1, 3, 10, 30, 100])
    args = parser.parse_args()
    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))

    loops = []
    for frequency in sorted(args.frequencies):
        try:
            iv = client.memristor_array(args.channel, amplitude=args.amplitude, frequency=frequency)["memristor"]
        except MessengerError as error:
            sys.exit(f"Refused at {frequency:g} Hz: {error.status_code} {error.detail}")
        loops.append(iv)
        print(f"{iv['frequency_hz']:7.2f} Hz: loop area {loop_area(iv)*1e9:+9.2f} nVA, "
              f"peak {max(abs(x) for x in iv['current_a'])*1e6:.3f} µA")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 4.5))
    for iv in loops:
        v, i = iv["device_voltage_v"], iv["current_a"]
        left.plot(v + v[:1], [x * 1e6 for x in i + i[:1]], label=f"{iv['frequency_hz']:g} Hz")
    left.set_xlabel("device voltage (V)")
    left.set_ylabel("current (µA)")
    left.legend()
    right.semilogx([iv["frequency_hz"] for iv in loops], [loop_area(iv) * 1e9 for iv in loops], "o-")
    right.axhline(0, color="0.8", lw=.6)
    right.set_xlabel("frequency (Hz)")
    right.set_ylabel("signed loop area (nVA)")
    fig.suptitle(f"Memristor array C{args.channel:02d} at ±{args.amplitude:g} V")
    fig.tight_layout()
    out = f"memristor_frequency_C{args.channel:02d}.png"
    fig.savefig(out, dpi=110)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
