"""Pulse a chosen voltage onto one RC coprocessor or memristor for a chosen time, and measure it.

    python examples/pulse.py --user YOUR_NAME --target rc:2 --volts 1.0 --ms 200
    python examples/pulse.py --user YOUR_NAME --target memristor:3 --volts 0.2 --ms 20
    python examples/pulse.py --user YOUR_NAME --target memristor:3 --pulses 0.1:20 0.3:50 0.1:20 -0.3:50 0.1:20
    python examples/pulse.py --user YOUR_NAME --target rc:9 --volts 2 --ms 1000 --repeat 3 --gap 2

The password is asked for, or read from MESSENGER_PASSWORD. Each pulse is one bench job
(client.pulse): the channel is selected, driven at the voltage for the duration, and its current
measured as 24 window means across the pulse, with the energy delivered.

Limits, checked before anything is queued:
  memristor:N   the device itself at -0.3 .. +0.3 V, for 5 .. 500 ms (after a rest check at 0 V)
  rc:N          W1 at 0 .. 4 V (2.24 V on channel 4), for 5 .. 2000 ms; capacitors are polarised
Start small on memristors: a strong pulse can switch a device, and it stays switched for the next
user. --pulses VOLTS:MS ... runs a sequence (read, write, read: 0.1:20 0.3:50 0.1:20). Writes
pulse_<target>.json and, with matplotlib, pulse_<target>.png.
"""
import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError


def pulse_list(args):
    """[(volts, ms), ...] from --pulses, or --volts/--ms repeated --repeat times."""
    if args.pulses:
        try:
            return [(float(v), float(ms)) for v, ms in (item.split(":") for item in args.pulses)]
        except ValueError:
            sys.exit("--pulses takes VOLTS:MS items, e.g. 0.1:20 0.3:50")
    if args.volts is None:
        sys.exit("Give --volts and --ms, or --pulses VOLTS:MS ...")
    return [(args.volts, args.ms)] * args.repeat


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--target", default="rc:2", help="rc:N or memristor:N, N in 0-15 (rc:2: tau ~0.13 s)")
    parser.add_argument("--volts", type=float, help="pulse voltage (memristor: -0.3..0.3; rc: 0..4, channel 4: 0..2.24)")
    parser.add_argument("--ms", type=float, default=100, help="pulse length in ms (memristor 5-500, rc 5-2000)")
    parser.add_argument("--repeat", type=int, default=1, help="the same pulse this many times")
    parser.add_argument("--pulses", nargs="+", metavar="VOLTS:MS", help="a sequence of pulses instead of --volts/--ms")
    parser.add_argument("--gap", type=float, default=0, help="extra seconds between pulses (queueing adds a few anyway)")
    args = parser.parse_args()
    pulses = pulse_list(args)

    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))
    print("Logged in. Time left:", client.usage()["remaining_seconds"], "s")

    records = []
    for n, (volts, ms) in enumerate(pulses, 1):
        try:
            # One call per pulse: target, voltage, duration. The reply is the bench's full record.
            reply = client.pulse(args.target, volts, ms)
        except MessengerError as error:   # 422: outside the limits; 409: switched off or refused by the bench
            sys.exit(f"Pulse {n}: refused: {error.status_code} {error.detail}")
        current = reply["result"]["value"]                    # 24 window means, amperes
        mean = sum(current) / len(current)
        print(f"Pulse {n}: {args.target} at {reply['pulse']['applied_v']:+.3f} V for {ms:g} ms -> "
              f"mean {mean*1e6:+.3f} uA, last window {current[-1]*1e6:+.3f} uA, "
              f"energy {reply['measured_energy_j']*1e9:.2f} nJ (job {reply['messenger_job']['id']})")
        records.append({"volts": volts, "ms": ms, "applied_v": reply["pulse"]["applied_v"], "current_a": current,
                        "energy_j": reply["measured_energy_j"], "job": reply["messenger_job"]["id"]})
        if args.gap and n < len(pulses):
            time.sleep(args.gap)

    name = args.target.replace(":", "")
    Path(f"pulse_{name}.json").write_text(json.dumps({"target": args.target, "pulses": records}, indent=2))
    print(f"Saved pulse_{name}.json. Time left:", client.usage()["remaining_seconds"], "s")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    start = 0.0
    for record in records:                                   # pulses end to end on pulse time
        k = len(record["current_a"])
        t = [start + (i + 0.5) * record["ms"] / k for i in range(k)]
        plt.plot(t, [i * 1e6 for i in record["current_a"]], "o-", ms=3, label=f"{record['volts']:+g} V, {record['ms']:g} ms")
        start += record["ms"]
    plt.xlabel("pulse time (ms)")
    plt.ylabel("current (uA)")
    plt.title(f"Pulses on {args.target}")
    if len(records) <= 8:
        plt.legend()
    plt.savefig(f"pulse_{name}.png", dpi=120)
    print(f"Saved pulse_{name}.png")
    plt.show()


if __name__ == "__main__":
    main()
