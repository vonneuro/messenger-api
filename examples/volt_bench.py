"""VOLT 0.1 on the bench: write a `system` program and run it on a real device.

    python examples/volt_bench.py --user YOUR_NAME
    python examples/volt_bench.py --user YOUR_NAME --target memristor:12 --drive 0.8

1. Runs MEMRISTOR_EVOLVE (below) on a memristor at several drives and prints the observed current
   and the energy measured at the device terminals.
2. Sends the same program to an RC coprocessor, which refuses it before anything is driven: the
   program asks for the 'adaptive' dialect, and an RC is only 'dynamical'.
3. Runs an RC version of the program, observing 24 window means of the charging current.
(PROGRAMMING.md, "VOLT 0.1 on the bench".)
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError

MEMRISTOR_EVOLVE = """
system memristor_evolve(u: real) {
  state material: adaptive;
  evolve material for 10 ms using drive.interval(u);
  observe response: real from material.terminal_current;
  return response;
  execution { intent = evolve; output = response; tolerance = 0.05; }
  device { dialects = [dynamical, adaptive]; required_capabilities = [drive.interval, observe.terminals]; }
  thermodynamics { process_boundary = "drive_to_observation"; energy_class = uncharacterized; }
}
"""

RC_CHARGE = """
system rc_charge(u: real) {
  state capacitor: charge;
  evolve capacitor for 200 ms using drive.interval(u);
  observe current: vector from capacitor.terminal_current;
  return current;
  execution { intent = evolve; output = current; }
  device { dialects = [dynamical]; required_capabilities = [drive.interval, observe.terminals]; }
  thermodynamics { process_boundary = "drive_to_observation"; }
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--target", default="memristor:15", help="memristor:N for step 1 (N in 0-15)")
    parser.add_argument("--rc", default="rc:2", help="rc:N for steps 2 and 3")
    parser.add_argument("--drive", type=float, default=0.5, help="largest |u| for step 1 (at most 1: 0.3 V)")
    args = parser.parse_args()
    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))

    print(f"1. memristor_evolve on {args.target}")
    for u in (-args.drive, -args.drive / 2, args.drive / 2, args.drive):
        try:
            reply = client.volt_run(MEMRISTOR_EVOLVE, {"u": u}, args.target)
        except MessengerError as error:
            sys.exit(f"   refused: {error.status_code} {error.detail}")
        if not reply["ok"]:
            sys.exit(f"   error: {reply['error']}")
        volts = reply["provenance"]["device_voltage_v"]
        print(f"   u={u:+.2f} ({volts:+.3f} V): response {reply['result']['value']*1e6:+.4f} µA, "
              f"{reply['measured_energy_j']*1e9:.3f} nJ at the terminals")
    print(f"   checked: {', '.join(reply['contracts_checked'])}")
    print(f"   declared, not yet enforced: {', '.join(reply['contracts_not_enforced'])}")

    print(f"2. the same program on {args.rc}")
    reply = client.volt_run(MEMRISTOR_EVOLVE, {"u": .5}, args.rc)
    print(f"   {'ran' if reply['ok'] else 'refused before driving: ' + reply['error']}")

    print(f"3. rc_charge on {args.rc}")
    reply = client.volt_run(RC_CHARGE, {"u": .5}, args.rc)
    if not reply["ok"]:
        sys.exit(f"   error: {reply['error']}")
    current = reply["result"]["value"]
    print("   charging current (mA): " + " ".join(f"{i*1e3:.2f}" for i in current[::3]))
    print(f"   {reply['measured_energy_j']*1e6:.1f} µJ delivered; VNG program '{reply['vng']['program']}', "
          f"source {reply['provenance']['source_sha256'][:12]}")


if __name__ == "__main__":
    main()
