"""Run a Python-lab script on your own computer, unchanged:

    python -m vonneuro_messenger.lab my_script.py

The script finds what the lab gives it: a signed-in ``client``, ``np``, ``plt``, ``save``, ``save_csv``
and ``run_energy``. Sign-in, in this order: MESSENGER_URL + MESSENGER_TOKEN (the vonNeuro VS Code
extension sets them), MESSENGER_USER + MESSENGER_PASSWORD, or it asks. The server is MESSENGER_URL,
or vonneuro.com's bench if unset. Files from save() land in MESSENGER_OUTPUT_DIR (default: the
script's folder); figures from plt.show() are written as PNGs there too, or shown in VS Code.
"""
from __future__ import annotations

import csv
import io
import json
import os
import runpy
import sys
from getpass import getpass
from pathlib import Path

from ._client import MessengerClient, MessengerError, _progress

DEFAULT_URL = "https://series-marina-gigantic.ngrok-free.dev"
_energy = {"device_j": 0.0, "measured_jobs": 0, "unmeasured_jobs": 0}


def _output_dir() -> Path:
    path = Path(os.environ.get("MESSENGER_OUTPUT_DIR") or ".")
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(name, data, mime=None):
    """As in the lab: write text, bytes or JSON data (dict, list) to a file."""
    path = _output_dir() / name
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data if isinstance(data, str) else json.dumps(data, indent=1), encoding="utf-8")
    _progress({"phase": "file", "path": str(path.resolve())})
    print("saved", path)


def save_csv(name, rows, header=None):
    """As in the lab: rows (and an optional header row) as a CSV file."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if header:
        writer.writerow(header)
    writer.writerows(rows)
    save(name, buffer.getvalue())


def run_energy():
    """Energy delivered to the devices so far in this run (J), from the bench's measured V and I."""
    return dict(_energy)


def _count(joules):
    if joules is None:
        _energy["unmeasured_jobs"] += 1
    else:
        _energy["device_j"] += float(joules)
        _energy["measured_jobs"] += 1


def signed_in_client() -> MessengerClient:
    client = MessengerClient(os.environ.get("MESSENGER_URL") or DEFAULT_URL)
    if not os.environ.get("MESSENGER_TOKEN"):
        client.login(os.environ.get("MESSENGER_USER") or input("Username: "),
                     os.environ.get("MESSENGER_PASSWORD") or getpass("Password: "))
    volt_run = client.volt_run

    def counted_volt_run(*args, **kwargs):
        reply = volt_run(*args, **kwargs)
        _count(reply.get("measured_energy_j") if isinstance(reply, dict) else None)
        return reply
    client.volt_run = counted_volt_run
    return client


def lab_globals(client: MessengerClient) -> dict:
    names = {"client": client, "MessengerClient": MessengerClient, "MessengerError": MessengerError,
             "save": save, "save_csv": save_csv, "run_energy": run_energy}
    try:
        import numpy as np
        names["np"] = np
    except ImportError:
        pass
    try:
        import matplotlib
        if os.environ.get("MESSENGER_FIGURES") == "vscode":
            matplotlib.use("module://vonneuro_messenger._mpl_vscode")
        import matplotlib.pyplot as plt
        names["plt"] = plt
    except ImportError:
        pass
    return names


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    script = Path(argv[0]).resolve()
    client = signed_in_client()
    sys.argv = [str(script), *argv[1:]]
    sys.path.insert(0, str(script.parent))
    try:
        runpy.run_path(str(script), init_globals=lab_globals(client), run_name="__main__")
    finally:
        client._release_quietly()      # a bench hold the script took ends with the script, even on an error
        if "matplotlib.pyplot" in sys.modules and os.environ.get("MESSENGER_FIGURES") == "vscode":
            sys.modules["matplotlib.pyplot"].show()   # figures the script made but never showed
    return 0


if __name__ == "__main__":
    sys.exit(main())
