# Real-Time Priority Inversion & Deadline-Protection Controller — Implementation Plan

## 1. Architecture & Tech Stack

This project implements a **deterministic, tick-based discrete-event simulator in Python** as the primary evaluation engine, paired with a **lightweight C/POSIX demo** as a real-world hardware/OS cross-check. Using a discrete simulator eliminates VM hypervisor jitter in primary measurements while providing exact, tick-level visibility for root-cause attribution.

* **Core Simulator:** Python 3.10+ (`dataclasses`, `pyyaml`, `matplotlib`, `pytest`)
* **POSIX Cross-Check:** C (`gcc`, `pthread`, `SCHED_FIFO`, `PTHREAD_EXPLICIT_SCHED`, `PTHREAD_PRIO_INHERIT`, `PTHREAD_PRIO_PROTECT`, `sched_setaffinity`, `clock_gettime(CLOCK_MONOTONIC)`)
* **Reproducibility:** A single LF-formatted `reproduce.sh` script (with `MPLBACKEND=Agg`) that installs pinned dependencies, runs all unit and property-based soundness tests, executes all scenarios with fixed seeds, and generates CSVs and Gantt charts on a fresh Ubuntu VM.

### Project Structure

```text
rtos-controller/
├── docs/
│   └── implementation_plan.md  # Canonical corrected specification & math reference
├── sim/
│   ├── __init__.py
│   ├── models.py               # Task, Job, Mutex, CriticalSection dataclasses & validation
│   ├── loader.py               # YAML scenario loader & schema validation
│   ├── engine.py               # Deterministic tick loop & overhead accounting
│   ├── schedulers.py           # RM, DM, EDF dispatchers & tie-breaking
│   ├── protocols.py            # NONE, PIP, and unified ICPP / Baker's SRP
│   ├── admission.py            # Iterative RTA (fixed-priority) & Baker's SRP test (EDF)
│   ├── attribution.py          # 6-category per-tick attribution & exhaustive miss-cause classifier
│   ├── runner.py               # Core scenario runner & CSV exporter
│   └── jitter.py               # Seeded bounded execution-time jitter & overrun sweep
├── scenarios/                  # YAML configs (pathfinder.yaml, overload.yaml, overhead.yaml, jitter.yaml)
├── plots/                      # Headless Matplotlib Gantt charts & jitter/miss-ratio curves
├── tests/                      # pytest unit tests + 1,000+ task-set property-based soundness sweep
├── posix/                      # pi_demo.c (SCHED_FIFO pinned to 1 CPU core) + Makefile
├── docs/ci.workflow.yml        # Ready-to-enable pytest + ruff + C compile CI template (copy to .github/workflows/ci.yml)
├── CONTRIBUTING.md             # Group branch/PR workflow & module ownership rules
├── requirements.txt            # Pinned Python dependency versions
└── reproduce.sh                # One-command Ubuntu reproduction pipeline (LF line endings)
```

---

## 2. Phase-by-Phase Implementation Plan

### Phase 1: Core Simulator & Tick Engine
1. **Define Data Models & Validation (`sim/models.py`, `sim/loader.py`):**
   * `CriticalSection`: `(offset: int, length: int, resource_id: str)` with validation `offset >= 0`, `length > 0`, `offset + length <= C`, and **non-overlapping / non-nested** sections (`s[i].offset + s[i].length <= s[i+1].offset`).
   * `Task`: `id: str`, `C: int` (WCET), `T: int` (period), `D: int` (relative deadline), `base_priority: int`, `phase: int = 0` (release offset, required for Mars Pathfinder & non-synchronous testing), `bcet: int | None = None` (defaults to `C`), and `sections: list[CriticalSection]`.
   * **Hard Model Invariants:** Validate `0 < C <= D <= T` (constrained/implicit deadlines required by iterative RTA & Baker's test), `phase >= 0`, `0 < bcet <= C`, and unique task IDs. When jitter is enabled, enforce `bcet >= max(offset + length)` (or auto-release held locks upon early job completion) so a short job can never leak a mutex.
   * `Job`: `task_id`, `job_index`, `release_time`, `abs_deadline`, `wcet`, `actual_exec`, `executed`, `remaining_exec`, `effective_priority`, `preemption_level`, `held_resources`, `blocked_on`, `missed` (recorded once), `finish_time`, and per-tick attribution counters.
   * `Mutex`: `id`, `owner`, `wait_queue`, and `ceiling` (computed strictly as `max(preemption_level)` across all tasks accessing the mutex).
   * **Lock Nesting Policy:** Nested locks are **explicitly forbidden** in model validation (non-overlapping critical sections) so `PIP` remains deadlock-free across randomized soundness sweeps and $B_i^{\text{PIP}}$ has an exact closed form. (An optional unit test may still document why nested locks can deadlock under `PIP`.)
2. **Enforce Strict Deterministic Tick Ordering (`sim/engine.py`, `sim/schedulers.py`):**
   Process every integer tick $t$ in this exact sequence (fixing lock-before-schedule and overhead double-counting bugs):
   1. **Release:** Spawn jobs whose `(t - phase) >= 0` and `(t - phase) % T == 0`.
   2. **Schedule & Lock-Acquire Loop:**
      * Select the highest-priority ready job (RM/DM by `effective_priority`, EDF by earliest `abs_deadline`; break ties deterministically by `task_id`). Under **SRP**, a candidate job may preempt the currently running job only if it has higher priority **and** its preemption level satisfies $\pi(J) > \Pi(t)$ (where $\Pi(t)$ is the current system ceiling of all locked mutexes).
      * If the selected job is at a critical-section entry (`executed == cs.offset`):
        * If the mutex is free, acquire it immediately (raising priority/system ceiling per protocol).
        * If the mutex is held, transition the job to `blocked_on = resource_id`, apply protocol elevation (e.g., `PIP` boosts the holder), and **re-run selection within the same tick $t$** so a runnable job executes during $[t, t+1)$.
   3. **Execute or Account Overhead:**
      * If a context switch or protocol priority boost occurred and $\varepsilon > 0$, consume 1 **`overhead`** tick (do **not** decrement `remaining_exec`).
      * Otherwise, execute the selected job for 1 tick (`executed += 1`, `remaining_exec -= 1`).
   4. **Unlock & Complete:**
      * Release any mutex whose `cs.offset + cs.length == job.executed` (restoring holder priority / recalculating system ceiling and waking waiters).
      * If `remaining_exec == 0`, release any remaining locks defensively, record `finish_time = t + 1`, and mark the job complete.
   5. **Per-Tick Attribution:** Record exactly one delay/progress bucket for every active (ready, running, or blocked) job during tick $t$.
   6. **Deadline Check & Overload Policy:** Flag any unfinished job where `t + 1 >= abs_deadline` (set `missed = True` once so misses are never double-counted). **Post-miss policy:** default to **`continue` (tardy completion)** so Scenario 2 reproduces EDF's classic cascading domino effect without ever stranding a held mutex; optionally support `abort` mode (releasing any held mutexes cleanly).

### Phase 2: Mitigation Protocols & Root-Cause Attribution
1. **Implement Synchronization Protocols (`sim/protocols.py`):**
   * **`NONE`:** Standard mutex with no priority adjustment and no system ceiling.
   * **`PIP` (Priority Inheritance Protocol — Fixed-Priority):** When a job blocks on a mutex held by a lower-priority holder, temporarily elevate the holder's `effective_priority` to `max(base_priority, max(effective_priority of all blocked waiters across all mutexes it holds))`; restore upon unlock.
   * **`ICPP` / Baker's `SRP` (Unified Ceiling Protocol — RM, DM, and EDF):**
     * Assign every task a static **preemption level** $\pi_i$: for Fixed-Priority (RM/DM), $\pi_i = \text{base\_priority}_i$; for EDF, $\pi_i$ is strictly ordered by shorter relative deadline $D_i$ (tie-broken consistently with `task_id`).
     * Resource ceiling: $\lceil R \rceil = \max\{\pi_i \mid \tau_i \text{ accesses } R\}$.
     * System ceiling: $\Pi(t) = \max\{\lceil R \rceil \mid R \text{ is currently locked}\}$ (0 if none locked).
     * Under **Fixed-Priority (`ICPP`)**, acquiring $R$ immediately raises the holder's effective priority to $\lceil R \rceil$ (equivalent to single-CPU SRP). Under **EDF (`SRP`)**, dynamic `abs_deadline` determines dispatch priority while $\pi(J) > \Pi(t)$ gates preemption — preventing deadlocks, chain-blocking, and unbounded inversion while matching Baker's EDF test exactly.
2. **Implement Exhaustive Per-Tick Attribution (`sim/attribution.py`):**
   Every tick $t$ an active job exists, classify it against **base priority / base EDF deadline** into exactly one of 6 mutually exclusive categories:

| Category | Condition | Meaning |
| :--- | :--- | :--- |
| **`running`** | Job executes on the CPU during tick $t$. | Normal execution progress. |
| **`overhead`** | CPU spends tick $t$ on context-switch or priority-change overhead ($\varepsilon > 0$). | Kernel/protocol runtime overhead. |
| **`interference`** | A job with higher **base** priority (or earlier `abs_deadline` / tie-break winner under EDF) runs. | Legitimate higher-priority preemption. |
| **`direct_blocking`** | Job is blocked on a mutex held by a lower-base-priority job, and that holder is running. | Bounded critical-section wait. |
| **`ceiling_or_push_through_blocking`** | A lower-base-priority job runs instead of this job due to `PIP` inheritance (push-through) or `ICPP`/`SRP` ceiling elevation. | Bounded protocol blocking (counts toward $B_i$). |
| **`inversion`** | Job is blocked on a mutex held by $L$, and a job $M$ runs with $\text{base}(L) < \text{base}(M) < \text{base}(J)$ (neither the lock holder nor higher base priority). | Unbounded medium-priority preemption (priority inversion). |

   *Total observed blocking* for RTA/SRP validation is `direct_blocking + ceiling_or_push_through_blocking`.
3. **Exhaustive Automated Miss-Cause Classification:**
   For every missed job, assign exactly one total-function cause:
   * **`INVERSION_MISS`:** `inversion > 0` and `(response_time - inversion) <= D_i` (removing priority inversion alone would have met the deadline).
   * **`MIXED_OVERLOAD_AND_INVERSION`:** `inversion > 0` and `(response_time - inversion) > D_i` (both inversion and overload/blocking contributed).
   * **`OVERRUN_OR_OVERHEAD_MISS`:** Baseline task set passed Admission Control, `inversion == 0`, and either $C_{\text{actual}} > \text{WCET}$ (jitter overrun) or $\varepsilon > 0$ (overhead) occurred.
   * **`OVERLOAD_MISS`:** `inversion == 0` and total utilization $U = \sum C_i / T_i > 1$ (or density $> 1$ for constrained deadlines).
   * **`SCHEDULABILITY_OR_BLOCKING_MISS`:** `inversion == 0`, $U \le 1$, and Admission Control (RTA / Baker's test) rejected the task set (e.g., non-harmonic RM limit or excessive critical-section blocking $B_i$).

### Phase 3: Admission Control (`sim/admission.py`)
Implement a pre-flight controller that accepts or rejects a task set before execution (strictly enforcing $0 < C_i \le D_i \le T_i$):

* **Rate-Monotonic (RM) / Deadline-Monotonic (DM) Fixed-Priority:** Iterative Response Time Analysis (RTA) with blocking term $B_i$:
  $$R_i^{(0)} = C_i + B_i, \qquad R_i^{(k+1)} = C_i + B_i + \sum_{j \in hp(i)} \left\lceil \frac{R_i^{(k)}}{T_j} \right\rceil \cdot C_j$$
  * Stop and **reject** immediately if $R_i^{(k+1)} > D_i$ (prevents infinite loops when $U > 1$); converge when $R_i^{(k+1)} == R_i^{(k)}$ and **accept** if $R_i \le D_i$ for all tasks.
  * Under **`ICPP` / `SRP`:** $B_i$ is the single longest critical section of any lower-priority task $k \in lp(i)$ on a resource with $\lceil r \rceil \ge P_i$.
  * Under **`PIP` (non-nested locks):** Includes **push-through blocking** over all resources with $\lceil r \rceil \ge P_i$:
    $$B_i^{\text{PIP}} = \min\!\left(\sum_{k \in lp(i)} \max_{r \in \text{cs}(k),\, \lceil r \rceil \ge P_i} L_{k,r},\;\; \sum_{r:\, \lceil r \rceil \ge P_i} \max_{k \in lp(i)} L_{k,r}\right)$$
  * Under **`NONE`:** If any shared resource spans across a priority band with an intervening medium-priority task ($P_H > P_M > P_L$ where $H$ and $L$ share a resource), reject due to unbounded priority inversion; if $H$ and $L$ are adjacent with no intervening $M$, compute $B_i$ over lower-priority critical sections sharing $i$'s resources (never silently treat $B_i = 0$).
* **Earliest-Deadline-First (EDF):** Baker's SRP test (**valid for `EDF + SRP` only**; reject `EDF + PIP` / `EDF + NONE` with shared resources or clearly flag them as uncertified), with tasks sorted by $D_1 \le D_2 \le \dots \le D_n$ (tie-broken consistently with `task_id`) and preemption levels $\pi_1 > \pi_2 > \dots > \pi_n$:
  $$B_k = \max\left(\{0\} \cup \{L_{j,r} \mid j > k \text{ and } \lceil r \rceil \ge \pi_k\}\right), \qquad \forall k \in \{1,\dots,n\}:\; \sum_{i=1}^{k} \frac{C_i}{D_i} + \frac{B_k}{D_k} \le 1$$

### Phase 4: Scenarios, Overhead & Stretch Goal
1. **Scenario 1 — Mars Pathfinder (`scenarios/pathfinder.yaml`):**
   * $L$ (`phase=0`) locks resource $S$ at `offset=0`.
   * $H$ (`phase=1`) arrives at $t = 1$, requests $S$, and blocks.
   * $M$ (`phase=2`) arrives at $t = 2$, does not use $S$, preempts $L$, and causes $H$ to miss its deadline (`INVERSION_MISS`) under `NONE`.
   * Re-run under `PIP` and `ICPP`/`SRP` to confirm `inversion == 0` and $H$ meets its deadline.
2. **Scenario 2 — Overload (`scenarios/overload.yaml`, $U > 1$):**
   * Execute the same overloaded task set under RM and EDF with `continue` post-miss policy.
   * Compare per-task miss ratios to contrast RM's controlled degradation (only low-priority tasks miss) against EDF's domino effect (cascading misses across all tasks).
3. **Scenario 3 — Overhead Modeling (`scenarios/overhead.yaml`):**
   * Parameterize context-switch and priority-boost costs ($\varepsilon = 0$ vs. $\varepsilon = 1$ tick).
   * Track total context switches, lock operations, priority changes, and `overhead` ticks to produce a quantitative overhead comparison.
4. **Stretch Goal — Bounded Execution-Time Jitter & Overrun Sweep (`scenarios/jitter.yaml`):**
   * Sample actual job execution time $C_{\text{actual}} \in [\text{BCET}, \text{WCET}]$ using a fixed random seed (enforcing lock safety if $C_{\text{actual}} < \text{WCET}$).
   * Include an overrun sweep ($C_{\text{actual}} > \text{WCET}$) to demonstrate where the admission-control contract breaks (`OVERRUN_OR_OVERHEAD_MISS`).
   * Plot **Deadline-Miss Ratio vs. Jitter Amplitude** for each scheduler/protocol combination.

### Phase 5: Validation & POSIX Cross-Check
1. **Property-Based Soundness Tests (`tests/test_soundness.py`):**
   * Generate **1,000+ random task sets** covering **both harmonic AND non-harmonic small-LCM periods** (e.g., drawn from `{4, 5, 6, 8, 10, 12, 15, 20, 24, 30, 60}` with $\text{LCM} \le 240$), both implicit ($D_i = T_i$) and constrained ($D_i < T_i$) deadlines, and **both synchronous (`phase=0`) and randomized non-zero phases** (so $L$ actually holds locks when $H$ arrives).
   * Assert that **every task set accepted by Admission Control has 0 deadline misses** over `max(phase) + hyperperiod` without jitter/overhead.
   * Assert that observed response time never exceeds $R_i$ (fixed-priority) and `direct_blocking + ceiling_or_push_through_blocking <= B_i`.
   * Assert that `inversion == 0` under `PIP` and `ICPP`/`SRP`.
2. **POSIX Cross-Check (`posix/pi_demo.c` + `posix/Makefile`):**
   * Implement a ~150-line C program using `pthread` with `SCHED_FIFO`, **`PTHREAD_EXPLICIT_SCHED`**, and single-core pinning via `sched_setaffinity`.
   * Run the 3-thread Pathfinder scenario with `PTHREAD_PRIO_NONE`, `PTHREAD_PRIO_INHERIT`, and `PTHREAD_PRIO_PROTECT` (setting `pthread_mutexattr_setprioceiling(&attr, prio_H)` explicitly).
   * Use calibrated `CLOCK_MONOTONIC` CPU-bound busy-wait loops inside $L$ and $M$ (never `sleep()` inside critical sections) and `clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, ...)` for deterministic release timing, keeping total CPU run time $< 500\text{ ms}$ to stay below Linux's `sched_rt_runtime_us` RT throttling.
   * Check `EPERM` explicitly (requires `sudo` or `CAP_SYS_NICE`) — never silently fall back to `SCHED_OTHER`. Measure $H$'s response time and document VM hypervisor jitter limitations.

---

## 3. Deliverables & Defense Checklist

* **One-Command Reproduction:** Running `./reproduce.sh` (LF line endings, `MPLBACKEND=Agg`) on a fresh Ubuntu VM completes in under 30 seconds and deterministically regenerates all CSVs, Gantt charts, and passing `pytest` results (plus runs `posix/pi_demo` when `CAP_SYS_NICE`/`sudo` is available, or reports the exact `sudo` command cleanly otherwise).
* **Clear Visuals:** Gantt charts color-code `running`, `direct_blocking` / `ceiling_or_push_through_blocking`, `inversion`, and `overhead` so priority inversion and protocol overhead are immediately visible.
* **Oral Defense Readiness:**
  * Derive the RTA equation ($R_i$) and explain why it is exact compared to the Liu & Layland utilization bound.
  * Explain why `PIP` allows deadlocks (with nested locks) and chain-blocking / push-through blocking, while `ICPP`/Baker's `SRP` prevents deadlocks and bounds blocking to a single critical section across both Fixed-Priority and EDF.
  * Explain what the discrete simulator abstracts away compared to real hardware (CPU caches, hardware interrupts, Linux RT throttling, VM hypervisor scheduling jitter).
