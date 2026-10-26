"""Command line for the vonNeuro node agent.

    vonneuro-node pair --hub https://vonneuro.com --code ABCD-EFGH [--driver module:Class]
                       get the code on the website (My hardware -> Connect a node); writes node.toml
    vonneuro-node check   [--config node.toml]   validate the driver's devices and operations, offline
    vonneuro-node run     [--config node.toml]   connect and serve jobs until stopped
    vonneuro-node service install [--config node.toml]   start the agent at logon (Windows) or print
                       a systemd unit (Linux)

(`python -m vonneuro_node ...` is the same.) node.toml:

    hub = "https://vonneuro.com"
    node_id = 3
    secret = "..."                         # private: it signs this node's requests
    journal = "jobs.jsonl"                 # optional: every job this node ran, one JSON line each

    [driver]
    class = "vonneuro_node.drivers.simulated:SimulatedDriver"
    [driver.options]                       # passed to the driver's constructor
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import platform
import subprocess
import sys
import tomllib
from pathlib import Path

from . import __version__
from .agent import Agent
from .schema import ParamError, check_operation

DEFAULT_DRIVER = "vonneuro_node.drivers.simulated:SimulatedDriver"


def load_driver(config: dict):
    spec = (config.get("driver") or {}).get("class", DEFAULT_DRIVER)
    module, _, name = spec.partition(":")
    driver_class = getattr(importlib.import_module(module), name)
    return driver_class(**(config.get("driver", {}).get("options") or {}))


def check(driver) -> int:
    """Validate the driver's declarations without contacting the hub."""
    failures = 0
    for device in driver.devices():
        for operation in device.operations:
            try:
                check_operation(operation.to_dict())
                print(f"ok    {device.slug}.{operation.name}")
            except ParamError as exc:
                failures += 1
                print(f"error {device.slug}.{operation.name}: {exc}")
    print(json.dumps([device.to_dict() for device in driver.devices()], indent=2))
    return 1 if failures else 0


def pair(hub: str, code: str, config_path: Path, driver: str) -> int:
    import httpx
    if config_path.exists():
        sys.exit(f"{config_path} exists: this machine is already paired (delete it to pair again)")
    response = httpx.post(hub.rstrip("/") + "/api/agent/pair", json={"code": code, "agent_version": __version__},
                          headers={"ngrok-skip-browser-warning": "1"}, timeout=30)
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        sys.exit(f"Pairing failed ({response.status_code}): {detail}")
    reply = response.json()
    config_path.write_text(
        "# vonneuro-node agent config. Private: the secret signs this node's requests.\n"
        f'hub = "{hub.rstrip("/")}"\nnode_id = {reply["node_id"]}\nsecret = "{reply["secret"]}"\n'
        'journal = "jobs.jsonl"\n\n'
        f'[driver]\nclass = "{driver}"\n', encoding="utf-8")
    if os.name != "nt":
        config_path.chmod(0o600)
    print(f"Paired as node {reply['name']} (owner {reply['owner']}); config written to {config_path}")
    print(f"Next: vonneuro-node check --config {config_path}   then   vonneuro-node run --config {config_path}")
    return 0


def install_service(config_path: Path) -> int:
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    command = [sys.executable, "-m", "vonneuro_node", "run", "--config", str(config_path.resolve())]
    if platform.system() == "Windows":
        name = f"vonNeuro node {config['node_id']}"
        task = subprocess.list2cmdline(command)
        result = subprocess.run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "LIMITED", "/TN", name, "/TR", task],
                                capture_output=True, text=True)
        print(result.stdout or result.stderr)
        return result.returncode
    unit = (f"[Unit]\nDescription=vonNeuro node {config['node_id']}\nAfter=network-online.target\n"
            f"Wants=network-online.target\n\n[Service]\nExecStart={' '.join(command)}\n"
            f"WorkingDirectory={config_path.resolve().parent}\nRestart=always\nRestartSec=10\n\n"
            "[Install]\nWantedBy=default.target\n")
    print("# Save as ~/.config/systemd/user/vonneuro-node.service, then:\n"
          "#   systemctl --user enable --now vonneuro-node\n")
    print(unit)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="vonneuro-node", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("run", "check", "pair", "service"))
    parser.add_argument("action", nargs="?", choices=("install",), help="service install")
    parser.add_argument("--config", type=Path, default=Path("node.toml"))
    parser.add_argument("--hub", help="pair: the hub's address, e.g. https://vonneuro.com")
    parser.add_argument("--code", help="pair: the one-time code from the website")
    parser.add_argument("--driver", default=DEFAULT_DRIVER, help="pair: the driver class, module:Class")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"vonneuro-node {__version__}")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)   # one line per poll otherwise
    if args.command == "pair":
        if not args.hub or not args.code:
            parser.error("pair needs --hub and --code")
        return pair(args.hub, args.code, args.config, args.driver)
    if args.command == "service":
        return install_service(args.config)
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    driver = load_driver(config)
    if args.command == "check":
        return check(driver)
    journal = config.get("journal")
    agent = Agent(config["hub"], config["node_id"], config["secret"], driver,
                  journal=(args.config.resolve().parent / journal) if journal else None)
    try:
        agent.serve_forever()
    except KeyboardInterrupt:
        agent.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
