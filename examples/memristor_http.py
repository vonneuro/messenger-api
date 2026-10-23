"""One memristor-array measurement over plain HTTP, without the vonneuro_messenger client.

Uses only the Python standard library, so it is also a template for other languages:
log in, submit a messenger_instrument job, poll it, download measurements.json
(PROGRAMMING.md sections 8 and 13).

    python examples/memristor_http.py --user YOUR_NAME --channel 3 --amplitude 0.25
"""
import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def call(method, url, token=None, body=None, form=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        sys.exit(f"{method} {url} -> {error.code}: {error.read().decode(errors='replace')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--channel", type=int, default=3)
    parser.add_argument("--amplitude", type=float, default=0.25)
    parser.add_argument("--frequency", type=float, default=10.0)
    args = parser.parse_args()
    base = args.url.rstrip("/")
    password = os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: ")

    # 1. Log in: form fields, returns a bearer token.
    token = call("POST", f"{base}/api/auth/login", form={"username": args.user, "password": password})["access_token"]

    # 2. Is the array switched on?
    items = {item["id"]: item for item in call("GET", f"{base}/api/hardware/available", token)["items"]}
    array = items.get("memristor-array")
    print(f"memristor-array: {'on' if array and array['enabled'] else 'off or missing'}")

    # 3. Submit the guarded preset as a messenger_instrument job.
    job = call("POST", f"{base}/api/jobs", token, body={
        "name": f"memristor array C{args.channel:02d} (HTTP)", "job_type": "messenger_instrument",
        "payload": {"args": ["--fixture", "memristor_array", "--array-channel", str(args.channel),
                             "--amplitude", str(args.amplitude), "--frequency", str(args.frequency)]}})
    print(f"job {job['id']} submitted")

    # 4. Poll until it finishes.
    while True:
        job = call("GET", f"{base}/api/jobs/{job['id']}", token)
        if job["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(.5)
    if job["status"] != "done":
        sys.exit(f"job {job['id']} {job['status']}: {(job.get('logs') or '')[-400:]}")

    # 5. Download the result.
    data = call("GET", f"{base}/api/jobs/{job['id']}/outputs/measurements.json", token)
    iv = data["memristor"]
    print(f"C{data['array_channel']:02d} device {data['device_id']} pins {data['pins']}: "
          f"{min(iv['current_a'])*1e6:+.3f} .. {max(iv['current_a'])*1e6:+.3f} µA over "
          f"{min(iv['device_voltage_v']):+.3f} .. {max(iv['device_voltage_v']):+.3f} V "
          f"({len(iv['current_a'])} points, rest {iv['rest_current_a']*1e9:+.0f} nA)")


if __name__ == "__main__":
    main()
