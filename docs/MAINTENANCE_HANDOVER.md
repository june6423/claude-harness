# Maintenance session handover

Written 2026-11-08 by the maintenance session `ef02e66f` (name "Harness_Control"), which ran from 2026-10-08 in `~/research_control`. Its context reached about 580 k tokens, so the researcher starts a new maintenance session with this file. Read it first, then `docs/DESIGN.md` (decisions D1 to D26, sections 1 to 27) for the reasons behind each feature.

## The researcher

- Korean in every reply. Give times in KST (UTC+9). Machine-read documents and tower rules are English STE (`.claude/skills/harness-writing`, `.claude/skills/asd-ste100`).
- Decides research questions, models, and authority. Prefers a short diagnosis with numbers, then a recommendation, then one question.
- Every model call must be Opus 5.5 or stronger. Never start a test session with `--model haiku`.
- Before a tower restart: say so, use `--resume`, and restart only the towers that the researcher named. A restart cuts a `claude attach` terminal; the Remote Control link stays.
- Never modify existing server directories or checkouts in tests. Use temporary folders and remove them.

## Layout

| Path | What |
|---|---|
| `~/harness` | The harness repository (GitHub `june6423/claude-harness`, private, push with `git push origin HEAD:main`). Python stdlib only. `python3 -m unittest discover -s tests -t .` (102 tests). |
| `~/harness-data/faculty-opsd` | Main data root (own git, committed by hand). `.harness-data-root` points here. `vast_backup/` (317 GB) is ignored. |
| `~/harness-data/faculty-opsd-tmp` | Second cycle (dt_tmp, et_tmp, vt_tmp, `towers.name_suffix: "_tmp"`). Dormant; the researcher said to leave it alone. Its daemon is not running. |
| `~/.claude/projects/-home-june6423-research-control/memory/` | Auto-memory of the maintenance project. `harness-phase0-decisions.md` has the dated history. |
| `ops/token_usage.py` | Token use by model and session from the transcripts. |

## Running state (2026-11-08)

- Towers: dt (Opus), et (Opus), vt (Fable), dt2 (Opus, discussion-only fork of DT). Remote Control for dt, et, dt2. `config.json` `towers.models` and `towers.remote_control` set this.
- Executor daemon: systemd user service `harness-executor` (`KillMode=process`, linger on). Restart after a harness code change with `systemctl --user restart harness-executor`; workers and tower sessions survive. Log: `<data-root>/runtime/logs/executor-daemon.log`.
- Limits (`config.json` `limits`): context_rotate_tokens 300000, handoff_body_chars 16000, project_state_chars 9000, handover_summary_chars 8000, dt_directives_per_handoff 10, run 30 / FULL 5 / recovery 8 per handoff revision.
- Current experiment: VLM-VAD-GRAD-001 (B200, gradient analysis), HANDOFF R2 is a correction revision of R1.
- Pending at handover: ET restart with `--resume` to load the settings of commit fb5ee8e (it was busy).

## How to operate

- Tower restart: `bin/harness tower restart <tower> --resume <session id>`. Get the id from `claude agents --json` (name field). Check `status` first: restart only an idle tower unless the researcher says otherwise. The restart writes a keep-alive hold so the daemon does not revive the old session in between.
- Verify that a restart loaded new settings: compare `~/.claude/jobs/<id>/state.json` `createdAt` with the mtime of `<data-root>/runtime/settings/<tower>.json`. The `pid` in `claude agents` is a pre-forked spare process; its start time says nothing.
- A session started with `--resume` uses `--system-prompt-snapshot off`, so it re-reads `towers/<tower>.md` at each request. A rotated session reads the rules only at start.
- Find denials: scan the tower transcript `~/.claude/projects/-home-june6423-harness/<session>.jsonl` for tool results with "has been denied" or `sandbox_violations`.
- Messages to a tower: `SendMessage` to its name. Subscribe with `notify_when_idle` to act when it is idle.
- Kill a process by PID only. Never `pkill -f "harness executor watch"` (it matches the calling shell).

## Permission model (D20)

- Every tower: dontAsk mode, Bash sandbox (bubblewrap, AppArmor profile `ops/apparmor/bwrap`), `autoAllowBashIfSandboxed`, allow rules for common text tools, every `git push` form denied (push only through `bin/harness git`), `bin/harness` runs outside the sandbox (`excludedCommands`).
- dontAsk still refuses a Bash call with a loop, a heredoc, or an awk program, and any Write outside the allowed paths; the rules tell towers to write helper scripts into `runtime/drafts/<tower>/`.
- ET also runs `ssh`, `scp`, `rsync` outside the sandbox. DT2 may use the Google Slides connector and `*.googleusercontent.com`.
- Authority only through records: handoff (researcher approval text), directive (`origin` RESEARCHER with transcript check, or DT for engineering decisions), grant, revoke, cancel. A correction revision (`approval: "CORRECTION"`) fixes DT's own handoff text without researcher text.

## Known open points

- VT is the main Fable consumer (about 13 M input tokens a verdict before fb5ee8e). Check the effect of the context-economy rules with `ops/token_usage.py` after a few verdicts.
- The tmp cycle and DT2 are unused; do not change them without the researcher.
