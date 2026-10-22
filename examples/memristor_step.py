"""Hold one memristor at a few voltages with a VOLT program and see its current respond.

    python examples/memristor_step.py --user YOUR_NAME
    python examples/memristor_step.py --user YOUR_NAME --channel 3 --steps -1 -0.5 0.5 1

The password is asked for, or read from MESSENGER_PASSWORD. A VOLT 0.1 `system` program holds
memristor N (0-15) at u x 0.3 V for 10 ms and observes the mean current; repeated for each u in
--steps (-1..1). A rectifying device answers more strongly in one direction. The bench checks the
device is at 0 V before each step, and every reply says which contract clauses it checked.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError

PROGRAM = """
system memristor_step(u: real) {
  state material: adaptive;
  evolve material for 10 ms using drive.interval(u);
  observe response: real from material.terminal_current;
  return response;
  execution { intent = evolve; output = response; }
  device { dialects = [dynamical, adaptive]; required_capabilities = [drive.interval, observe.terminals]; }
  thermodynamics { process_boundary = "drive_to_observation"; }
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--channel", type=int, default=15, help="memristor 0-15 (0-7 device 23, 8-15 device 22)")
    parser.add_argument("--steps", type=float, nargs="+", default=[-1, -0.5, 0.5, 1], help="u values in -1..1")
    args = parser.parse_args()

    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))

    for u in args.steps:
        try:
            reply = client.volt_run(PROGRAM, {"u": u}, f"memristor:{args.channel}")
        except MessengerError as error:
            sys.exit(f"Refused: {error.status_code} {error.detail}")
        if not reply["ok"]:
            sys.exit(f"VOLT error: {reply['error']}")
        volts, amps = reply["provenance"]["device_voltage_v"], reply["result"]["value"]
        ohms = f"{volts / amps / 1e6:.2f} MΩ" if abs(amps) > 1e-10 else "—"
        print(f"u={u:+.2f}  {volts:+.3f} V  ->  {amps*1e6:+.4f} µA   ({ohms}, {reply['measured_energy_j']*1e9:.3f} nJ)")
    print("Time left:", client.usage()["remaining_seconds"], "s")


if __name__ == "__main__":
    main()
