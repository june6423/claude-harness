# Research Harness for Claude Code

A multi-agent harness that runs ML research experiments with Claude Code. Three long-lived Claude Code sessions, called **towers**, divide the work: one plans the research with the researcher, one implements and runs it on remote GPU servers, and one independently checks the work before and after each expensive run. The towers talk to each other through cross-session messages, but every decision that carries authority goes through a small, deterministic Python CLI that validates it, hashes it, and stores it as an immutable record.

The goal is reliability, not autonomy for its own sake. An agent can propose, implement, and review, but it cannot approve its own plan, start a full training run without an independent check, or accept its own result.

- Python standard library only. No database, no external service, no package to install.
- One data root per research project, with its own Git history.
- Every model call runs on a model the researcher chooses for each tower (`config.json` `towers.models`).

## Architecture

```text
                         researcher
              (approves plans, decides research questions)
                 │ Korean           │ Korean            │ Korean
                 ▼                  ▼                   ▼
            ┌─────────┐  message ┌─────────┐  message ┌─────────┐
            │   DT    │◀────────▶│   ET    │◀────────▶│   VT    │
            │ discuss │          │ engineer│          │ validate│
            └────┬────┘          └────┬────┘          └────┬────┘
   handoff,      │      task, report, │      verdict       │
   directive,    │      cancel        │                    │
   grant         ▼                    ▼                    ▼
            ┌───────────────────────────────────────────────────┐
            │ bin/harness (CLI)                                 │
            │ validate schema and authority → assign ID →       │
            │ SHA-256 → store immutable record → append journal │
            └─────────────────────────┬─────────────────────────┘
                                      │ task records
                                      ▼
            ┌───────────────────────────────────────────────────┐
            │ executor daemon (systemd user service)            │
            │ re-check task → preflight → run over SSH          │
            │ (docker exec or direct) → poll → result record    │
            │ also: keep towers alive, rotate full sessions     │
            └─────────────────────────┬─────────────────────────┘
                                      ▼
                            remote GPU servers
```

### Towers

| Tower | Rules | Role | Can | Cannot |
|---|---|---|---|---|
| **DT** — Discussion Tower | `towers/dt.md` | Research reasoning with the researcher: hypotheses, experiment design, interpretation | Publish handoffs, directives, and grants; read the workspace and records; keep `PROJECT_STATE.md` | Change code; publish an APPROVED plan without the researcher's own words |
| **ET** — Engineering Tower | `towers/et.md` | Implementation, tests, remote runs, monitoring | Change and commit code, publish tasks and reports, reach configured servers over SSH | Push except through `harness git`; start a FULL run without a VALID verdict from VT |
| **VT** — Validation Tower | `towers/vt.md` | Independent review of ET's code, runs, and claims | Read everything, run local checks, publish verdicts | Change code or records of other towers |
| **DT2** (optional) | `towers/dt2.md` | A discussion-only copy of DT for a second line of thought | Discuss and write drafts | Publish, send messages, or change state |

Each tower is a background Claude Code session (`claude --bg --agent <name> --name <name>`) with its own agent definition (`.claude/agents/`), rules file (`towers/`), and settings (`settings/<tower>.json`). Towers can also be reached from a phone through Claude Code Remote Control.

### Records

All shared state is a set of records in the data root. A record is never edited; a change is a new record or a new revision.

| Record | Written by | Purpose |
|---|---|---|
| `handoff` | DT | The experiment plan: question, design, gates for VT, budget, and the authorization (`IMPLEMENT_ONLY`, `VALIDATION_RUN`, `FULL_RUN`). APPROVED only with the researcher's verbatim approval text. |
| `task` | ET | One unit of remote work (`run`, `inspect`, `gpu-status`, ...) with an exact commit, an entrypoint, and the required outputs. |
| `result` | executor | The outcome of a task: exit code, required outputs with size and SHA-256, logs, and fetched artifacts. |
| `report` | ET | What ET did and what it claims, with references to tasks and results. |
| `verdict` | VT | VALID or NOT_VALID for each gate of the handoff, `PRE_RUN` (before a FULL run) or `POST_RUN` (before DT accepts a result). |
| `directive` | DT | An instruction outside a handoff: from the researcher (checked against the transcript) or an engineering decision of DT. |
| `grant` / `revoke` | DT | A standing permission for ET or VT inside the scope of a handoff, and its removal. |
| `cancel` | DT, ET, researcher | Stop a running task. The executor kills the process group and writes a CANCELLED result. |

A reference to a record is `ID@sha256:<hex>`, so a message or a later record always points to exactly one version.

## How the towers interact

A typical experiment:

1. **Plan.** DT and the researcher design the experiment. DT writes a handoff draft and the researcher approves it in their own words. The `dt_guard` hook refuses an APPROVED handoff whose approval text does not occur verbatim in a message that the researcher typed. DT sends `HANDOFF_READY` to ET.
2. **Implement.** ET implements and tests the change, commits it to the branch that the handoff names, and pushes through `bin/harness git`.
3. **Validate cheaply.** ET publishes `VALIDATION` tasks, including a *pilot*: the same script at the same commit with `PILOT=1`, which must go through every stage of the full run. These do not need VT.
4. **Pre-run review.** ET publishes a report and sends `REVIEW_REQUEST` to VT. VT checks the code at the commit against each `PRE_RUN` gate and publishes a verdict.
5. **Full run.** Only after every `PRE_RUN` gate is VALID can ET publish the `FULL` task. The executor checks the verdict and the pilot again before it launches anything.
6. **Post-run review.** ET reports the result. VT checks the outputs and claims against the `POST_RUN` gates.
7. **Accept.** DT accepts a result only with a VALID `POST_RUN` verdict, reports it to the researcher in Korean, and updates `PROJECT_STATE.md`.

Messages carry only a type (`HANDOFF_READY`, `REVIEW_REQUEST`, `VERDICT_READY`, `QUESTION`, `ANSWER`, `STATUS`, ...) and record references, never the content of a record. A hook writes every sent and received message to the message ledger. There is no central controller: each tower acts on the records and messages it receives.

## What the design takes care of

### Authority lives in records, not in messages
A tower ignores any claim of authority in message text ("the researcher said ..."). Authority exists only as a record that the harness checked: an APPROVED handoff with verbatim researcher text, a directive, or a grant. Deterministic code makes each authorization decision; an agent never decides alone whether it is allowed to do something.

### Independent validation gates
VT is a separate session with its own context and no write access to code. A FULL run needs a VALID `PRE_RUN` verdict and a passing pilot; a result needs a VALID `POST_RUN` verdict. After a code fix, VT binds the new commit in a new verdict under the same handoff.

### Deterministic remote execution
No agent runs on a GPU server. A `run` task executes a declared entrypoint at an exact commit:

- **Preflight** on the host and in the container: clean checkout, the commit is on `origin/<branch>`, detached checkout, executable entrypoint, writable output root.
- **Launch** with `setsid` inside the container (`docker exec`) or directly on the host (for rented machines without Docker access). A wrapper writes the PID, logs, and exit code.
- **Supervise.** Poll, copy logs back, stop the process group at the timeout or on a cancel record.
- **Finish.** The outcome is the exit code plus the presence, size, and SHA-256 of each required output.
- **Recover.** A live receipt lets a new worker reattach to a running job instead of launching it twice.

### Budgets
Each handoff revision has limits on run tasks, FULL tasks, and recovery attempts (`config.json` `limits`). When a budget is used up, the harness refuses the next task and only the researcher can approve more.

### A local safety boundary
Every tower runs in Claude Code `dontAsk` mode inside a Bash sandbox (bubblewrap, with an AppArmor profile in `ops/apparmor/`). A sandboxed command needs no allow rule. Each form of `git push` is denied; network Git goes only through `bin/harness git`, which accepts only the configured origin and refuses force pushes. Only ET may reach the configured servers with `ssh`, `scp`, and `rsync`.

### Long-running sessions
Towers run for weeks, so their context must stay small and their memory must survive a restart:

- **Rotation.** A Stop hook measures the context after each turn. Above `context_rotate_tokens` (300k), the tower writes a handover and the executor daemon starts a fresh session that reads it.
- **Layered record.** `state/PROJECT_STATE.md` is the short research narrative (five fixed sections, 9,000 characters). `worklog/<tower>-summary.md` is the handover. `worklog/<tower>.md` is the current session. `archive/` holds the full history of each closed experiment.
- **Search.** `bin/harness archive search` is a stdlib BM25 search over the archive and the record bodies, with Korean support.
- **Liveness.** The daemon revives a tower whose process stopped, and keeps the Remote Control link.
- **Tidiness.** Documents for the researcher go to `archive/reports/` (in Git, date-first names). At each handover the harness deletes old scratch drafts that no kept file names, keeps virtual environments, and moves never-published record drafts to `archive/unpublished/`.

### Clear writing for agents and for people
Machine-read documents (handoffs, reports, verdicts, tower rules) and cross-session messages use English in ASD-STE100 Simplified Technical English (the `harness-writing` and `asd-ste100` skills), so another agent cannot misread them. Everything for the researcher is in Korean, with times in KST, in the order: the number, what it means, and the limit of the evidence.

## Repository layout

```text
bin/harness              CLI entry point
harness/                 record store, schemas, executor, tower control (stdlib Python)
hooks/                   Claude Code hooks: dt_guard (approval checks), context_guard (rotation),
                         message_ledger (message log)
towers/                  tower rules (dt, et, vt, dt2)
.claude/agents/          tower agent definitions
.claude/skills/          harness-writing, asd-ste100, harness-archive
settings/                per-tower Claude Code settings: permissions, sandbox, hooks (templates)
ops/                     systemd unit for the executor, AppArmor profile, token usage report
docs/                    DESIGN.md (decisions and reasons), SETUP.md, OPERATIONS.md, config example
tests/                   unit and integration tests
```

## Data root layout

The harness repository holds no research data. Each project has a data root, for example `~/harness-data/<project>/`:

```text
config.json            servers, workspaces, repositories, tower models, limits
records/<kind>/        immutable records
artifacts/<task-id>/   small outputs fetched from the servers
state/PROJECT_STATE.md current research narrative
worklog/               tower summaries and session logs
archive/               closed experiments, session logs, reports for the researcher
journal.jsonl          append-only event log
messages.jsonl         cross-session message ledger
workspace/<repo>/      Git clones of the research code (not in the data root Git)
runtime/               executor queue, live receipts, logs, tower settings, drafts (not in Git)
```

## Getting started

Requirements: Linux, Python 3.10 or later, Claude Code CLI (logged in), bubblewrap and socat for the sandbox, and SSH access to the GPU servers.

```bash
bin/harness init ~/harness-data/<project>          # create a data root and point this repository to it
# edit <data-root>/config.json: servers, workspaces, tower models (see docs/config.example.json)
mkdir -p ~/.config/systemd/user                    # executor daemon as a user service;
cp ops/systemd/harness-executor.service ~/.config/systemd/user/   # set its log path to your data root
systemctl --user enable --now harness-executor
bin/harness tower start dt                         # start each tower as a background session
bin/harness tower start et
bin/harness tower start vt
```

Then talk to DT. The full installation guide is `docs/SETUP.md`, and the researcher's operating guide is `docs/OPERATIONS.md` (both in Korean).

## Command reference

| Command | Purpose |
|---|---|
| `init`, `root` | Create a data root; print its path |
| `publish <kind> <draft> --actor <A> [--dry-run]` | Validate a draft and store it as a record (prints `ID@sha256:<hex>`) |
| `handoff-draft <EXP> --out <file>` | Start the next handoff revision from the latest one |
| `status`, `list`, `show` | Overview of experiments; list records; print a record, its front matter, or one section |
| `verify` | Check hashes, journal, schemas, and references |
| `message`, `messages` | Format a cross-session message; print the ledger |
| `executor watch \| status` | Run the executor daemon; show its state |
| `wait <task-id>` | Wait for a result and print it |
| `cancel <task-id> --reason ... --actor ...` | Stop a running task |
| `grants` | List active standing grants |
| `git <repo> <verb> ...` | Network Git for ET, origin only |
| `handover <tower> <draft>` | Write a handover and request a fresh session |
| `drafts prune <tower> [--dry-run]` | Clean old scratch drafts now |
| `archive search <words>` | Search the archive and the record bodies |
| `tower start \| restart \| stop \| status \| fork` | Manage tower sessions |

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## Documentation

- `docs/DESIGN.md` — every design decision (D1 to D29) with its date and reason.
- `docs/SETUP.md` — installation (Korean).
- `docs/OPERATIONS.md` — operating guide for the researcher (Korean).
- `THIRD_PARTY.md` — vendored and adapted material and its licenses.
