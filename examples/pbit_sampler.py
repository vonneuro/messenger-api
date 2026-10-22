"""Sample an Ising model on the hardware p-bit sampler and compare with the exact distribution.

    python examples/pbit_sampler.py --user YOUR_NAME
    python examples/pbit_sampler.py --user YOUR_NAME --model frustrated --samples 300 --temperature 0.8
    python examples/pbit_sampler.py --user YOUR_NAME --mock     # the same algorithm on the server, no hardware

The password is asked for, or read from MESSENGER_PASSWORD. A small Ising model (couplings J, fields h,
temperature T) is loaded into the 16-p-bit sampler, which runs Gibbs sweeps. For up to 12 spins
the exact Boltzmann distribution P(s) ~ exp((1/2 s.J.s + h.s) / T) is computed by listing every
configuration, so the script can report how close the samples are: the total-variation distance
between the empirical and the exact distribution (0 = identical, 1 = disjoint).
Each sample is one short metered job; the sampler is used by one person at a time.
"""
import argparse
from collections import Counter
import getpass
import itertools
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError

MODELS = {
    # 4 spins on a ring, aligned couplings: two ground states, all up and all down.
    "ferromagnet": ([[0, 1, 0, 1], [1, 0, 1, 0], [0, 1, 0, 1], [1, 0, 1, 0]], [0, 0, 0, 0]),
    # 3 spins, all anti-aligned couplings: no configuration satisfies every bond (frustration).
    "frustrated": ([[0, -1, -1], [-1, 0, -1], [-1, -1, 0]], [0, 0, 0]),
    # 4 independent spins with fields: each p-bit is biased on its own.
    "fields": ([[0] * 4 for _ in range(4)], [0.8, 0.3, -0.3, -0.8]),
}


def exact_distribution(J, h, T):
    n = len(h)
    weights = {}
    for s in itertools.product((-1, 1), repeat=n):
        energy = -0.5 * sum(J[i][j] * s[i] * s[j] for i in range(n) for j in range(n)) - sum(h[i] * s[i] for i in range(n))
        weights[s] = math.exp(-energy / T)
    z = sum(weights.values())
    return {s: w / z for s, w in weights.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--model", choices=sorted(MODELS), default="ferromagnet")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--samples", type=int, default=150)
    parser.add_argument("--burn-in", type=int, default=200, help="sweeps before the first sample")
    parser.add_argument("--thin", type=int, default=10, help="sweeps between samples")
    parser.add_argument("--mock", action="store_true", help="run the sampler algorithm on the server, without hardware")
    parser.add_argument("--port", default="COM3", help="the bench's serial port, as given by the operator")
    args = parser.parse_args()
    J, h = MODELS[args.model]

    client = MessengerClient(args.url)
    client.login(args.user, os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: "))
    try:
        client.nano("connect", **({"mode": "mock"} if args.mock else {"mode": "serial", "port": args.port}))
    except MessengerError as error:
        sys.exit(f"Could not claim the sampler: {error.status_code} {error.detail}")
    try:
        client.nano("load", J=J, h=h, temperature=args.temperature, seed=11)
        samples = []
        for k in range(args.samples):
            record = client.nano_sample(burn_in=args.burn_in if k == 0 else 0, thin=args.thin)
            samples.append(tuple(record["spins"]))
            if (k + 1) % 25 == 0:
                print(f"  {k + 1}/{args.samples} samples", flush=True)
    finally:
        client.nano("disconnect")      # always release the sampler for the next person

    exact = exact_distribution(J, h, args.temperature)
    counts = Counter(samples)
    tv = 0.5 * sum(abs(counts.get(s, 0) / len(samples) - p) for s, p in exact.items())
    print(f"\n{args.model}, T = {args.temperature}, {len(samples)} samples "
          f"({'server emulation' if args.mock else 'hardware sampler'})")
    print(f"{'configuration':>16}  {'measured':>8}  {'exact':>6}")
    for s, p in sorted(exact.items(), key=lambda kv: -kv[1])[:8]:
        print(f"{''.join('+' if x > 0 else '-' for x in s):>16}  {counts.get(s, 0) / len(samples):8.3f}  {p:6.3f}")
    print(f"\ntotal-variation distance to the exact distribution: {tv:.3f}")
    # With n samples, sampling noise alone gives a TV of roughly sqrt(states / n) / 2.
    print(f"(for {len(samples)} samples, sampling noise alone gives about {0.5 * math.sqrt(len(exact) / len(samples)):.3f})")


if __name__ == "__main__":
    main()
