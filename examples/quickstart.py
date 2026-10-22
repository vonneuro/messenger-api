"""The shortest complete use of the bench: log in, see what is switched on, sweep one memristor.

    python examples/quickstart.py --user YOUR_NAME
    python examples/quickstart.py --user YOUR_NAME --channel 12 --amplitude 0.3

The password is asked for, or read from MESSENGER_PASSWORD. One sweep is one guarded job: W2
rests at the 2.47 V bias, the channel is selected, then W2 = bias + A sin(2 pi f t) plays for 6
cycles, so the memristor sees +-A while W2 itself never goes negative. Costs a few seconds of
bench time. Writes quickstart_C03.png when matplotlib is installed.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--channel", type=int, default=3, help="memristor 0-15 (0-7 device 23, 8-15 device 22)")
    parser.add_argument("--amplitude", type=float, default=0.25, help="volts around the bias, at most 1")
    args = parser.parse_args()

    # 1. Log in with the same account as on the website.
    client = MessengerClient(args.url)
    me = client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))
    left = client.usage()["remaining_seconds"]
    print(f"Hello {me.username}: {'unlimited' if left is None else f'{left:.0f} s of'} bench time")

    # 2. What the operator has switched on right now (reading this never touches the hardware).
    for item in client.hardware():
        print(f"  {'ON ' if item['enabled'] else 'off'} {item['id']:<16} {item['name']}")

    # 3. One memristor's I-V loop.
    try:
        data = client.memristor_array(args.channel, amplitude=args.amplitude, frequency=10)
    except MessengerError as error:           # 409: switched off; 402: out of bench time; ...
        sys.exit(f"Refused: {error.status_code} {error.detail}")
    iv = data["memristor"]
    v, i = iv["device_voltage_v"], iv["current_a"]
    top = max(range(len(v)), key=lambda k: v[k])
    bottom = min(range(len(v)), key=lambda k: v[k])
    print(f"C{data['array_channel']:02d} is on device {data['device_id']}, pins {data['pins'][0]}-{data['pins'][1]}")
    print(f"  at {v[top]:+.3f} V: {i[top]*1e9:+8.1f} nA  (W2 above the bias: current flows in)")
    print(f"  at {v[bottom]:+.3f} V: {i[bottom]*1e9:+8.1f} nA  (W2 below the bias: current flows out)")
    print(f"  energy delivered to the memristor: {data['energy']['device_j']*1e9:.2f} nJ (job {data['messenger_job']['id']})")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("pip install matplotlib to plot the loop")
        return
    plt.plot(v + v[:1], [x * 1e6 for x in i + i[:1]])
    plt.axhline(0, color="0.8", lw=.6)
    plt.axvline(0, color="0.8", lw=.6)
    plt.xlabel("device voltage (V)")
    plt.ylabel("current (µA)")
    plt.title(f"Memristor C{args.channel:02d}, ±{args.amplitude:g} V, 10 Hz")
    out = f"quickstart_C{args.channel:02d}.png"
    plt.savefig(out, dpi=120)
    print("Saved", out)


if __name__ == "__main__":
    main()
