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

## How to Handle a Task (Group Workflow)

Every teammate **must** follow this workflow for all issues (`#1`–`#10`) so nobody duplicates work or causes merge conflicts on `main`:

1. **Choose an Issue on GitHub:**
   - Open the [Issues](https://github.com/P1e2003/rtos-controller/issues) tab, pick an unassigned issue whose dependencies are merged (check the `wave-1` / `wave-2` / `wave-3` labels), and assign yourself (or comment that you're taking it).
2. **Mark Your Task on Notion:**
   - Open the team's [**RTOS Controller Notion Board**](https://app.notion.com/p/4219b538618181a28969dc15f5526875), set yourself in **Owner** on that issue card, and move its status to **In Progress** so the whole group knows which task you are handling.
3. **Create a Feature Branch from Latest `main`:**
   - Never commit or push directly to `main`. Always branch from an up-to-date `main` using `feature/<issue-name>`:
     ```bash
     git checkout main
     git pull origin main
     git checkout -b feature/<issue-name>
     ```
     Examples: `feature/models-and-loader`, `feature/posix-pi-demo`, `feature/admission-control`.
4. **Finish the Task & Run Checks Locally:**
   - Stick to the files owned by your issue and verify everything passes before pushing:
     ```bash
     ruff check sim/ plots/ tests/
     pytest -v
     ```
     *(For `#6 [US-2.4]`, also verify `make -C posix` builds with zero warnings on Linux.)*
5. **Push Your Branch & Open a Pull Request:**
   - Push only your feature branch and open a PR targeting `main` with `Closes #<issue_number>` in the description:
     ```bash
     git add <your-files>
     git commit -m "feat(<module>): <short summary>"
     git push -u origin feature/<issue-name>
     ```
   - Update your task status on **Notion** to **In Review** (and paste the PR link).
6. **Wait for Review & Merge:**
   - Wait for **@P1e2003** to review and merge your PR into `main` (do not self-merge). Once merged, update your task on **Notion** to **Done** and pull the updated `main` before starting your next issue.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full 3-wave parallel dependency order and Windows→Ubuntu LF line-ending rules.
