"""Your vonNeuro Messenger account at a glance, and asking for more time or the client repository.

    python examples/account.py --user YOUR_NAME
    python examples/account.py --user YOUR_NAME --request-hours 3 --reason "I-V sweeps of all 16 memristors"
    python examples/account.py --user YOUR_NAME --github your-github-name

The password is asked for, or read from MESSENGER_PASSWORD. Shows your remaining bench time, what
the operator has switched on right now, your requests for more time and your access to the client
repository. Requests go to an admin, who approves them on the Messenger admin page.
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
    parser.add_argument("--request-hours", type=float, help="ask an admin for this many more hours (up to 24)")
    parser.add_argument("--reason", default="", help="what you will run with the extra time")
    parser.add_argument("--github", help="ask for the private client repository for this GitHub account")
    args = parser.parse_args()

    client = MessengerClient(args.url)
    me = client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))
    usage = client.usage()
    left = usage["remaining_seconds"]
    print(f"{me.username}: {'unlimited' if left is None else f'{left / 60:.1f} minutes'} of bench time left")

    print("\nSwitched on now:")
    for item in client.hardware():
        print(f"  {'ON ' if item['enabled'] else 'off'}  {item['id']:<16} {item['name']}")

    if args.request_hours:
        if len(args.reason.strip()) < 3:
            sys.exit("Give a --reason: what you will run with the extra time")
        try:
            r = client.request_time(args.request_hours, args.reason)
            print(f"\nRequested {r['hours']} h (request {r['id']}, {r['status']}); an admin will review it.")
        except MessengerError as error:
            print(f"\nTime request not sent: {error.detail}")
    requests = client.time_requests()
    if requests:
        print("\nYour time requests:")
        for r in requests[:5]:
            granted = f", {r['granted_hours']:.2f} h granted" if r["granted_hours"] else ""
            note = f" — {r['admin_note']}" if r["admin_note"] else ""
            print(f"  #{r['id']}: {r['hours']} h, {r['status']}{granted}{note}")

    if args.github:
        me = client.request_repo_access(args.github, args.reason or None)
    if me.github_username:
        state = {"requested": "requested; an admin will invite you on GitHub",
                 "invited": "invited; accept the invitation in your GitHub email"}.get(me.github_access, me.github_access)
        print(f"\nClient repository for GitHub '{me.github_username}': {state}")
    else:
        print("\nClient repository: not requested yet (use --github YOUR_GITHUB_NAME)")


if __name__ == "__main__":
    main()
