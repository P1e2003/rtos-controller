# POSIX Pathfinder Cross-Check (`pi_demo`)

Single-core Linux demo of the classic priority-inversion scenario (Mars Pathfinder style) using `SCHED_FIFO` threads and a shared mutex.

## What it shows

Three threads on **one CPU**:

| Thread | Priority | Behavior |
|--------|----------|----------|
| **L** | 10 | Locks the mutex, does ~40 ms of CPU work, unlocks |
| **H** | 30 | Released ~5 ms later; needs the same mutex |
| **M** | 20 | Released ~10 ms later; burns ~30 ms CPU, **no** lock |

The same story runs under three mutex protocols:

| Mode | Protocol | Expected H response |
|------|----------|---------------------|
| `NONE` | `PTHREAD_PRIO_NONE` | ~70 ms (M preempts L → inversion) |
| `INHERIT` | `PTHREAD_PRIO_INHERIT` | ~40 ms (L inherits H while H waits) |
| `PROTECT` | `PTHREAD_PRIO_PROTECT` | ~40 ms (ceiling = H’s priority) |

Busy-waits use `CLOCK_THREAD_CPUTIME_ID` so preemption does not shrink L’s critical section. H’s response time is measured from its **scheduled** absolute release to completion.

## Build & run (Ubuntu / Linux VM)

Requires root (or `CAP_SYS_NICE`) for `SCHED_FIFO`. Does **not** fall back to `SCHED_OTHER`.

```bash
make -C posix
sudo ./posix/pi_demo
```

Example output:

```text
Pathfinder cross-check (single core, SCHED_FIFO)
Expect NONE ~70ms, INHERIT ~40ms, PROTECT ~40ms for H's response
(VM/hypervisor jitter can still move these around)

NONE        H response =  70.28 ms
INHERIT     H response =  40.76 ms
PROTECT     H response =  41.03 ms
```

Absolute numbers will move under a hypervisor; the important check is **NONE ≫ INHERIT ≈ PROTECT**.

## Notes

- Pinning: `sched_setaffinity` to CPU 0 before creating threads.
- Timing: `clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, …)` for releases.
- Optional `mlockall` reduces page-fault jitter (warns and continues if denied).
- macOS cannot build this demo (`cpu_set_t` / affinity APIs are Linux-only).
