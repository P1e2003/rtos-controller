# POSIX Pathfinder Cross-Check (`pi_demo`)

Single-core Linux demo of the classic priority-inversion scenario (Mars Pathfinder style) using `SCHED_FIFO` threads and a shared mutex.

## What it shows

Three threads pinned to **one CPU** (`CPU 0` when available in the process affinity mask):

| Thread | Priority | Behavior |
| :--- | :---: | :--- |
| **L** | `10` | Locks the mutex first, records `t0`, releases `H`/`M`, runs `40 ms` of CPU work inside the critical section, unlocks |
| **H** | `30` | Released at `t0 + 10 ms`; locks the mutex, runs `5 ms` of CPU work, unlocks |
| **M** | `20` | Released at `t0 + 20 ms`; burns `120 ms` of CPU work, does **not** touch the mutex |

The same scenario runs under three POSIX mutex protocols:

| Mode | Protocol | Expected `H` response |
| :--- | :--- | :--- |
| `none` | `PTHREAD_PRIO_NONE` | `~155 ms` (`> 120 ms`; `M` preempts `L` -> priority inversion) |
| `inherit` | `PTHREAD_PRIO_INHERIT` | `~35 ms` (`< 60 ms`; `L` inherits `H`'s priority while `H` waits) |
| `protect` | `PTHREAD_PRIO_PROTECT` | `~35 ms` (`< 60 ms`; immediate priority ceiling = `PRIO_H`) |

Busy-waits use `CLOCK_THREAD_CPUTIME_ID` so preemption by `M` does not shrink `L`'s critical section. `H`'s response time is measured from its **scheduled** absolute release (`t0 + 10 ms`) to completion, so `PTHREAD_PRIO_PROTECT` accurately reports the full critical-section blocking delay rather than only post-lock execution.

## Build & run (Ubuntu / Linux VM)

Requires `root` or `CAP_SYS_NICE` for `SCHED_FIFO`. If unprivileged, `pi_demo` exits with code **`77`** (`EPERM`) and **never** silently falls back to `SCHED_OTHER`.

```bash
make -C posix

# Option 1: run with sudo
sudo ./posix/pi_demo --mode all

# Option 2: grant CAP_SYS_NICE once, then run without sudo
sudo setcap cap_sys_nice+ep posix/pi_demo
./posix/pi_demo --mode all

# Run a single protocol mode
sudo ./posix/pi_demo --mode none
sudo ./posix/pi_demo --mode inherit
sudo ./posix/pi_demo --mode protect

# Machine-readable CSV for reproduce.sh / plots/curves.py / sim/report.py
sudo ./posix/pi_demo --mode all --csv > outputs/posix_results.csv
```

Example human-readable output:

```text
Pathfinder cross-check (single core, SCHED_FIFO)
Timings: L=40ms lock, H=+10ms (5ms), M=+20ms (120ms)
Expect PTHREAD_PRIO_NONE >120ms (~155ms), INHERIT/PROTECT <60ms (~35ms)
(VM/hypervisor jitter can still shift absolute timings)

PTHREAD_PRIO_NONE       H response = 155.42 ms  (inversion_gt_120ms)
PTHREAD_PRIO_INHERIT    H response =  35.61 ms  (bounded_lt_60ms)
PTHREAD_PRIO_PROTECT    H response =  35.48 ms  (bounded_lt_60ms)
```

Example `--csv` output (`stdout` contains only the header and rows; warnings go to `stderr`):

```csv
protocol,h_response_ms,expected_pattern
PTHREAD_PRIO_NONE,155.42,inversion_gt_120ms
PTHREAD_PRIO_INHERIT,35.61,bounded_lt_60ms
PTHREAD_PRIO_PROTECT,35.48,bounded_lt_60ms
```

## Notes & Oral-Defense Caveats

- **Deterministic lock ordering:** `H` and `M` block on an initialization semaphore until `L` has already acquired the mutex and stamped `t0`, eliminating startup lock-ordering races under VM hypervisor pauses.
- **Linux RT throttling (`sched_rt_runtime_us`):** By default, Linux limits `SCHED_FIFO`/`SCHED_RR` threads to `950 ms` of CPU per `1000 ms` period (`/proc/sys/kernel/sched_rt_runtime_us` = `950000`, `sched_rt_period_us` = `1000000`). Each mode uses `~165 ms` of RT CPU (`~495 ms` across all 3 modes with `50 ms` inter-mode gaps), staying safely below the throttling limit.
- **VM hypervisor jitter:** On VirtualBox/VMware/WSL2, host scheduling pauses and vCPU preemption can still shift absolute millisecond values, which is why the Python discrete-event simulator is the primary deterministic evaluation engine and this C binary serves as a real-OS cross-check (**`NONE` ≫ `INHERIT` ≈ `PROTECT`**, ~3x–4x latency drop).
- **Optional `mlockall`:** Locks memory to reduce page-fault jitter; logs a warning to `stderr` and continues if `CAP_IPC_LOCK` is unavailable.
- **Linux-only:** macOS cannot build this binary (`sched_setaffinity`, `cpu_set_t`, and `clock_nanosleep` are Linux/POSIX-RT APIs).
