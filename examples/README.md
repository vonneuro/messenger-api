# Examples

Runnable scripts for the vonNeuro Messenger bench: real memristors, RC coprocessors and a hardware
p-bit sampler. Every script is a short, commented study that you can read in a few minutes, run
as it is, and then change.

## Before you run them

1. **Install** the API, plus matplotlib for the plots (optional; without it the scripts still
   print their results and save JSON):

   ```bash
   pip install git+https://github.com/vonneuro/messenger-api.git
   pip install matplotlib
   ```

2. **Create an account** at https://series-marina-gigantic.ngrok-free.dev/register. Every
   account gets an hour of bench time to start with.

3. **Run a script** with your username. It asks for your password, or reads it from
   `MESSENGER_PASSWORD`, so it never has to be written into a script:

   ```bash
   python examples/quickstart.py --user YOUR_NAME

   # macOS / Linux: the password once per terminal
   export MESSENGER_PASSWORD='...'
   # Windows PowerShell
   $env:MESSENGER_PASSWORD = '...'
   ```

Every script also takes `--url` (default: the public server, or `MESSENGER_URL`) and `--help`,
which lists its options. Files are written to the folder you run it from.

## The scripts

The bench time column is roughly what a run costs at the default settings, excluding any wait in
the queue (waiting is not counted).

### Start here

| Script | What it shows | Bench time |
|---|---|---|
| `quickstart.py` | Log in, list what is switched on, sweep one memristor's I–V loop, plot it | ~5 s |
| `bench_tour.py` | A tour of everything: hardware, the scope, an RC charge, a memristor sweep, VOLT on a memristor, p-bits | ~30 s |
| `account.py` | Your bench time, what is switched on, and asking for more time (`--request-hours 3 --reason "..."`) | none |

### Memristors

The sixteen memristors sit behind a multiplexer with their far end at a 2.47 V bias: the
generator swings around that level, so each device sees ± the amplitude while the generator
itself never goes negative. Positive current flows into the memristor. Start at 0.25 V: a large
amplitude can switch a device, and it stays switched for the next user.

| Script | What it shows | Bench time |
|---|---|---|
| `memristor_iv.py` | One channel's loops at increasing amplitudes; stops above 40 µA | ~10 s |
| `memristor_frequency.py` | The memristor fingerprint: the pinched loop narrows as the frequency rises | ~30 s |
| `memristor_array_scan.py` | All 16 channels: resistance, rectification, loop area, a 4 × 4 plot | ~1.5 min |
| `memristor_energy.py` | Energy per sweep and apparent resistance against amplitude, compared with A² | ~20 s |
| `memristor_step.py` | Hold a memristor at a few voltages with a VOLT program and watch it rectify | ~15 s |
| `sweep_to_csv.py` | Save a sweep for your own analysis: `loop.csv`, `summary.json` and the job's raw file | ~5 s |

### RC coprocessors

Sixteen series resistor–capacitor branches with time constants from 12 ms to 3.4 s. A capacitor
keeps its charge between visits, so each channel holds state.

| Script | What it shows | Bench time |
|---|---|---|
| `rc_charge.py` | Charge one RC coprocessor with a VOLT program and plot its current | ~5 s |
| `rc_survey.py` | Discharge, then charge, several channels; fit each time constant from the decay | ~1 min |

### VOLT

| Script | What it shows | Bench time |
|---|---|---|
| `volt_bench.py` | Write a VOLT 0.1 program, run it on a memristor or an RC, and see a device contract refuse it | ~10 s |
| `volt_program.py` | List the installed processing programs (`--list`) and run one; software only | none |

### p-bits

| Script | What it shows | Bench time |
|---|---|---|
| `pbit_sampler.py` | Sample an Ising model on the hardware p-bit sampler and compare with the exact distribution (`--mock` runs the same algorithm without hardware) | ~20 s |

### Without the Python client

| Script | What it shows | Bench time |
|---|---|---|
| `memristor_http.py` | One memristor sweep over plain HTTP with the standard library only: a template for any other language | ~5 s |

## Changing them

Each script's options are at the top of `main()` (`parser.add_argument(...)`), and each bench call
is a single line: `client.memristor_array(...)`, `client.volt_run(...)`, `client.nano(...)`. The
repository README lists every method, what it returns and the errors.

| If you see | It means |
|---|---|
| `Refused: 409 … not available right now` | That hardware is switched off by the operator; try later |
| `Refused: 402 …` | Out of bench time, or a slow sweep longer than your time left: `account.py --request-hours …` |
| `Refused: 409 W2 feeds the MUX2 memristor array …` | A drive outside 0 … 5 V on the memristor generator: use `memristor_array()` instead |
| `401` | Wrong password, or the session could not be renewed |
