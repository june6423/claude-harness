# Harness Design — Phase 0

Status: APPROVED by the researcher on 2026-10-08. Phase 0 is complete (section 8.1). Phase 1 is complete (section 10.5). Phase 2 is complete (section 11.6). Phase 4 is complete (section 14.2).

## 1. Decisions

| ID | Decision | Date |
|---|---|---|
| D1 | Each tower (DT, ET, VT) runs as an independent Claude Code session. | 2026-10-08 |
| D2 | Towers communicate through cross-session messages. | 2026-10-08 |
| D3 | A tower starts with `claude --bg --agent <name> --name <name>`. | 2026-10-08 |
| D4 | A remote `run` executes a declared entrypoint without an agent. A remote agent does only `inspect` and environment diagnosis. | 2026-10-08 |
| D5 | A FULL run needs a VALID pre-run verdict from VT. DT accepts a result only with a VALID post-run verdict from VT. A VALIDATION run does not need VT. | 2026-10-08 |
| D6 | Superseded by D13. | 2026-10-08 |
| D7 | Machine-read documents and cross-session messages use English STE Strict. Replies to the researcher use Korean. | 2026-10-08 |
| D8 | The harness does not enforce the writing rules with hooks. | 2026-10-08 |
| D9 | The data root of a project is `~/harness-data/<project>/`. It has its own Git repository. `runtime/` stays out of Git. | 2026-10-08 |
| D10 | A tower writes records only through `harness publish`. A tower writes a draft in its scratch directory first. | 2026-10-08 |
| D11 | Each `run` and `allocate-run` task declares a non-empty `required_outputs` list. | 2026-10-08 |
| D12 | The front matter of a Markdown record is one JSON object. The harness uses the Python standard library only. | 2026-10-08 |
| D13 | Each workspace repository is a clone in `<data-root>/workspace/`. The data root Git repository ignores `workspace/`. The copies in `research_control/workspace/` stay unchanged. | 2026-10-08 |

## 2. Principles

1. Deterministic code makes each authorization decision. An agent never makes an authorization decision alone.
2. Each layer works without the layers above it. The researcher can operate each layer by hand.
3. A tower never edits a published record. A change makes a new record with a new revision.
4. Each reference to a record contains the record ID and the SHA-256 of its content.
5. A message carries references to records. A message does not carry the content of a record.

## 3. Components

```text
 researcher ── Korean ──┬──────────────┬──────────────┐
                        ▼              ▼              ▼
                     ┌────┐  message ┌────┐  message ┌────┐
                     │ DT │◀────────▶│ ET │◀────────▶│ VT │
                     └─┬──┘          └─┬──┘          └─┬──┘
                       │ publish       │ publish        │ publish
                       ▼               ▼                ▼
               ┌──────────────────────────────────────────────┐
               │ harness CLI: validate, assign ID, hash,       │
               │ store as immutable record, append journal     │
               └──────────────────────┬───────────────────────┘
                                      │ task records
                                      ▼
               ┌──────────────────────────────────────────────┐
               │ executor (Phase 1): authorize, preflight,     │
               │ run over SSH and docker, write result record  │
               └──────────────────────────────────────────────┘
```

| Component | Kind | Phase |
|---|---|---|
| Record store and schemas | Python library, stdlib only | 0 |
| `harness` CLI | Python command | 0 |
| Journal | Append-only JSONL file | 0 |
| Message ledger | Hook command, no model tokens | 0 |
| Executor | Python daemon | 1 |
| Tower agent definitions | `.claude/agents/*.md` | 0 |
| Tower settings: inbound policy and ledger hook | `settings/<tower>.json` | 0 |
| Tower settings: permission rules and sandbox | `settings/<tower>.json` | 2 |

## 4. Directory layout

### 4.1 Harness repository

```text
harness/
├── .claude/agents/{dt,et,vt}.md     # tower frontmatter only (section 7)
├── towers/{dt,et,vt}.md             # tower rules, appended at launch
├── .claude/skills/                  # asd-ste100, harness-writing
├── harness/                         # Python package
│   ├── records.py                   # schemas, validation, IDs, hashes
│   ├── store.py                     # publish, read, list
│   ├── journal.py
│   └── cli.py
├── hooks/message_ledger.py
├── settings/{dt,et,vt}.json         # section 7
├── tests/
└── docs/DESIGN.md
```

### 4.2 Data root (open decision O1)

One data root holds the records of one research project. The harness repository does not contain research records.

```text
<data-root>/
├── config.json                      # workspaces, servers, repositories, runtimes
├── records/
│   ├── handoff/HANDOFF-<exp>-<rev>.md
│   ├── report/REPORT-<exp>-<n>.md
│   ├── verdict/VERDICT-<exp>-<n>.md
│   ├── task/TASK-<exp>-<n>.json
│   └── result/RESULT-<task-id>.json
├── artifacts/<task-id>/             # fetched small outputs (section 13)
├── state/PROJECT_STATE.md           # DT keeps this file current
├── workspace/<repo>/                # workspace clones (D13), not in the data root Git
├── journal.jsonl
├── messages.jsonl                   # message ledger
└── runtime/                         # executor queue, live receipts, logs
```

## 5. Records

### 5.1 Common rules

1. The CLI assigns the record ID. A tower does not choose an ID.
2. The CLI computes the SHA-256 of the stored bytes. A reference has the form `<ID>@sha256:<hex>`.
3. A stored record is read-only. The CLI refuses an overwrite.
4. A Markdown record starts with a front matter block: a line `---`, one JSON object, and a line `---`. The CLI validates the front matter and the required body sections. The rest of the body is free STE text.
5. JSON records have a fixed schema. The CLI refuses unknown fields.

### 5.2 Handoff (DT writes)

```text
---
{
  "kind": "handoff",
  "experiment_id": "VLM-OPSD-TRANSPLANT-001",
  "revision": 3,
  "predecessor": "HANDOFF-...@sha256:... | NONE",
  "status": "DRAFT | APPROVED",
  "authorization": "IMPLEMENT_ONLY | VALIDATION_RUN | FULL_RUN",
  "approval": "researcher statement, quoted verbatim | NONE",
  "gates": [{"id": "G1", "phase": "PRE_RUN | POST_RUN", "check": "one STE sentence that VT can check"}],
  "full_run_condition": "NONE | ALL_PRE_RUN_GATES_VALID"
}
---
```

Rules that the CLI checks:

1. `revision` is the previous revision plus 1. `predecessor` is the reference of the previous revision.
2. `APPROVED` needs an `approval` quotation. The quotation does not prove approval. It makes the approval claim auditable.
3. An APPROVED handoff with VALIDATION_RUN or FULL_RUN needs at least one POST_RUN gate.
4. FULL_RUN or `ALL_PRE_RUN_GATES_VALID` needs at least one PRE_RUN gate.

### 5.3 Task (ET writes)

```json
{
  "kind": "task",
  "experiment_id": "...",
  "handoff": "HANDOFF-...@sha256:...",
  "source": "HANDOFF | USER",
  "user_request": "verbatim text | NONE",
  "mode": "gpu-status | inspect | run | allocate-run",
  "execution_class": "NONE | VALIDATION | FULL",
  "server": "a6000",
  "repo": "faculty_opsd_transplant",
  "branch": "exp/...",
  "commit": "<40 hex>",
  "runtime": {"container": "NAME"} ,
  "entrypoint": ["scripts/run_main.sh", "--shard", "0"],
  "env": {"KEY": "VALUE"},
  "output_root": "/abs/server/path",
  "required_outputs": ["run/scores.jsonl", "run/metrics.json"],
  "gpu": {"count": 2, "policy": "FIXED | BEST_EFFORT_JIT", "constraints": "..."},
  "timeout_seconds": 172800,
  "pre_run_verdict": "VERDICT-...@sha256:... | NONE",
  "lineage": {"parent": "NONE", "recovery_of": "NONE", "attempt": 0}
}
```

Rules for `run` and `allocate-run`:

1. `entrypoint[0]` is a script path inside the repository. The file must have mode 100755 at `commit`.
2. The entrypoint runs inside the container. The executor does not run host Python. With runtime `direct`, the server itself is the container (for example a vast.ai CUDA instance), and the entrypoint runs on it without `docker exec`.
3. A FULL task needs `pre_run_verdict`. The executor checks that the verdict is VALID, has kind PRE_RUN, and names the same commit and handoff.

`docker-setup` is out of the Phase 1 scope. Container setup stays a manual researcher action until a later phase.

### 5.4 Result (executor writes)

```json
{
  "kind": "result",
  "task": "TASK-...@sha256:...",
  "transport": "OK | ERROR",
  "preflight": "PASS | FAIL",
  "exit_code": 0,
  "outcome": "PASS | FAIL | REJECTED | INTERRUPTED | CANCELLED",
  "cancel": "CANCEL-TASK-...@sha256:... (only when outcome is CANCELLED)",
  "git": {"requested": "<sha>", "actual": "<sha>", "tracked_changes": false},
  "runtime": {"container": "...", "image": "sha256:...", "gpu_uuids": ["..."]},
  "outputs": [{"path": "...", "bytes": 0, "sha256": "..."}],
  "missing_outputs": [],
  "started_at": "...", "ended_at": "...",
  "logs": {"stdout": "runtime/logs/...", "stderr": "runtime/logs/..."}
}
```

`outcome` is PASS only when all four conditions are true:

1. `transport` is OK.
2. `preflight` is PASS.
3. `exit_code` is 0.
4. `missing_outputs` is empty.

The executor does not look at commands that an agent ran during exploration. This rule replaces the old outcome rule.

When the executor stopped the run because of a cancel record (section 22), `outcome` is CANCELLED and `cancel` gives the reference of that record.

### 5.5 Report (ET writes)

Front matter fields: `kind: report`, `experiment_id`, `handoff`, `commits`, `tasks`, `results`.
Required body sections:

1. `Changes` — the implementation and the reason for each implementation choice (ET rule 6).
2. `Evidence` — references to results and tests.
3. `Proposals` — simpler approaches or open choices (ET rule 9). Write NONE if there are none.
4. `Deviations` — differences from the handoff. Write NONE if there are none.

### 5.6 Verdict (VT writes)

```text
---
{
  "kind": "verdict",
  "phase": "PRE_RUN | POST_RUN",
  "handoff": "HANDOFF-...@sha256:...",
  "subject": {"commit": "<40 hex> | NONE", "report": "REPORT-...@sha256:... | NONE", "results": ["RESULT-...@sha256:..."]},
  "gates": [{"id": "G1", "verdict": "VALID | PARTIAL | INVALID | NOT_VERIFIABLE", "evidence": ["file:line, command, or record reference"]}]
}
---
```

The CLI computes `overall`. It is VALID only when each gate of that phase in the handoff has the verdict VALID.

### 5.7 Message envelope

A cross-session message has a header and an STE body.

```text
[HMSG v1]
type: HANDOFF_READY | REPORT_READY | REVIEW_REQUEST | VERDICT_READY | QUESTION | ANSWER | STATUS
refs: <ID>@sha256:<hex>, ...
---
<STE body>
```

The message ledger hook records each sent and each received message in `messages.jsonl`. The hook does not block a message. It uses no model tokens.

### 5.8 Journal event

The CLI and the executor append one JSON line for each state change:
`{"at": ..., "actor": "DT|ET|VT|CLI|EXECUTOR|RESEARCHER", "event": "...", "refs": [...]}`.
A tower does not write the journal directly.

## 6. Main flow

1. DT publishes an APPROVED handoff. DT sends `HANDOFF_READY` to ET.
2. ET implements, tests, commits, and pushes.
3. ET publishes VALIDATION tasks. The executor runs them without VT.
4. ET publishes a report. ET sends `REVIEW_REQUEST` to VT with the report and the planned commit.
5. VT publishes a PRE_RUN verdict. VT sends `VERDICT_READY` to ET and DT.
6. If the verdict is VALID, ET publishes the FULL task with the verdict reference.
7. The executor checks the verdict. Then it runs the task and writes the result.
8. ET publishes a report. ET sends `REVIEW_REQUEST` to VT.
9. VT publishes a POST_RUN verdict. VT sends `VERDICT_READY` to ET and DT.
10. DT accepts the result only if the POST_RUN verdict is VALID. DT reports to the researcher in Korean.

There is no central controller in Phases 0 to 3. The researcher starts the towers. Budgets and automatic loops belong to Phase 5.

## 7. Tower launch

### 7.1 Facts

| Fact | Basis |
|---|---|
| A non-empty agent body replaces the default Claude Code system prompt. The default tool guidance and safety text are then absent. | Documentation (sub-agents) |
| An agent with an empty body keeps the default system prompt. | Documentation (sub-agents) |
| An empty-body agent with `--bg` and `--append-system-prompt-file` received the appended text. It saw the project skills. Its tools were limited to the `tools` field. | Test on 2026-10-08, Claude Code 2.1.294 |
| Frontmatter hooks run in a main session together with settings hooks. | Documentation (sub-agents) |
| Path deny rules block file tools and recognized shell commands. They do not block a script that opens the file itself. The Bash sandbox gives OS-level enforcement. | Documentation (permissions, sandboxing) |

### 7.2 Launch method

1. `.claude/agents/<tower>.md` has frontmatter only: `name`, `description`, `model`, `effort`, `tools` or `disallowedTools`, and `permissionMode`. The body is empty.
2. `towers/<tower>.md` keeps the tower rules. The launch appends the file to the default system prompt.
3. `settings/<tower>.json` holds `crossSessionInbound`, permission rules, the sandbox settings, and the message ledger hook.

```bash
claude --bg --agent et --name et \
  --append-system-prompt-file towers/et.md \
  --settings settings/et.json
```

The command `harness tower start <tower>` wraps this command. After the launch, the command reads `claude agents --json` and confirms that the session runs.

### 7.3 Limits

1. A resumed conversation keeps the system prompt of its first request. A change to `towers/<tower>.md` takes effect in a new conversation or after compaction.
2. The workspace must be trusted before the first `--bg` launch.

## 8. Phase 0 scope

Phase 0 builds these items:

1. The package skeleton and the `harness` CLI with `publish`, `show`, `list`, and `verify` commands.
2. Schemas and validators for handoff, task, result, report, verdict, and message header.
3. Record IDs, SHA-256 references, the read-only store, and the journal with a file lock.
4. The message ledger hook.
5. Tower agent definitions with frontmatter only, and the `harness tower start` command.
6. Unit tests.

Exit criteria:

1. All unit tests pass.
2. The CLI accepts one handoff and one task converted from `research_control` records.
3. The CLI refuses each invalid fixture with a specific error.
4. Two test sessions exchange one message. The ledger has one sent entry and one received entry.

### 8.1 Phase 0 result (2026-10-08)

| Exit criterion | Result | Evidence |
|---|---|---|
| 1. All unit tests pass. | PASS | `python3 -m unittest discover -s tests -t .` ran 24 tests. |
| 2. The CLI accepts converted records. | PASS | `test_converted_handoff_verdict_and_task` publishes a handoff, a verdict, and a FULL task from `research_control` records. |
| 3. The CLI refuses invalid fixtures with a specific error. | PASS | Tests in `tests/test_store.py` check each error text. |
| 4. Two sessions exchange one message and the ledger records it. | PASS | Live test with the `et` and `vt` agents, settings, and Haiku: one `sent` entry and one `received` entry, both with a parsed header. |

## 10. Phase 1 — Executor

### 10.1 Server configuration

```json
{
  "executor": {"poll_seconds": 10},
  "ssh": {"connect_timeout_seconds": 15, "control_persist_seconds": 120},
  "servers": {
    "<server>": {
      "transport": "ssh | local",
      "ssh_host": "<ssh config host>",
      "agent_command": "claude",
      "repos": {
        "<repo key>": {
          "path": "<checkout path on the server host>",
          "containers": {"<container>": {"workdir": "<checkout path in the container>",
                                         "runtime": "docker | direct", "image": "<text for direct>"}}
        }
      }
    }
  }
}
```

The `local` transport runs commands on this machine. Tests use it.

Each server repository is a checkout that only the harness uses, such as `/home/vision/dongjun/harness/faculty-opsd`. The executor fetches and switches branches in it. Checkouts that people or older tools use are not in the configuration. The workspace entry gives the server repository keys and the output directory:

```json
"workspaces": {"<key>": {"path": "workspace/<repo>", "remote": "<url>", "default_branch": "main",
                         "server_repos": ["<repo key>"], "server_outputs": "<container path>"}}
```

### 10.2 Daemon and workers

1. `harness executor watch` claims each task that has no result and no claim. One daemon runs for one data root.
2. The daemon starts one detached worker for each claim. A dead worker gets a new worker.
3. One `run` task at a time uses a server and repository pair. The pair has one checkout.
4. The worker checks the task again with the same code as `harness publish`. A failed check gives a REJECTED result.

### 10.3 Run mode

1. Preflight on the host: no tracked changes, fetch the branch, the commit is on `origin/<branch>` (it need not be the tip), detached checkout of the exact commit, HEAD equals the commit, and `entrypoint[0]` has mode 100755 at the commit. Until 2026-10-19 the checkout went to the branch tip, so an older commit of a branch could not run again.
2. Preflight in the container: the container runs, the entrypoint is executable, and `output_root` is writable. The worker records the image and the GPU list. With runtime `direct`, the check is `cat /etc/hostname` and the NVIDIA driver version; the record gets `kind: direct`, the `image` text from `config.json`, the host, and the driver.
3. Launch: `docker exec -d` starts a wrapper with `setsid`. The wrapper writes `pid`, `stdout`, `stderr`, and `exit_code` in `<output_root>/.harness/<task-id>/`. With runtime `direct`: `cd <workdir> && { env K=V setsid nohup bash -c <wrapper> < /dev/null > /dev/null 2>&1 & }`. The braces are necessary: without them the background subshell keeps the SSH output open, and the launch waits for the run to end.
4. Poll: the worker copies new log bytes to `runtime/logs/` and checks the exit code. At the timeout, the worker stops the process group.
5. Finalize: the worker computes the size and SHA-256 of each required output. Then it publishes the result.
6. A new worker reads the receipt in `runtime/executor/live/`. If the run started, the worker reattaches and does not launch again.

### 10.4 Other modes

- `gpu-status`: `nvidia-smi` on the host.
- `inspect`: the server agent runs in plan mode without Edit and Write. The result message is its final text.
- `allocate-run`: not implemented. The executor gives a REJECTED result.

### 10.5 Phase 1 result (2026-10-08)

| Check | Result |
|---|---|
| Unit tests with the local transport and a fake docker: pass, non-zero exit, missing output, entrypoint not executable, commit mismatch, tracked changes, stopped container, timeout, unknown container, allocate-run, reattach, daemon slots, dead worker | 14 PASS |
| Real docker container on this machine: run mode | PASS with logs, output hash, image, and GPU list |
| Real docker: `output_root` not writable (user namespace remap) | Preflight FAIL with a specific message |
| gpu-status and inspect with the local transport | PASS |

### 10.6 Known limits

1. The executor records GPU use. It does not refuse a FIXED task when another process uses the GPU.
2. An SSH server needs `git`, `docker`, `bash`, `setsid`, `base64`, and `sha256sum`. No SSH server was tested.
3. A task needs a running container. The executor does not create containers. A `direct` runtime needs a running server only.
4. SSH connection sockets are in `/tmp/harness-<uid>/ssh/` (mode 700). A socket under a long data root path was over the Unix socket path limit (found 2026-10-08).

## 11. Phase 2 — Tower permissions and ET operation

### 11.1 Settings

1. `settings/<tower>.json` is a template with `{{DATA_ROOT}}` and `{{HARNESS}}`.
2. `harness tower start` writes the filled file to `<data-root>/runtime/settings/<tower>.json`. It sets `HARNESS_DATA_ROOT` in `env` and creates the draft directory of the tower.
3. Each tower uses `defaultMode: dontAsk`. A tool call that no allow rule covers is refused without a prompt.
4. `blockReadsOutsideWorkingDirectories` is true. The working directories are the harness repository and the data root.

### 11.2 Permission matrix

| Action | DT | ET | VT |
|---|---|---|---|
| Read records, handoffs, reports, verdicts | Yes | Yes | Yes |
| Read `workspace/` and `runtime/logs/` | No | Yes | Yes |
| Write own draft directory | Yes | Yes | Yes |
| Write `state/PROJECT_STATE.md` | Yes | No | No |
| Write `workspace/` | No | Yes | No |
| Write the harness repository | No | No | No |
| `harness publish` | handoff as DT | task and report as ET | verdict as VT |
| `harness executor`, `tower`, `init`, `--data-root` | No | No | No |
| Git | No | Yes, without force push, `reset --hard`, or `clean` | `git -C` read commands |
| Run a script file with `python3` | No | Yes | Yes |
| Web search and fetch | Yes | No | No |

### 11.3 Sandbox

The settings templates contain a Bash sandbox block. `harness tower start` keeps it only when `config.json` has `"towers": {"sandbox": true}`. The `faculty-opsd` data root enables it.

1. Ubuntu 24.04 blocks unprivileged user namespaces (`kernel.apparmor_restrict_unprivileged_userns=1`). The profile `ops/apparmor/bwrap` gives that permission to `/usr/bin/bwrap` only. The researcher installed it on 2026-10-08. The sysctl stays 1.
2. With `allowWrite` set, the sandbox mounts the working directory read-only. bubblewrap then fails when it must create a protected placeholder, such as `.gitconfig` or `.vscode`. `harness tower start` creates these placeholders in the harness repository first. Git ignores them.
3. `bin/harness` runs outside the sandbox (`excludedCommands`). The permission rules still apply to it.
4. `strictAllowlist` refuses each network host that is not in `allowedDomains`. Only ET has a host: `github.com`.

### 11.4 ET waiting

After ET publishes a task, ET starts `harness wait <task>` as a background command and ends its turn. Claude Code starts a new turn when the command ends. ET uses no tokens while it waits.

### 11.5 Findings from live tests

1. Claude Code never auto-allows inline code such as `python3 -c`. A script file with `python3 <file>` is allowed. The tower rules require script files.
2. Claude Code refused `cd <other directory> && git ...` in dontAsk mode. `git -C <path>` is allowed. The tower rules forbid `cd`.
3. Claude Code protects `~/.claude`. A data root under `~/.claude` makes draft writes fail.
4. A tower model refuses a step that its rules forbid before the permission system decides. Permission tests must state that the researcher authorized the attempt.

### 11.6 Phase 2 result (2026-10-08)

| Check | Result |
|---|---|
| Unit tests | 39 PASS |
| DT probe: Read tool and `cat` on workspace code, write to the harness repository, `harness executor` | Refused |
| DT probe: write own draft, write `PROJECT_STATE.md`, `harness root` | Allowed |
| VT probe: write workspace, `python3` script that writes workspace, publish a task as ET | Refused (the `python3 -c` form) |
| ET probe: write `state/`, `git push --force`, publish a handoff as DT | Refused |
| ET end to end (Sonnet, background session): write a script, commit, push, publish a task, wait in the background, read the result, publish a report, send REPORT_READY to DT | PASS without researcher input after the first prompt |
| Ledger for the end-to-end test | One sent and one received REPORT_READY with parsed headers |
| `harness verify` after the end-to-end test | 0 problems |

### 11.7 Sandbox result (2026-10-08)

| Probe (script run with `python3 <file>` in a tower session) | Result |
|---|---|
| VT script writes the workspace | Refused: read-only file system |
| VT or ET script writes the harness repository | Refused: read-only file system |
| ET script writes `state/PROJECT_STATE.md` | Refused: read-only file system |
| VT script writes its own draft directory | Allowed |
| VT script opens `https://example.com` | Refused by the network proxy (403) |
| ET `git ls-remote` to `gitlab.com` | Refused by the network proxy (403) |
| ET `git ls-remote` to `github.com`, ET `git commit` in the workspace | Allowed |
| VT script lists `~/.ssh` | Not visible in the sandbox |
| VT script listens on `0.0.0.0:18765` | The listen succeeds inside the sandbox network. The host never sees the port. |

### 11.8 Known limits

1. A sandbox covers Bash commands only. Read, Edit, Write, and WebFetch follow the permission rules.
2. VT cannot read output files on a server. A result gives their size and SHA-256 only. A content check needs an `inspect` task or a later output-fetch feature.
3. The researcher starts the executor daemon and the towers.
4. `git push` to GitHub through the sandbox proxy was not tested. `git ls-remote` to GitHub works.

## 12. Full flow and SSH tests (2026-10-08)

### 12.1 Changes from the tests

1. A FULL_RUN handoff also authorizes VALIDATION runs. The old rule refused them, but the main flow needs a VALIDATION run before the PRE_RUN verdict.
2. A PRE_RUN verdict can bind the predecessor handoff of the task handoff. The predecessor must have `full_run_condition: ALL_PRE_RUN_GATES_VALID`, the successor must have FULL_RUN, and both must have the same PRE_RUN gates.
3. The daemon reaps exited workers. A zombie worker counts as dead, so the daemon starts a new worker.
4. The executor creates empty log files for a run that prints nothing.

### 12.2 Full flow with three towers

Local transport with the test docker. DT, ET, and VT ran as Sonnet background sessions with the sandbox on. The researcher gave one approval to DT.

| Step | Record or message |
|---|---|
| DT publishes the handoff, then revisions 2 and 3 (VALIDATION_RUN with the promotion condition, then FULL_RUN) | HANDOFF-DEMO-2-R1 to R3 |
| ET implements, commits, pushes, and runs VALIDATION | TASK-DEMO-2-001, RESULT PASS |
| ET reports and asks VT | REPORT-DEMO-2-001, REVIEW_REQUEST |
| VT gives the PRE_RUN verdict | VERDICT-DEMO-2-001, VALID |
| ET runs FULL with the verdict | TASK-DEMO-2-002, RESULT PASS |
| VT gives the POST_RUN verdict | VERDICT-DEMO-2-002, VALID |
| DT reports to the researcher in Korean | Final message of DT |

Two QUESTION messages from ET to DT show the two rule conflicts that section 12.1 items 1 and 2 fix. ET did not get around a rule. `harness verify` gave 0 problems.

### 12.3 SSH server test

Server `POSTECH_900_A6000_8`, container `VLM_EGG_QUAL_A6000_001`, a new test folder `/home/vision/dongjun/harness-test` with its own bare origin. The test did not use an existing checkout. The test folder was removed after the test.

| Task | Result |
|---|---|
| gpu-status | PASS, 8 GPUs listed |
| run `scripts/check.sh` | PASS, git commit match, image and GPU list recorded, output hash |
| run `scripts/fail.sh` | FAIL, exit code 5, stderr tail in the message |
| run `scripts/slow.sh`, worker killed after launch | Daemon restarted the worker, the worker reattached, one launch, complete logs, PASS |

### 12.4 Limits found

1. ET cannot push over SSH in the sandbox. The sandbox network allows HTTP hosts only. A real project needs a GitHub origin or a sandbox exception.
2. No server has `claude`, so `inspect` mode does not work on the servers yet.
3. VT cannot read output files. It checks hashes in the result records.

## 13. Fetched outputs

1. A run task can list `fetch_outputs`. Each path must also be in `required_outputs`.
2. After the run, the executor copies each listed file to `<data-root>/artifacts/<task-id>/<path>`. The limits are `executor.fetch_max_file_bytes` (default 1 MiB) and `executor.fetch_max_total_bytes` (default 20 MiB).
3. The executor compares the SHA-256 of the copy with the SHA-256 that it computed in the container. A mismatch, a missing file, or a file over a limit goes to `fetch_skipped`.
4. A copied file is read-only. The result lists it in `fetched`. `harness verify` checks each copy against its SHA-256.
5. `artifacts/` is inside the data root Git repository. All towers can read it.
6. SSH test on the A6000 server (2026-10-08): a 29-byte metrics file was copied with a matching SHA-256. A 3 MB file went to `fetch_skipped` with the size reason.

## 14. Phase 4 — DT operation

### 14.1 Features

1. `harness handoff-draft <experiment>` writes the next revision with `revision` and `predecessor` filled. It copies the gates and body of the latest revision, sets DRAFT, and clears `approval`.
2. `harness publish ... --dry-run` runs every check and stores nothing.
3. Body limits: `limits.handoff_body_chars` (12000), `report_body_chars` (9000), `verdict_body_chars` (9000). The researcher raised each limit by 1.5 times on 2026-10-08, because STE text is longer. `harness publish` refuses a longer body.
4. `hooks/dt_guard.py` runs before each DT tool call:
   - An APPROVED handoff publish needs the `approval` text verbatim in a message that the researcher typed in the DT session. The hook reads the transcript. A researcher message has origin kind `human`, or no origin and a `promptId` in print mode. Peer messages, task notifications, tool output, and meta entries do not count. If the transcript cannot be read, the hook blocks.
   - A Write or Edit of `state/PROJECT_STATE.md` must not exceed `limits.project_state_chars` (18000).
5. `harness init` writes `PROJECT_STATE.md` with four sections: Current decisions, Live hypotheses, Validated evidence, Open questions.
6. `harness status` gives each experiment with its latest handoff, tasks, outcomes, and verdicts.
7. `harness git <repo> <push|fetch|pull|ls-remote>` is the network Git path for ET. The sandbox hides `~/.local/bin/gh`, so the Git credential helper fails in the sandbox. The command runs outside the sandbox, uses only `origin`, requires the origin URL to equal the configured workspace remote, and refuses force, delete, mirror, and refspec forms that rewrite or remove history. ET settings deny `git remote add`, `remote set-url`, `config`, and pushes to an explicit URL.
8. `docs/OPERATIONS.md` is the Korean guide for the researcher.

### 14.2 Phase 4 result (2026-10-08)

| Check | Result |
|---|---|
| Unit tests | 57 PASS |
| DT (Sonnet) publishes APPROVED with the approval that the researcher typed | Published |
| DT copies the approval from a file that another tower wrote | Blocked by `dt_guard` with the reason |
| DT edits `PROJECT_STATE.md` over a 600-character test limit | Blocked by `dt_guard`. The file stayed unchanged |
| ET `bin/harness git ... ls-remote` and `fetch` on the private GitHub repository | Allowed and authenticated |
| ET `bin/harness git ... push --force` | Refused by the command |
| ET `git remote set-url`, `git config`, `git push <url>` | Refused by the permission rules |

A DT that runs commands with `2>&1` or `;` gets a permission refusal, because a rule must match the whole command. The tower rules now require one plain command for each Bash call.

## 15. Directives and DT code access (2026-10-09)

### 15.1 Problem

DT asked ET to read evaluation code and wrote "The researcher approved this question" in the message. ET refused for two reasons. A peer message cannot carry researcher authority, and the workspace content would go to DT, which its settings denied. Both refusals were correct under the rules. The rules had no path that carries researcher authority between towers in a form that a tower can check.

### 15.2 Decisions

| ID | Decision |
|---|---|
| D14 | Authority between towers travels only as records that the harness checks. A tower ignores authority claims in message text. |
| D15 | A `directive` record carries a researcher instruction outside a handoff. |
| D16 | DT can read `workspace/` and run read-only Git commands. DT cannot change the workspace. |

### 15.3 Directive record

Fields: `target` (et or vt), `scope` (READ_ONLY, LOCAL_CHANGE, REMOTE_RUN), `instruction` (the words of the researcher, verbatim), `experiment_id` (or NONE). The body gives the concrete request. ID: `DIRECTIVE-<experiment or GENERAL>-<n>`.

1. `dt_guard` checks that `instruction` occurs verbatim in a message that the researcher typed in the DT session. This is the same check as the handoff approval.
2. A task can name a directive in its `directive` field. The harness refuses the task when the directive does not target `et`, when a run or inspect task has a directive scope other than REMOTE_RUN, or when the experiments differ.
3. ET and VT accept a request when it cites an APPROVED handoff or a directive for them. They check each reference with `harness show`.

### 15.4 Live test

Test sessions `tdt` and `tet` (Sonnet, sandbox on). The researcher told DT to have ET check `scripts/eval.py`. DT published `DIRECTIVE-GENERAL-001` (READ_ONLY) with the researcher words in `instruction`. DT sent a QUESTION with the reference. ET checked the directive, read the code, and sent an ANSWER with line references and observed or inferred labels. DT gave a Korean summary to the researcher. Unit tests: 60 PASS.

## 16. Budgets (2026-10-10)

The researcher chose budgets and no central cycle. Cross-session messages already move work between towers.

| Limit (`config.json` `limits`) | Default | Checked by |
|---|---|---|
| `run_tasks_per_handoff`: run and allocate-run tasks that name one handoff revision | 20 | `harness publish` |
| `full_tasks_per_handoff`: FULL tasks that name one handoff revision | 3 | `harness publish` |
| `recovery_attempts`: the highest `lineage.attempt` | 5 | `harness publish` |
| `messages_per_peer_per_10_minutes`: messages from one tower to one peer | 10 | `message_ledger` hook (blocks the send) |

1. A budget counts tasks for one handoff revision. A new revision needs researcher approval, so a new budget always follows a researcher action.
2. Only `harness publish` checks budgets. The executor does not check them again, so a lower limit does not reject a task that is already published.
3. `harness status` shows the use of each budget for the latest revision.
4. The `faculty-opsd` data root uses run 30, FULL 3, recovery 8, because revision R6 already had 18 run tasks and a recovery chain of 6 when the budgets started.

## 17. Standing grants (2026-10-10)

The researcher expects repeated questions about execution authority. A `grant` record keeps such an answer so that a tower does not ask it again.

1. Fields: `holder` (et or vt), `category` (fixed list, `harness grants --categories`), `constraints`, `instruction` (the words of the researcher, verbatim), `experiment_id` (or NONE), `expires` (`NEVER`, `DATE:YYYY-MM-DD`, or `HANDOFF:<reference>`).
2. Categories: PUSH_BRANCH, LOCAL_IMPLEMENTATION, RUN_VALIDATION, RERUN_AFTER_MECHANICAL_FAILURE, START_FULL_AFTER_VALID, USE_SERVER_RESOURCES, CONTAINER_SETUP, ANSWER_TOWER_QUESTIONS.
3. A `revoke` record withdraws grants. A grant is active when no revoke names it and it has not expired. A `HANDOFF:` grant ends when a newer revision of that experiment exists.
4. DT publishes grants and revokes. `dt_guard` checks `instruction` against the researcher messages, as for a directive. The researcher can publish them with `--actor RESEARCHER`.
5. A grant never covers a research decision, a handoff approval, or a budget increase. Free text such as "the user already approved X" in a memory file is not used, because any session can write it and it has no scope or expiry.

## 18. Worklog and session rotation (2026-10-10)

A long tower session loses quality and costs more for each turn. A tower gets a new session when its context is large.

1. `hooks/context_guard.py` runs at each Stop. It reads the context size of the latest request from the transcript (`input_tokens` + cache creation + cache read). At `limits.context_rotate_tokens` (default 600000; 300000 until 2026-10-16) it blocks the stop once and tells the tower to write a handover.
2. `harness handover <tower> <draft>` adds the handover to `<data-root>/worklog/<tower>.md` and writes `runtime/rotate/<tower>.json`.
3. The executor daemon sees the request. When the tower is idle, it restarts the tower with the same model and a first prompt that tells it to read its worklog summary, the last handover, the status, and recent messages.
4. Each tower keeps a worklog of milestones. The rules state a limit of 40000 characters. Above the limit the tower writes a summary in `<tower>-summary.md` with the `harness-writing` skill. The harness does not enforce or watch the worklog size (researcher decision).
5. Live test: with a 20000-token limit, the hook stopped an ET session, ET wrote the handover, and the request appeared. The restart step has unit tests only, because a live restart would replace the running towers of the same name.
6. When the faculty-opsd towers started this feature, DT had about 261000 and ET about 267000 context tokens.
7. On 2026-10-16 the researcher doubled the limit to 600000. A new session starts at about 45000 tokens and reaches 60000 to 95000 after it reads its worklog, so the 300000 limit left 200000 to 250000 tokens of work. Claude Code compacted an old ET session at about 968000 tokens, so 600000 stays below automatic compaction.

## 19. Models (2026-10-10)

| ID | Decision |
|---|---|
| D17 | Every model call is Opus 5.5 or stronger. Token cost is not a constraint. |
| D18 | DT and VT use Fable 5.1. ET uses Opus 5.5. |

Reason for D18: VT is the gate for FULL runs and for result acceptance, so a missed defect becomes a research conclusion. A DT error in design or paper reading spreads to all later work. ET uses the most tokens, and its failures show in run results and VT verdicts.

1. `config.json` `towers.models` gives the model of each tower. `harness tower start`, `restart`, and the rotation in the executor daemon use it. `--model` overrides it for one start.
2. The rendered settings set `CLAUDE_CODE_SUBAGENT_MODEL` to the model of the tower and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`. A subagent always uses the tower model, whatever the Agent tool asks for.
3. `ANTHROPIC_DEFAULT_HAIKU_MODEL` is `towers.background_model` (default `claude-opus-5-5`). Claude Code background calls, such as title generation, and the `haiku` alias then use Opus.
4. The harness defaults keep D17 when `config.json` has no model settings: towers use Opus, and subagents and background calls use Opus.
5. Check on 2026-10-10: the three tower sessions used only Opus 5.5 and no subagents. The Haiku sessions in the harness transcript folder were short test sessions that ran with `--model haiku`.

## 20. Keeping towers reachable (2026-10-11)

1. Claude Code stops the process of a background session that is done and unattached for about an hour. The conversation stays on disk, but the session has no inbox socket, so cross-session messages fail with "not reachable". On 2026-10-10 and 2026-10-11 this stopped VT and ET after rotations, and DT could not reach VT. A blocked session (it waits for a reply) also loses its process after about an hour: on 2026-10-12 ET waited for a decision, lost its process, and DT could not reach it, because the first version of the revival checked only state done.
2. No setting changes the one-hour rule. Pinning exists only as a key in the agent view.
3. The executor daemon checks dt, et, and vt at each step. When no session of a tower has a process (state done or blocked), the daemon runs `claude respawn <id>` (at most once in 300 seconds for one session). The session continues its conversation under the same ID with its launch settings. A live test confirmed the data root setting and message delivery after a respawn.
4. `claude stop` also gives state done and no process, so the daemon revives such a session. To stop a tower and keep it stopped, use `harness tower stop`, which also removes the session. `config.json` `towers.keep_alive: false` turns the revival off.
5. Tower rule: when SendMessage reports a tower as not reachable, wait one minute and send again. If that fails, tell the researcher.

## 21. Discussion fork DT2 (2026-10-20)

1. The researcher wants a second DT conversation with the same context, like `/fork`. A tower session restarts from scripts, so `/fork` is not available. The researcher decided that the copy is discussion-only.
2. `harness tower fork dt2` copies `worklog/dt.md` and `worklog/dt-summary.md` to `worklog/dt2.md` and `worklog/dt2-summary.md`, then starts the session `dt2` with a fork prompt. Run it after a DT handover, so that both sessions start from the same handover.
3. The rules of DT2 are `towers/dt.md` followed by `towers/dt2.md`. The harness writes the combined file to `<data-root>/runtime/rules/dt2.md`. The DT2 rules have priority.
4. `settings/dt2.json` denies `SendMessage`, each `bin/harness publish` command, and each edit in `state/`, `records/`, and the DT worklog. DT2 writes drafts in `runtime/drafts/dt2/`. The researcher gives an accepted draft to DT, and DT publishes it.
5. DT2 uses the DT model unless `towers.models.dt2` is set. The daemon rotates and revives DT2 as it does the other towers. To end DT2, run `harness tower stop dt2`.

## 22. Cancel records (2026-10-20)

1. Problem: on 2026-10-19 the judge server task TASK-VLM-VAD-REPRO-001-016 (vLLM, timeout 7 days) kept GPU 7 and the a100 `faculty_opsd` slot after its client tasks ended. No tower could stop it: the harness had no stop command, the executor killed a run only at its timeout, towers have no server access (by design), and `judge_serve.sh` had no stop file. The researcher stopped it by hand with `docker exec ... kill`.
2. A cancel record `CANCEL-<task ID>` has the fields `task` (reference) and `reason`. `harness cancel <task ID> --reason <text> --actor <DT|ET|RESEARCHER>` publishes it. A task has at most one cancel. The harness refuses a cancel for a task that has a result. VT and DT2 cannot cancel.
3. The worker reads the cancel at each poll. It stops the process group of the run in the runtime (TERM, KILL after 10 seconds), the same stop as for a timeout. It cannot stop any other process. Then it collects and fetches outputs as usual and publishes a result with outcome CANCELLED and the cancel reference. A reattached worker also sees the cancel.
4. A task that has not launched gets a CANCELLED result with preflight NOT_RUN and no run. The daemon claims a cancelled queued task at once, without its (server, repo) slot.
5. When the run ends before the worker sees the cancel, the result has the outcome of the run and no `cancel` field.
6. CANCELLED is not a mechanical failure, so RERUN_AFTER_MECHANICAL_FAILURE does not cover a rerun. ET does not cancel a FULL task itself; DT asks the researcher before it cancels a FULL task.
7. The journal gets a CANCELLED event with the cancel reference and whether the run had launched.

## 23. Remote Control for towers (2026-10-26)

1. The researcher wants to reach DT, DT2, and the harness session from the Claude mobile app (Remote Control). A Remote Control link is bound to one session ID (docs: code.claude.com/docs/en/remote-control). A tower gets a new session at each rotation, so a link does not survive a rotation.
2. Test on 2026-10-26 (Claude Code 2.1.310): `claude --bg --remote-control <name> ...` works. The background session reports "/remote-control is active" with its link, and `~/.claude/jobs/<id>/state.json` holds `bridgeSessionId: cse_<id>`; the link is `https://claude.ai/code/session_<id>`. The job keeps `--remote-control` in `respawnFlags`, so a keep-alive respawn (section 20) keeps Remote Control.
3. `config.json` `towers.remote_control` is `true` (every tower) or a list of tower names. `harness tower start`, `restart`, `fork`, and the rotation in the daemon then pass `--remote-control <tower>`. The title in claude.ai/code is the tower name. After a rotation the researcher opens the newest session with that title; the daemon log and the TOWER_ROTATED journal event give the link. `harness tower status` prints the link of each live tower session.
4. The link changes at each rotation. Claude Code has no mechanism that gives a new session the link of an old one, except `--resume` of the same conversation. A rotation starts a new conversation on purpose (section 18), so the harness accepts the new link.

## 24. A second tower cycle with a name suffix (2026-10-27)

1. The researcher wants a separate cycle `dt_tmp`, `et_tmp`, `vt_tmp` with the same structure as the main cycle, for a temporary line of work. The two cycles must not talk to each other.
2. Decision: a second cycle is a second data root. It has its own records, worklogs, PROJECT_STATE, config, journal, message ledger, executor daemon, and workspace clones. The harness repository is shared. `config.json` `towers.name_suffix` (here `_tmp`) gives the session names: the tower name stays `dt` inside the harness (rules files, hooks, record actors, `harness tower start dt`), and the Claude Code session is `dt_tmp`. The daemon of a data root rotates and revives only the sessions with its suffix.
3. The rendered rules of a suffixed tower end with a "Session names" section that gives the session name of each peer. The message ledger hook blocks a message to a tower name of another suffix (journal entry `blocked: other cycle`) in both directions: `dt_tmp` cannot message `et`, and `et` cannot message `dt_tmp`. A name that is not a tower name, such as the maintenance session, passes. The main cycle does not know that the second cycle exists.
4. The servers are shared, so the second data root must use its own checkouts: the daemons do not know each other's (server, repo) slots. On 2026-10-27 the clones `/home/vision/dongjun/harness-tmp/<repo>` (container path `/workspace/harness-tmp/<repo>`, outputs `/workspace/harness-tmp/outputs`) were made on a100 and a6000 for `~/harness-data/faculty-opsd-tmp`. GPUs are still shared: ET of each cycle sees the other cycle's processes in gpu-status as external jobs.
5. Commands take `--data-root <path>` before the subcommand, because `.harness-data-root` points to the main root: `bin/harness --data-root ~/harness-data/faculty-opsd-tmp tower start dt`. The towers themselves get the root from `HARNESS_DATA_ROOT` in their rendered settings.

## 9. Open decisions

None. The researcher accepted O1 to O3 as D9 to D11.
