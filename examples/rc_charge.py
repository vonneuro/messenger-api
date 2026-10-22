"""Charge one RC coprocessor of the bench with a VOLT program and plot the measured current.

    python examples/rc_charge.py --user YOUR_NAME
    python examples/rc_charge.py --user YOUR_NAME --channel 4 --drive 0.8 --ms 300

The password is asked for, or read from MESSENGER_PASSWORD. A VOLT 0.1 `system` program drives
RC coprocessor N (0-15) for an interval and observes its terminal current as 24 window means; the
reply also carries the energy delivered and which contract clauses the bench checked.
Slow channels (3, 9, 10, 12, 13: time constants around 1-3 s) need a longer --ms, up to 2000.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError

PROGRAM = """
system rc_charge(u: real) {{
  state capacitor: charge;
  evolve capacitor for {ms} ms using drive.interval(u);
  observe current: vector from capacitor.terminal_current;
  return current;
  execution {{ intent = evolve; output = current; }}
  device {{ dialects = [dynamical]; required_capabilities = [drive.interval, observe.terminals]; }}
  thermodynamics {{ process_boundary = "drive_to_observation"; }}
}}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--channel", type=int, default=2, help="RC coprocessor 0-15 (2: 1 kOhm, 100 uF, tau ~0.13 s)")
    parser.add_argument("--drive", type=float, default=0.5, help="u in 0..1, a fraction of the channel's drive voltage")
    parser.add_argument("--ms", type=int, default=200, help="interval in ms (5-2000)")
    args = parser.parse_args()

    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))
    print("Logged in. Time left:", client.usage()["remaining_seconds"], "s")

    try:
        reply = client.volt_run(PROGRAM.format(ms=args.ms), {"u": args.drive}, f"rc:{args.channel}")
    except MessengerError as error:          # e.g. 409: the RC board is switched off right now
        sys.exit(f"Refused: {error.status_code} {error.detail}")
    if not reply["ok"]:                      # refused before anything was driven, clause named
        sys.exit(f"VOLT error: {reply['error']}")

    current = reply["result"]["value"]       # 24 values, amperes
    print(f"Drove RC {args.channel} at {reply['provenance']['drive_v']:.2f} V for {reply['interval_s']*1e3:.0f} ms")
    print("Current (mA):", " ".join(f"{i*1e3:.2f}" for i in current))
    print(f"Energy delivered: {reply['measured_energy_j']*1e6:.1f} µJ")
    print("Contract clauses checked:", ", ".join(reply["contracts_checked"]))
    print("Time left:", client.usage()["remaining_seconds"], "s")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    t = [(k + 0.5) * reply["interval_s"] / len(current) * 1e3 for k in range(len(current))]
    plt.plot(t, [i * 1e3 for i in current], "o-")
    plt.xlabel("time (ms)")
    plt.ylabel("current (mA)")
    plt.title(f"RC coprocessor {args.channel} charging, u = {args.drive}")
    plt.savefig(f"rc_charge_{args.channel}.png", dpi=120)
    print(f"Saved rc_charge_{args.channel}.png")
    plt.show()


if __name__ == "__main__":
    main()
