"""A tour of the vonNeuro Messenger bench from Python: log in, see what is available,
record with the scope, charge an RC coprocessor, sweep a memristor, run VOLT programs and
sample the p-bits. Every step is in PROGRAMMING.md.

    python examples/bench_tour.py --url https://series-marina-gigantic.ngrok-free.dev --user YOUR_NAME

The password is asked for (or read from MESSENGER_PASSWORD). Steps that drive hardware
use only the presets and limits the operator has enabled; switched-off devices are skipped.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # not needed once the API is pip-installed
from vonneuro_messenger import MessengerClient, MessengerError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("MESSENGER_URL", "https://series-marina-gigantic.ngrok-free.dev"))
    parser.add_argument("--user", required=True)
    args = parser.parse_args()
    password = os.environ.get("MESSENGER_PASSWORD") or getpass.getpass(f"Password for {args.user}: ")

    # 1. Log in.
    client = MessengerClient(args.url)
    me = client.login(args.user, password)
    usage = client.usage()
    remaining = "unlimited" if usage.get("remaining_seconds") is None else f"{usage['remaining_seconds']:.0f} s"
    print(f"Logged in as {me.username} (admin: {me.is_admin}); compute time left: {remaining}")

    # 2. What is on the bench right now.
    hardware = {item["id"]: item for item in client.hardware()}
    for item in hardware.values():
        wiring = ", ".join(x for x in [item["scope_channel"] and f"CH{item['scope_channel']}",
                                       item["wavegen_channel"] and f"W{item['wavegen_channel']}",
                                       item["pins"] and f"pins {item['pins'][0]}-{item['pins'][1]}",
                                       item["max_voltage_v"] and f"max {item['max_voltage_v']} V"] if x)
        print(f"  {'ON ' if item['enabled'] else 'off'}  {item['id']:<14} {item['name']}  ({wiring})")

    # 3. Scope only: record both inputs, drive nothing.
    data = client.measure(samples=1024, sample_rate=100_000)
    for ch, s in data["summary"].items():
        print(f"Scope CH{ch}: mean {s['mean_v']*1000:+.2f} mV, min {s['min_v']*1000:+.2f}, max {s['max_v']*1000:+.2f}")

    if hardware.get("mux16", {}).get("enabled") or me.is_admin:
        # 4. Drive an RC coprocessor: a VOLT program steps W1 to u x the channel's drive voltage for
        #    200 ms and observes the charging current (W1 never goes negative: polarised capacitors).
        rc_program = """
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
        reply = client.volt_run(rc_program, {"u": 0.5}, "rc:2")
        current = reply["result"]["value"]
        print(f"RC 2 charged at {reply['provenance']['drive_v']:.2f} V: {current[0]*1e3:.2f} mA falling to "
              f"{current[-1]*1e3:.2f} mA over 200 ms ({reply['measured_energy_j']*1e6:.0f} µJ delivered)")

        # 4b. The same kind of drive, stated in volts and milliseconds: a pulse. client.pulse(target,
        #     volts, ms) writes the VOLT program for you; RC channels take 0..4 V (2.24 V on channel 4)
        #     for 5..2000 ms. client.pulse_rc(2, 1.0, 100) is the same call.
        reply = client.pulse("rc:2", 1.0, 100)
        print(f"Pulse: rc:2 at {reply['pulse']['applied_v']:.2f} V for 100 ms -> "
              f"{reply['result']['value'][0]*1e3:.2f} mA at the start")

    if hardware.get("memristor-array", {}).get("enabled") or me.is_admin:
        # 5. One memristor's I-V loop. W2 swings around the 2.47 V bias of the array, so the device
        #    sees +-0.25 V while W2 itself stays positive.
        data = client.memristor_array(3, amplitude=0.25, frequency=10)
        iv = data["memristor"]
        print(f"C{data['array_channel']:02d} (device {data['device_id']}, pins {data['pins']}): "
              f"rest {iv['rest_current_a']*1e9:+.0f} nA, peak {iv['max_abs_current_a']*1e6:.2f} uA, "
              f"{len(iv['device_voltage_v'])} points, device energy {data['energy']['device_j']*1e9:.2f} nJ")

        # 6. A VOLT 0.1 program on a memristor: hold u x 0.3 V on the device for 10 ms.
        program = """
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
        reply = client.volt_run(program, {"u": 0.5}, "memristor:15")
        print(f"VOLT on memristor:15 -> {reply['result']['value']*1e9:+.1f} nA mean, "
              f"{reply['measured_energy_j']*1e9:.3f} nJ at the terminals")

        # 6b. A memristor pulse in volts: the device itself held at +0.1 V for 20 ms (memristors take
        #     -0.3..+0.3 V for 5..500 ms). Small and short: a strong pulse can switch a device.
        reply = client.pulse_memristor(15, 0.1, 20)
        mean = sum(reply["result"]["value"]) / len(reply["result"]["value"])
        print(f"Pulse: memristor:15 at {reply['pulse']['applied_v']:+.2f} V for 20 ms -> {mean*1e9:+.1f} nA mean")
    else:
        print("The memristor array is switched off; skipping the memristor examples.")

    # 7. A VOLT processing program (software only, no hardware).
    reply = client.run_volt("device22_current_level", {"samples": [1e-6, -1.2e-6, 0.8e-6],
                                                       "baseline_rms_a": 0, "scale_a": 2e-6})
    print(f"VOLT device22_current_level -> {reply['result']['value']:.3f}")

    # 8. pIsing p-bits on the Nano (mock mode runs the same sampler in software).
    if hardware.get("pbit", {}).get("enabled") or me.is_admin:
        try:
            client.nano("connect", mode="mock")
            client.nano("load", J=[[0, 1], [1, 0]], h=[0.2, -0.2], temperature=1.0, seed=7)
            sample = client.nano_sample(burn_in=10, thin=10)
            print(f"pIsing sample: spins {sample['spins']} after {sample['completed_sweeps']} sweeps")
        except MessengerError as error:
            print("pIsing unavailable:", error)
        finally:
            try:
                client.nano("disconnect")
            except MessengerError:
                pass
    print("Done; every job is listed on the dashboard.")


if __name__ == "__main__":
    main()
