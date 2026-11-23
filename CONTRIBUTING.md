# Contributing & Group Workflow

This repository is structured so a **3–4 member team** can work in parallel across cleanly separated modules with minimal merge conflicts.

## 1. Branching & Pull Requests

1. **Never push directly to `main`.** Always pull the latest `main` before starting an issue:
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feature/<short-issue-slug>
   ```
   Example branch names: `feature/models-and-loader`, `feature/posix-pi-demo`, `feature/admission-control`.
2. **One issue = one focused PR.** Reference your issue in the PR description using `Closes #<issue_number>`.
3. **Stick to the files owned by your issue.** If you think a shared interface in `sim/models.py` or `sim/engine.py` needs to change, coordinate in the issue comments first so parallel branches do not break.
4. **Keep CI green before requesting review:**
   ```bash
   ruff check sim/ plots/ tests/
   pytest -v
   ```

## 2. Parallel Wave Order (3–4 Members)

- **Wave 1 (Start Day 1):**
  - `#1 [US-1.1]` Core Models & YAML Loader *(unblocks `#2` and `#5`)*
  - `#6 [US-2.4]` C/POSIX `pi_demo.c` *(100% independent of Python — can start Day 1!)*
  - `#2 [US-1.2]` Tick Engine & Schedulers *(starts right after `#1`)*
- **Wave 2 (Run in Parallel after `#1` / `#2`):**
  - `#3 [US-2.1]` Protocols (`NONE`, `PIP`, `ICPP`/`SRP`)
  - `#4 [US-2.2]` Per-Tick Attribution & Miss-Cause Classifier
  - `#5 [US-2.3]` Admission Control (`RTA` + Baker's `EDF-SRP` test — only needs `#1`!)
- **Wave 3 (Integration, Experiments & Defense Artifacts):**
  - `#7 [US-3.1]` Core Scenarios (`pathfinder`, `overload`, `overhead`) & CSV Runner
  - `#8 [US-3.2]` Stretch Goal — Bounded Jitter & Overrun Sweep
  - `#9 [US-3.3]` Matplotlib Gantt Charts & Curves
  - `#10 [US-3.4]` 1,000+ Soundness Tests & Ubuntu `reproduce.sh`

## 3. Windows → Ubuntu Line-Ending Rule

`.gitattributes` enforces **LF (`\n`)** line endings for `.sh`, `.py`, `.c`, `.h`, `Makefile`, and `.yaml` files so scripts authored on Windows run cleanly on Ubuntu Linux VMs without `\r` errors.
