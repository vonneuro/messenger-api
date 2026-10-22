"""Save a memristor sweep for your own analysis: a CSV of the loop and the job's raw files.

    python examples/sweep_to_csv.py --user YOUR_NAME
    python examples/sweep_to_csv.py --user YOUR_NAME --channel 6 --amplitude 0.5 --frequency 2 --out runs/c06

The password is asked for, or read from MESSENGER_PASSWORD. Writes, in --out (default sweep_C03/):

    loop.csv            phase index, device voltage (V), current (A): the averaged cycle
    summary.json        channel, device, pins, amplitude, frequency, rest/peak current, energy, job id
    measurements.json   the job's own result file, downloaded with list_files()/download_file()

Every job's files stay downloadable from the server and from the dashboard.
"""
import argparse
import csv
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
    parser.add_argument("--channel", type=int, default=3)
    parser.add_argument("--amplitude", type=float, default=0.25)
    parser.add_argument("--frequency", type=float, default=10.0)
    parser.add_argument("--out", type=Path, default=None, help="folder for the files (default sweep_CNN)")
    args = parser.parse_args()
    out = args.out or Path(f"sweep_C{args.channel:02d}")
    out.mkdir(parents=True, exist_ok=True)

    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))
    try:
        data = client.memristor_array(args.channel, amplitude=args.amplitude, frequency=args.frequency)
    except MessengerError as error:
        sys.exit(f"Refused: {error.status_code} {error.detail}")
    iv, job_id = data["memristor"], data["messenger_job"]["id"]

    with open(out / "loop.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["phase_index", "device_voltage_v", "current_a"])
        for k, (v, i) in enumerate(zip(iv["device_voltage_v"], iv["current_a"])):
            writer.writerow([k, f"{v:.6g}", f"{i:.6g}"])

    summary = {"job": job_id, "channel": data["array_channel"], "device": data["device_id"], "pins": data["pins"],
               "amplitude_v": iv["amplitude_v"], "frequency_hz": iv["frequency_hz"], "cycles": iv["cycles"],
               "bias_v": iv["w2_rest_v"], "rest_current_a": iv["rest_current_a"],
               "max_abs_current_a": iv["max_abs_current_a"], "energy": data["energy"]}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")

    # The same job's files on the server: list them, then download the result file itself.
    for f in client.list_files(job_id):
        print(f"  on the server: {f['name']} ({f['size']} bytes)")
    client.download_file(job_id, "measurements.json", dest=out / "measurements.json")
    print(f"Saved {out / 'loop.csv'} ({len(iv['current_a'])} points), {out / 'summary.json'} "
          f"and {out / 'measurements.json'} (job {job_id})")


if __name__ == "__main__":
    main()
