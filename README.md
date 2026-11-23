# RTOS Controller — Real-Time Priority Inversion & Deadline-Protection Simulator

Deterministic, tick-based real-time scheduling and synchronization simulator in **Python 3.10+**, paired with a single-core **C/POSIX (`SCHED_FIFO`)** cross-check demo for Ubuntu Linux.

## What This Project Demonstrates

- **Schedulers:** Rate-Monotonic (`RM`), Deadline-Monotonic (`DM`), and Earliest-Deadline-First (`EDF`).
- **Synchronization Protocols:** `NONE` (unprotected mutex), `PIP` (Priority Inheritance Protocol), and unified `ICPP` / Baker's `SRP` (Stack Resource Policy for both Fixed-Priority and EDF).
- **Pre-Flight Admission Control:** Iterative Response Time Analysis (`RTA`) with push-through blocking for Fixed-Priority, and Baker's `SRP` density test for `EDF`.
- **Root-Cause Attribution:** Exhaustive 6-category per-tick attribution (`running`, `overhead`, `interference`, `direct_blocking`, `ceiling_or_push_through_blocking`, `inversion`) and 5-way automated miss-cause classification.
- **Validation:** 1,000+ randomized harmonic & non-harmonic small-LCM property-based soundness checks + real Linux `pthread` single-core cross-check (`posix/pi_demo.c`).

Full specification and mathematical formulas live in [`docs/implementation_plan.md`](docs/implementation_plan.md).

---

## Project Layout

```text
rtos-controller/
├── docs/implementation_plan.md  # Canonical spec & math reference
├── sim/                         # Python tick simulator, protocols, admission & attribution
├── scenarios/                   # YAML scenarios (pathfinder, overload, overhead, jitter)
├── plots/                       # Headless Matplotlib Gantt & jitter/miss-ratio plots
├── tests/                       # pytest unit tests & 1,000+ task-set soundness sweep
├── posix/                       # Single-core SCHED_FIFO C/POSIX cross-check demo
├── docs/ci.workflow.yml         # Ready-to-enable GitHub Actions CI template (copy to .github/workflows/ci.yml)
├── CONTRIBUTING.md              # Group branch, PR, and issue ownership workflow
├── requirements.txt             # Pinned Python dependencies
└── reproduce.sh                 # One-command Ubuntu reproduction pipeline
```

---

## Quickstart (Local Dev & Ubuntu VM)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -v
ruff check sim/ plots/ tests/
```

Once all issues are merged, full reproduction on Ubuntu runs with:

```bash
chmod +x reproduce.sh
./reproduce.sh
```

---

## Team Workflow & Issue Hand-Out

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and the **GitHub Issues** tab (`[US-1.1]` – `[US-3.4]`). Issues are structured in **3 Waves** with disjoint file ownership so 3–4 teammates can work in parallel with minimal merge conflicts.
