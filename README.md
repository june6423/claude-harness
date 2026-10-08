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

## Status

Phase 0. The repository contains the tower instructions and the writing skills only.
