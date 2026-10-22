"""Measure the time constant of several RC coprocessors, and see that each one remembers its charge.

    python examples/rc_survey.py --user YOUR_NAME
    python examples/rc_survey.py --user YOUR_NAME --channels 2 4 5 6 7 --ms 400

The password is asked for, or read from MESSENGER_PASSWORD. For each channel, two VOLT 0.1 runs:

1. discharge: drive.interval(0) holds W1 at 0 V, so a capacitor charged by an earlier user empties
   through its resistor (negative current);
2. charge: drive.interval(u) steps W1 to u x the channel's drive voltage; the current decays as
   I(t) = I0 exp(-t / tau). A straight-line fit of ln I against t gives tau.

A deselected capacitor keeps its charge, which is why the discharge comes first: charge a channel
twice without it and the second run shows almost no current. Writes rc_survey.json and .png.
"""
import argparse
import getpass
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError

PROGRAM = """
system rc_step(u: real) {{
  state capacitor: charge;
  evolve capacitor for {ms} ms using drive.interval(u);
  observe current: vector from capacitor.terminal_current;
  return current;
  execution {{ intent = evolve; output = current; }}
  device {{ dialects = [dynamical]; required_capabilities = [drive.interval, observe.terminals]; }}
  thermodynamics {{ process_boundary = "drive_to_observation"; }}
}}
"""


def fit_tau(times, currents):
    """tau from a least-squares line through ln I, using the points above 5 % of the peak."""
    peak = max(currents)
    points = [(t, math.log(i)) for t, i in zip(times, currents) if i > 0.05 * peak]
    if len(points) < 3:
        return None
    n = len(points)
    mt, my = sum(t for t, _ in points) / n, sum(y for _, y in points) / n
    slope = sum((t - mt) * (y - my) for t, y in points) / sum((t - mt) ** 2 for t, _ in points)
    return -1 / slope if slope < 0 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--channels", type=int, nargs="+", default=[2, 4, 5, 6, 7],
                        help="RC coprocessors 0-15 (the fast ones: tau from 12 ms to 0.26 s)")
    parser.add_argument("--drive", type=float, default=0.5, help="u in (0, 1]")
    parser.add_argument("--ms", type=int, default=400, help="interval of each run, 5-2000 ms; use ~3 tau or more")
    args = parser.parse_args()
    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))

    program = PROGRAM.format(ms=args.ms)
    results = []
    for channel in args.channels:
        try:
            empty = client.volt_run(program, {"u": 0.0}, f"rc:{channel}")
            charge = client.volt_run(program, {"u": args.drive}, f"rc:{channel}")
        except MessengerError as error:
            sys.exit(f"Refused: {error.status_code} {error.detail}")
        if not (empty["ok"] and charge["ok"]):
            sys.exit(f"VOLT error: {empty.get('error') or charge.get('error')}")
        current = charge["result"]["value"]                       # 24 window means, amperes
        step = charge["interval_s"] / len(current)
        times = [(k + 0.5) * step for k in range(len(current))]
        tau = fit_tau(times, current)
        leftover = max(0.0, -min(empty["result"]["value"]))       # discharge current at its largest
        drained = (f"drained {leftover*1e3:.3f} mA of leftover charge" if leftover > 2e-5
                   else "no charge left over")                    # 20 µA: 2 mV on the 100 Ω sense
        results.append({"channel": channel, "tau_s": tau, "drive_v": charge["provenance"]["drive_v"],
                        "peak_current_a": max(current), "discharge_peak_a": leftover,
                        "charge_energy_j": charge["measured_energy_j"], "times_s": times, "current_a": current})
        print(f"RC{channel:02d}: tau {('%.0f ms' % (tau * 1e3)) if tau else 'not fitted (use a longer --ms)':>14}, "
              f"peak {max(current)*1e3:6.3f} mA at {charge['provenance']['drive_v']:.2f} V, "
              f"{drained}, "
              f"{charge['measured_energy_j']*1e6:.1f} µJ delivered")

    Path("rc_survey.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Saved rc_survey.json")
        return
    for r in results:
        label = f"RC{r['channel']:02d}" + (f", τ = {r['tau_s']*1e3:.0f} ms" if r["tau_s"] else "")
        plt.semilogy([t * 1e3 for t in r["times_s"]], [max(i, 1e-7) * 1e3 for i in r["current_a"]], "o-", ms=3, label=label)
    plt.xlabel("time since the step (ms)")
    plt.ylabel("charging current (mA)")
    plt.title("RC coprocessors: charging after a discharge")
    plt.legend()
    plt.savefig("rc_survey.png", dpi=120)
    print("Saved rc_survey.json and rc_survey.png")


if __name__ == "__main__":
    main()
