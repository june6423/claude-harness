# Harness Design — Phase 0

Status: APPROVED by the researcher on 2026-10-08. Phase 0 is complete (section 8.1). Phase 1 is complete (section 10.5). Phase 2 is complete (section 11.6).

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
2. The entrypoint runs inside the container. The executor does not run host Python.
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
  "outcome": "PASS | FAIL | REJECTED | INTERRUPTED",
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
          "containers": {"<container>": {"workdir": "<checkout path in the container>"}}
        }
      }
    }
  }
}
```

The `local` transport runs commands on this machine. Tests use it.

### 10.2 Daemon and workers

1. `harness executor watch` claims each task that has no result and no claim. One daemon runs for one data root.
2. The daemon starts one detached worker for each claim. A dead worker gets a new worker.
3. One `run` task at a time uses a server and repository pair. The pair has one checkout.
4. The worker checks the task again with the same code as `harness publish`. A failed check gives a REJECTED result.

### 10.3 Run mode

1. Preflight on the host: no tracked changes, fetch the branch, switch, fast-forward only, HEAD equals the commit, and `entrypoint[0]` has mode 100755 at the commit.
2. Preflight in the container: the container runs, the entrypoint is executable, and `output_root` is writable. The worker records the image and the GPU list.
3. Launch: `docker exec -d` starts a wrapper with `setsid`. The wrapper writes `pid`, `stdout`, `stderr`, and `exit_code` in `<output_root>/.harness/<task-id>/`.
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
3. A task needs a running container. The executor does not create containers.

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

## 9. Open decisions

None. The researcher accepted O1 to O3 as D9 to D11.
