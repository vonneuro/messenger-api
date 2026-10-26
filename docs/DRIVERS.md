# Hardware drivers: put your device on the vonNeuro network

A **driver** is a short Python class that describes your hardware and drives it. The
**vonneuro-node agent** runs it on the computer next to the hardware. The agent connects out to
vonNeuro Messenger (the hub), publishes your devices, takes the jobs people send, and runs them
through your driver. It uploads the results and reports usage. Your hardware stays in your
laboratory, and your driver decides what is safe.

```text
user (website, Python, VOLT) ──> hub: accounts, checks, queue, metering ──> agent on your computer ──> your driver ──> hardware
                                 <── results, energy, usage ─────────────── (dials out, no open port) <──
```

This guide covers installing the agent and writing a driver: its operations, the settings your
node's admins change from the website, the device's page, and how VOLT programs run on your
device. Two drivers come with the agent:

- `vonneuro_node/drivers/simulated.py` is a simulated RC circuit that runs anywhere.
- `vonneuro_node/drivers/template.py` is a skeleton for a real instrument.

The vonNeuro bench runs on this same kit, as one node like any other; the agent download on the hub
(`/downloads/vonneuro-node.zip`) includes its driver as a full real example.

## 1. Install and connect

```bash
pip install git+https://github.com/vonneuro/messenger-api.git    # the client and the vonneuro-node agent
```

1. **Register the node.** On the website, open **My hardware → Register a node** and get a
   one-time pairing code. While the hub is in early access, a superadmin approves each new node
   first.
2. **Pair this computer.** This writes `node.toml`, with the node's secret:

   ```bash
   vonneuro-node pair --hub https://series-marina-gigantic.ngrok-free.dev --code ABCD-EFGH
   ```

3. **Check the driver.** The first run uses the simulated driver, so you can try the whole loop
   with no hardware. `check` validates every declaration, offline.

   ```bash
   vonneuro-node check
   ```

4. **Run the agent:**

   ```bash
   vonneuro-node run                     # your devices go online; stop with Ctrl+C
   vonneuro-node service install         # optional: start it at logon (Windows) or as a systemd unit (Linux)
   ```

Your devices appear under **My hardware**, private at first. You choose who may use them: every
account, accounts you grant time to, or only your node's admins. You can also add colleagues as
admins of the node.

To use your own driver, set its class in `node.toml`:

```toml
hub = "https://..."
node_id = 3
secret = "..."                 # private
journal = "jobs.jsonl"         # every job this node ran, one JSON line each

[driver]
class = "my_driver:MyDriver"   # a module on the Python path, and the class
[driver.options]               # passed to the driver's constructor
port = "COM5"
```

## 2. A driver

```python
from vonneuro_node import Driver, DeviceSpec, Operation, RunResult

class MyDriver(Driver):
    def devices(self):                       # what the hardware is
        return [DeviceSpec(slug="crossbar-8x8", name="8x8 memristor crossbar", kind="memristor",
            specs={"cells": 64},
            operations=[Operation("pulse", "Hold one cell at a voltage for a time",
                params={"row": {"type": "integer", "minimum": 0, "maximum": 7},
                        "col": {"type": "integer", "minimum": 0, "maximum": 7},
                        "volts": {"type": "number", "minimum": -1, "maximum": 1, "unit": "V"},
                        "ms": {"type": "number", "minimum": 5, "maximum": 100, "unit": "ms", "default": 20}},
                result="pulse.json")])]

    def check(self, slug, operation, params, ctx):   # what is unsafe: refuse before driving
        return None if instrument.ready() else "The probe station is busy"

    def run(self, slug, operation, params, ctx):     # drive it; write files into ctx.output_dir
        current = instrument.pulse(params["row"], params["col"], params["volts"], params["ms"] / 1e3)
        (ctx.output_dir / "pulse.json").write_text(json.dumps({"current_a": current}))
        return RunResult(exit_code=0, usage_seconds=params["ms"] / 1e3)
```

**`DeviceSpec`** describes one device:

| Field | Meaning |
|---|---|
| `slug`, `name`, `kind` | Its address (`owner/slug`), a name, and a kind (`memristor`, `rc`, `pbit`, `photonic`, …) |
| `description`, `specs` | Free text and facts shown to users (`{"cells": 64, "max_voltage_v": 1.0}`) |
| `operations` | What people can run, below |
| `settings` | What your node's admins can change from the website (section 3) |
| `page` | The device's default page, in Markdown (section 4) |
| `enabled` | Your own switch: `False` keeps the device listed but refuses its jobs |
| `capabilities` | Tags for features of your own (e.g. a console) |

**`Operation(name, summary, params, estimate=None, timeout_s=60, result=None, outputs=[])`**:

- **`params`:** each parameter is `{"type": ..., constraints..., "default": ...}`. The types are
  `integer`, `number`, `string`, `boolean`, `object` and `array`. The constraints are
  `minimum`/`maximum`, `exclusiveMinimum`/`exclusiveMaximum`, `enum`, `minLength`/`maxLength`,
  `pattern` and `maxProperties`, plus `unit` and `description`. A parameter without a default is
  required. The hub refuses bad parameters before queueing. The agent checks them again, and your
  `check()` sees only valid ones.
- **`estimate`:** the run time in seconds as a formula of the parameters, e.g.
  `"6 / frequency + 15"`. The hub refuses jobs longer than the user's time left.
- **`result`:** the main JSON file. `device.run(...)` returns it to Python users.

**`check(slug, operation, params, ctx)`** returns a reason to refuse, or `None`. It is the last
word on safety: the hub can never override it. `ctx.privileged` is true for your node's admins
and superadmins.

**`run(slug, operation, params, ctx)`** drives the hardware and writes its result files into
`ctx.output_dir`. Poll `ctx.cancelled()` in long runs, and respect `ctx.timeout_s`. It returns
`RunResult(exit_code, usage_seconds, logs="", energy_j=None)`. Always leave the hardware safe,
in `finally`.

**`status(slug)`** is a cheap health report. It is shown to your node's admins and never drives
anything.

The agent runs one job at a time, so your driver needs no locking. Jobs and their results go
through the hub; your computer keeps a journal of every job.

## 3. Settings your admins change from the website

Limits and calibration often change, and nobody should have to edit code for that. Declare
**settings**: each one is a parameter spec plus the `value` in force now. Your node's admins
change them on **My hardware → Settings**. The hub sends the change to the agent, which applies
it between jobs through your `apply_settings`. The page and the operations then show the new
values.

```python
settings={"max_voltage_v": {"type": "number", "exclusiveMinimum": 0, "maximum": 3.0, "unit": "V",
                            "default": 1.0, "value": self.max_voltage_v,
                            "description": "Largest |voltage| on a cell"}}

def apply_settings(self, slug, values):            # checked against the spec already; None = default
    for name, value in values.items():
        self.settings[name] = DEFAULTS[name] if value is None else value
    SETTINGS_FILE.write_text(json.dumps(self.settings))     # persist: they must survive a restart
```

Raise `ValueError` to refuse a change; the admin sees your message. Use settings for your bounds
too: build each operation's `maximum` from them, so the hub refuses out-of-range jobs. The bench's
pulse limits are settings of its two devices, built exactly this way.

## 4. The device's page

Every device has a page: what the hardware is and how to use it, with a Python script. Users see
it under **Hardware → About this device**, and on vonneuro.com once the device is listed. Your
driver's `page` is the default. Your node's admins can rewrite it on **My hardware → Page**,
without touching code.

The page is Markdown, and these placeholders stay live:

| Placeholder | Becomes |
|---|---|
| `{{name}}` `{{ref}}` `{{owner}}` `{{server}}` | the device's name and address, its owner, the hub's address |
| `{{setting:NAME}}` | one setting's value in force, with its unit (e.g. `1.5 V`) |
| `{{settings}}` | a table of every setting: in force, allowed, default |
| `{{operations}}` | every operation with its parameters, ranges and defaults |
| `{{python}}` | a Python script that logs in and runs the first operation |

So a page that says *"pulses up to ±{{setting:max_voltage_v}}"* is right after every change.

## 5. VOLT on your device

VOLT is the vonNeuro language for physical computing. A VOLT 0.1 `system` program says what to do
to the matter, what to observe, and what the device must offer:

```volt
system hold(u: real) {
  state material: adaptive;
  evolve material for 20 ms using drive.interval(u);
  observe current: vector from material.terminal_current;
  return current;
  execution { intent = evolve; output = current; }
  device { dialects = [dynamical, adaptive]; required_capabilities = [drive.interval, observe.terminals]; }
  thermodynamics { process_boundary = "drive_to_observation"; }
}
```

To accept VOLT programs, your driver declares a **contract** and writes **one function**,
`drive(u, seconds)`. `vonneuro_node.volt_device` compiles the program with the same compiler as
the vonNeuro bench. It checks the program's `device` contract against yours, so a program needing
`adaptive` is refused by a device that is only `dynamical`. It checks `u` and the interval, calls
your drive, reduces the samples to what the program observes, and writes the reply in the bench's
format:

```python
from vonneuro_node.volt_device import VoltContract, volt_operation, volt_check, run_volt_job

class MyDriver(Driver):
    def contract(self):
        return VoltContract(dialects=["dynamical", "adaptive"],      # the behaviour your device has
                            input_range=(-1.0, 1.0),                  # allowed u
                            duration_s=(0.005, 0.1),                  # allowed evolve interval
                            drive="the cell held at u x 1 V for the interval")

    def devices(self):
        return [DeviceSpec(..., operations=[..., volt_operation(self.contract())])]

    def check(self, slug, operation, params, ctx):
        if operation == "volt_run":
            return volt_check(self.contract(), params)      # refused programs never reach the hardware
        ...

    def run(self, slug, operation, params, ctx):
        if operation == "volt_run":
            return run_volt_job(self.contract(), params, ctx, self.drive)
        ...

    def drive(self, u, seconds):
        """drive.interval(u): drive the hardware for `seconds`, measure the terminal current."""
        volts = u * 1.0
        try:
            current = instrument.apply(volts, seconds)       # evenly sampled currents, in A
        finally:
            instrument.zero()
        energy = sum(volts * i * seconds / len(current) for i in current)
        return current, energy, {"device_voltage_v": volts}  # samples, energy (J), details for the record
```

| Contract field | Meaning |
|---|---|
| `dialects` | The kinds of behaviour the device has (`dynamical`, `adaptive`, `stochastic`, …). A program's `device.dialects` must all be among them |
| `capabilities` | What it offers; default `drive.interval` and `observe.terminals` |
| `observables` | What `observe` can read; default `terminal_current` |
| `input_range`, `duration_s` | The allowed `u` and evolve interval. Build them from your settings, as the template does |
| `drive` | What `drive.interval(u)` does, in words; it goes into every record |

People run a program on your device from Python:

```python
reply = client.device("you/crossbar-8x8").run("volt_run", source=program, inputs={"u": 0.5})
print(reply["result"]["value"], reply["measured_energy_j"], reply["contracts_checked"])
```

The reply is the same as on the bench: `ok`, `result` (a mean for `real`, 24 window means for
`vector`), `interval_s`, `measured_energy_j`, `contracts_checked`, `contracts_not_enforced`,
`provenance` and the compiled `vng`. See the **VOLT** page on the hub for the language itself.

## 6. Test before you connect

- `vonneuro-node check` validates every operation and setting, offline.
- Call your driver directly in a test, with a `RunContext` and a temporary folder:

  ```python
  from pathlib import Path
  import tempfile
  from vonneuro_node import RunContext
  ctx = RunContext(job_id=1, output_dir=Path(tempfile.mkdtemp()), timeout_s=10)
  driver = MyDriver()
  assert driver.check("crossbar-8x8", "pulse", {"row": 0, "col": 0, "volts": 5.0, "ms": 20}, ctx)   # refused
  print(driver.run("crossbar-8x8", "pulse", {"row": 0, "col": 0, "volts": 0.2, "ms": 20}, ctx))
  ```

- Run against the hub with the device **private**, which only your node's admins can use, before
  you share it.

## 7. Safety, in short

- **Operations only.** People send operations with bounded parameters; your computer never runs
  code it receives.
- **Your `check()` is the last word.** The hub checks parameters first, but only you know the
  hardware.
- **The agent dials out.** Nothing listens on your network. Every request is signed with your
  node's secret. Rotate it on My hardware, or disconnect the node, at any time.
- **You keep a record.** The journal (`journal` in `node.toml`) lists every job your node ran.
