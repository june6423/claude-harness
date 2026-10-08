# Research harness

This repository is a Claude Code harness for research work. It replaces the Codex-based `research_control` orchestration. The `research_control` directory is a reference only.

## Towers

Each tower runs as an independent Claude Code session. The towers communicate through cross-session messages.

| Tower | Instruction file | Role |
|---|---|---|
| DT | `towers/dt.md` | Research reasoning with the researcher |
| ET | `towers/et.md` | Implementation and remote execution |
| VT | `towers/vt.md` | Independent validation of ET work |

## Writing policy

The `harness-writing` skill sets the language and the ASD-STE100 mode for each output channel. Machine-read documents and cross-session messages use English STE Strict. Replies to the researcher use Korean.

## Usage

```bash
bin/harness init ~/harness-data/<project>        # create a data root and point this repository to it
bin/harness publish handoff draft.md --actor DT  # validate and store a record. Prints ID@sha256:<hex>
bin/harness list [kind] [--experiment ID]
bin/harness show <ID or reference>
bin/harness verify                               # check hashes, journal, schemas, and references
bin/harness status                               # experiments, tasks, results, and verdicts
bin/harness handoff-draft <EXPERIMENT-ID> --out d.md   # next handoff revision
bin/harness publish <kind> <draft> --dry-run --actor <A>  # check without storing
bin/harness git <repo> push <branch>             # network Git for ET (origin only)
bin/harness message --type STATUS --body-file b.md   # format a cross-session message
bin/harness messages --last 20                   # print the message ledger
bin/harness executor watch                       # run published tasks (one daemon for each data root)
bin/harness executor status
bin/harness wait <task-id> --timeout-minutes 30    # wait for a result and print it
bin/harness tower start et [--model sonnet] [--prompt TEXT]   # start a tower session in the background
python3 -m unittest discover -s tests -t .
```

## Status

Phases 0, 1, 2, and 4 are complete. See `docs/DESIGN.md`. The researcher guide is `docs/OPERATIONS.md` (Korean).
