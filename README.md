# vonNeuro Messenger API for Python

The Python interface to [vonNeuro Messenger](https://series-marina-gigantic.ngrok-free.dev), the server in front of the vonNeuro lab bench:
real memristors, RC coprocessors and a hardware p-bit sampler that you can measure and program
remotely. This repository is only the client: every call is an HTTPS request to the server,
which checks your account, the operator's hardware switches and the voltage rules, meters your
bench time and queues your job on the shared instrument.

## Install

Python 3.11 or newer:

```bash
pip install git+https://github.com/vonneuro/messenger-api.git
```

The only dependency is `httpx`.

## Account

Sign up at https://series-marina-gigantic.ngrok-free.dev/register (or with `MessengerClient(url).register(...)`). New accounts get an
allowance of bench time; ask for more with `client.request_time(hours, reason)`. The full manual,
with how the bench is wired and how the memristors are driven, is on the server's **Guide** page
once you are signed in.

## Quick start

```python
from getpass import getpass
import matplotlib.pyplot as plt
from vonneuro_messenger import MessengerClient

client = MessengerClient("https://series-marina-gigantic.ngrok-free.dev")
client.login("YOUR_NAME", getpass("Password: "))

data = client.memristor_array(3, amplitude=0.25, frequency=10)   # memristor C03, ±0.25 V, 10 Hz
iv = data["memristor"]
plt.plot(iv["device_voltage_v"], [i * 1e6 for i in iv["current_a"]])
plt.xlabel("device voltage (V)"); plt.ylabel("current (µA)"); plt.show()
```

That is one guarded measurement of one real memristor, a few seconds of bench time at 10 Hz.

## Examples

[examples/](examples/README.md) has 15 runnable studies, each with a short guide: memristor
I–V loops, frequency and energy, a scan of all 16 memristors, RC time constants, VOLT programs on
the devices, the p-bit sampler, saving results, and the same measurement over plain HTTP.

```bash
python examples/quickstart.py --user YOUR_NAME
```

## Method reference

All on `MessengerClient(url, credentials_file=None, timeout=30)`. Calls that fail raise
`MessengerError`, with `.status_code` and `.detail`.

| Method | Returns | What it does |
|---|---|---|
| **Account** | | |
| `login(username, password)` | `User` | Logs in and stores the session |
| `me()` | `User` | `.username`, `.is_admin`, `.email`, … |
| `usage()` | dict | `remaining_seconds` (None: unlimited), bench time used |
| `request_time(hours, reason)` · `time_requests()` | dict · list | Ask for more bench time · your requests and their status |
| `register(username, email, password, github_username=None, purpose=None)` | `User` | Create an account and log in |
| `request_repo_access(github_username, purpose=None)` | `User` | Ask for the client repository |
| `logout()` | None | Forget the stored session |
| **Bench** | | |
| `hardware()` | list of dicts | What is wired and switched on |
| `instrument_status()` | dict | Instrument serial and output limits |
| `memristor_array(channel, amplitude=0.25, frequency=10.0, wait_timeout=None)` | dict | One guarded I–V sweep |
| `volt_run(source, inputs, target, wait_timeout=180)` | dict | A VOLT 0.1 program on `"memristor:N"` or `"rc:N"` |
| `measure(channel=None, waveform="sine", amplitude=0.1, offset=0.0, frequency=1000, duration=0.004, samples=1024, sample_rate=100000, range_v=None, fixture=None)` | dict | One scope/generator job |
| `nano(action, **data)` · `nano_sample(burn_in=0, thin=10)` · `nano_status()` | dict | p-bit sampler |
| **VOLT processing** | | |
| `volt_programs()` | list of dicts | Installed programs and their signatures |
| `run_volt(program, inputs)` · `submit_volt(program, inputs)` | dict · `Job` | Run and wait · submit only |
| **Jobs and files** | | |
| `submit(name, job_type, args=None, timeout_seconds=None, ...)` | `Job` | Submit any job |
| `wait(job_id, timeout=3600, on_update=None)` | `Job` | Until the job ends |
| `get_job(job_id)` · `list_jobs()` · `cancel(job_id)` | `Job` · list · None | One job · yours, newest first · cancel |
| `stream_logs(job_id)` | iterator of str | Log lines as they appear |
| `list_files(job_id)` · `download_file(job_id, name, dest=None)` · `get_results(job_id, output_dir)` | list · `Path` · list | A job's output files |
| `dashboard()` | `DashboardStats` | Counts of your jobs by status |
| **Admin** | | |
| `set_hardware(item_id, enabled)` | list | Switch a device on or off for everyone else |

`AsyncMessengerClient` has the same account, job, file and VOLT-processing methods as
coroutines, but not the bench helpers (`memristor_array`, `measure`, `volt_run`, `hardware`,
`usage`, `nano`). Use the synchronous client for the bench.

**In the Python lab** the same `client` methods return plain dicts, where the real client returns
`User` and `Job` objects. The lab adds a few names of its own: `save(name, data)` and
`save_csv(name, rows, header)` offer files for download, and `run_energy()` returns the device
energy measured so far in the run. `plt.show()` puts a figure on the page.

## Errors and limits

| HTTP | Meaning | What to do |
|---|---|---|
| 401 | Not logged in, or the session could not be renewed | `client.login(...)` again |
| 402 | Out of bench time, or a sweep longer than your time left | `client.usage()`; `client.request_time(...)` |
| 403 | Job type not open to your account | Use one of the job types open to every account |
| 409 | Hardware switched off, a voltage rule broken, fixture busy, or a job already running | Read `error.detail`; for busy hardware, retry later |
| 422 | Invalid arguments | Read `error.detail` |

```python
try:
    client.measure(channel=2, amplitude=0.5, offset=0.0, duration=0.05, samples=8192, sample_rate=102_400)
except MessengerError as error:
    print(error.status_code, error.detail)   # 409 W2 feeds the MUX2 memristor array and must stay within 0..5 V …
```

- **Metering:** bench time is metered per job, rounded up, and queue time does not count.
- **One active job per account.** Wait for it, or cancel it.
- **A shared instrument.** Everyone's jobs take turns in arrival order, so a job may wait a few
  seconds before it runs.
- **Only finite outputs.** Every output stops by itself; nothing is left driving after a job.

## Without the Python client

The client is a thin layer over HTTP. With `requests`:

```python
import requests
base = "https://series-marina-gigantic.ngrok-free.dev"
token = requests.post(f"{base}/api/auth/login",
                      data={"username": "YOUR_NAME", "password": "..."}).json()["access_token"]
auth = {"Authorization": f"Bearer {token}"}
job = requests.post(f"{base}/api/jobs", headers=auth, json={
    "name": "C03 sweep", "job_type": "messenger_instrument",
    "payload": {"args": ["--fixture", "memristor_array", "--array-channel", "3",
                         "--amplitude", "0.5", "--frequency", "10"]}}).json()
done = requests.get(f"{base}/api/jobs/{job['id']}/wait", headers=auth, params={"timeout": 25}).json()
data = requests.get(f"{base}/api/jobs/{job['id']}/outputs/measurements.json", headers=auth).json()
```

| Method and path | Body / query | Purpose |
|---|---|---|
| `POST /api/auth/login` | form: `username`, `password` | Returns `access_token`, `refresh_token` |
| `POST /api/auth/refresh?refresh_token=…` | | A new pair of tokens |
| `GET /api/billing/usage` | | Bench time left and used |
| `GET /api/hardware/available` | | The hardware list |
| `GET /api/jobs/instrument-status` | | Instrument serial and output limits |
| `POST /api/jobs` | `{"name", "job_type", "payload": {"args": [...]}}` | Submit a job |
| `POST /api/jobs` (VOLT) | `{"name", "job_type": "volt_run", "payload": {"source", "inputs", "target"}}` | VOLT 0.1 on the bench; the result is the job's `result.json` |
| `GET /api/jobs/{id}` · `GET /api/jobs/{id}/wait?timeout=25` · `DELETE /api/jobs/{id}` | | Job state · state when it ends (or after the timeout) · cancel |
| `GET /api/jobs/{id}/outputs` · `GET /api/jobs/{id}/outputs/{file}` | | List · download result files |
| `GET /api/jobs/volt-programs` | | VOLT processing programs |
| `POST /api/nano/command` · `POST /api/nano/sample` · `GET /api/nano/status` | `{"action", "data"}` · `{"burn_in", "thin"}` | p-bit sampler |

Arguments for `messenger_instrument` jobs, written in full:

- capture: `--samples`, `--sample-rate`, `--range`;
- drive: `--channel 1|2`, `--waveform dc|sine|square|triangle`, `--amplitude`, `--offset`,
  `--frequency`, `--duration`;
- presets: `--fixture generic|rc_led|memristor_array`, with `--array-channel 0..15` for the
  memristor array.

## Fair use

The hardware is real and shared. Large drives can change a memristor's state for the next user,
so start at small amplitudes (0.25 V).

## License

MIT, see [LICENSE](LICENSE).
