"""Run a VOLT program on a Messenger server and print its reply.

From the repository folder:  python examples/volt_program.py --list
                 python examples/volt_program.py device22_current_level \
                     '{"samples": [1e-6, -2e-6, 3e-6], "baseline_rms_a": 0, "scale_a": 5e-6}'
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vonneuro_messenger import MessengerClient

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("program", nargs="?", help="Installed VOLT program name")
parser.add_argument("inputs", nargs="?", default="{}", help="Inputs as a JSON object")
parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
parser.add_argument("--username", required=True)
parser.add_argument("--list", action="store_true", help="List installed programs and exit")
args = parser.parse_args()

client = MessengerClient(args.url)
client.login(args.username, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass("Messenger password: "))
if args.list or not args.program:
    for program in client.volt_programs():
        print(f"{program['name']:<28} {program['signature']}")
    raise SystemExit(0)
reply = client.run_volt(args.program, json.loads(args.inputs))
print(json.dumps({k: reply[k] for k in reply if k in ("ok", "program", "source_sha256", "result", "error", "messenger_job")},
                 indent=2))
raise SystemExit(0 if reply["ok"] else 1)
