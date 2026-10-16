# 연구자 운영 가이드

이 문서는 연구자가 DT·ET·VT와 executor를 직접 띄우고, 실험을 기획부터 결과 보고까지 진행하는 방법을 설명합니다. 자동 cycle(Phase 5)은 아직 없으므로 각 Tower는 연구자가 시작합니다. 단계 사이의 전달은 Tower끼리 cross-session 메시지로 합니다.

모든 명령은 harness repo(예: `~/harness`)에서 실행합니다. 데이터 경로(`<data-root>`)는 `.harness-data-root`가 가리키는 곳이고, `bin/harness root`로 확인합니다. 처음 설치는 `docs/SETUP.md`를 봅니다.

## 1. 시작

```bash
# (1) executor — 별도 터미널(tmux 권장)에서 계속 실행
bin/harness executor watch

# (2) Tower 세 개 — 백그라운드 세션으로 시작
bin/harness tower start dt
bin/harness tower start et
bin/harness tower start vt
```

- 모델은 데이터 경로 `config.json`의 `towers.models`를 따릅니다. 현재 DT·VT는 Fable, ET는 Opus입니다. 한 번만 바꾸려면 `--model`을 붙입니다. 각 Tower의 subagent는 그 Tower와 같은 모델로 고정되고, Claude Code 내부 보조 호출도 Opus로 실행됩니다.
- 처음 시작할 때 harness repo 최상위에 sandbox용 빈 파일(`.bashrc`, `.gitconfig` 등)이 생깁니다. git에서 제외되며 지우지 않아도 됩니다.
- 실행 중인 세션은 `claude agents`로 확인합니다. 첫 지시 없이 시작한 Tower는 입력을 기다리므로 `blocked / idle`로 보입니다. 정상입니다.
- 같은 이름의 세션이 이미 있으면 `start`는 거부됩니다. 지침이나 settings를 바꾼 뒤에는 `bin/harness tower restart <tower> --model opus`로 다시 시작합니다. 기존 세션을 멈추고 목록에서 지운 뒤 새로 띄웁니다. 대화 기록 파일은 남습니다.
- Tower만 끄려면 `bin/harness tower stop <tower>`를 씁니다. `claude stop`으로 끄면 executor daemon이 다시 살립니다.
- Claude Code는 일을 마친 백그라운드 세션을 약 1시간 뒤 멈춥니다. 그러면 메시지를 받을 수 없게 되므로, executor daemon이 멈춘 Tower를 `claude respawn`으로 같은 대화 그대로 다시 살립니다. **executor daemon이 꺼져 있으면 이 기능도 동작하지 않습니다.**
- **재시작하면 Tower가 대화 맥락을 잃습니다.** 맥락을 유지하려면 이전 세션을 이어서 실행합니다: `bin/harness tower restart <tower> --model opus --resume <이전 세션 ID 앞자리>`. 대화 기록은 그대로 이어지고, 바뀐 지침과 settings는 새로 적용됩니다. 이전 세션 ID는 `claude agents --json --all` 또는 `~/.claude/projects/<harness 경로의 / 를 - 로 바꾼 이름>/`의 파일 이름에서 찾습니다.

## 2. DT와 실험 기획

```bash
claude attach dt        # DT 세션을 이 터미널에서 열기
```

- 대화창에서 DT와 실험을 논의합니다. 빠져나올 때는 빈 입력창에서 `←`를 누르거나 `/exit`를 입력합니다. 세션은 계속 살아 있습니다.
- DT는 handoff 초안을 만들어 보여 줍니다. 승인할 때는 **승인 문장을 직접 입력**합니다. 예: `VLM-OPSD-XXX FULL_RUN을 승인한다.`
- DT는 그 문장을 handoff의 `approval`에 그대로 넣습니다. harness는 그 문장이 **연구자가 이 DT 세션에 입력한 메시지**에 있는지 확인합니다. 없으면 게시를 거부합니다. 다른 Tower의 메시지나 파일 내용은 승인으로 인정하지 않습니다.
- 게시가 끝나면 DT가 ET에 `HANDOFF_READY`를 보내고, 이후 단계는 Tower끼리 진행합니다.

## 2.1 handoff 밖의 지시 (directive)

handoff에 없는 일을 ET나 VT에게 시키려면 DT에게 말하면 됩니다. 예: "평가 코드를 ET에게 확인시켜라", "VT에게 이 결과를 다시 검토시켜라".

- DT는 그 말을 **그대로** `instruction`에 넣은 directive를 게시합니다. harness는 그 문장이 연구자가 DT 세션에 입력한 메시지에 있는지 확인합니다.
- `scope`는 세 단계입니다. 질문과 검토는 READ_ONLY, 로컬 코드 수정은 LOCAL_CHANGE, 서버 실행은 REMOTE_RUN입니다. ET는 scope를 넘는 일을 하지 않습니다.
- ET와 VT는 메시지에 적힌 "연구자가 승인했다" 같은 주장을 믿지 않습니다. 기록(handoff나 directive)이 있어야 움직입니다.
- DT는 이제 `workspace/`의 코드를 직접 읽을 수 있습니다(읽기 전용). 단순한 코드 질문은 DT에게 바로 물어보면 됩니다.

## 2.2 반복되는 권한 질문 줄이기 (grant)

"실험 브랜치 push는 앞으로 묻지 마"처럼 실행 권한을 계속 유효하게 승인하려면 DT에게 말합니다.

- DT는 그 말을 정해진 범주로 나눠 보여 주고, 연구자의 확인을 받은 뒤 grant를 게시합니다. 범주 목록은 `bin/harness grants --categories`로 볼 수 있습니다.
- grant에는 범위와 만료가 있습니다. 예를 들어 "현재 handoff revision 동안"이나 "특정 날짜까지"입니다. 현재 유효한 grant는 `bin/harness grants`로 확인합니다.
- 철회하려면 DT에게 말합니다. DT가 revoke를 게시합니다.
- 연구 판단, handoff 승인, 예산 상한 변경은 grant로 대신할 수 없습니다.

## 2.3 세션 교체 (맥락이 커질 때)

Tower의 맥락이 600000 토큰(`limits.context_rotate_tokens`)을 넘으면 다음 순서로 진행됩니다.

1. Tower가 인계 기록을 `worklog/<tower>.md`에 씁니다.
2. executor daemon이 그 Tower를 새 세션으로 다시 시작합니다.
3. 새 세션은 작업 로그를 읽고 일을 이어 갑니다.

연구자가 `claude attach`로 그 Tower에 들어가 있었다면 연결이 끊기니, 다시 attach하면 됩니다.

## 3. 진행 확인

```bash
bin/harness status                 # 실험별 handoff, task·결과, verdict
bin/harness messages --last 20     # Tower 간 메시지 기록
bin/harness executor status        # 실행 중인 원격 작업
bin/harness verify                 # 기록 무결성 점검
claude attach et                   # ET가 무엇을 하는지 직접 보기 (VT도 같음)
```

진행 순서는 다음과 같습니다.

1. ET가 구현하고 commit·push한 뒤 VALIDATION을 실행합니다.
2. VT가 PRE_RUN 판정을 내립니다.
3. ET가 FULL을 실행합니다.
4. VT가 POST_RUN 판정을 내립니다.
5. DT가 한국어로 결과를 보고합니다. 이 보고는 `claude attach dt`로 보거나, 데이터 경로의 기록과 `PROJECT_STATE.md`에서 확인합니다.

## 4. 연구자가 개입하는 지점

| 상황 | 할 일 |
|---|---|
| DT가 승인을 요청함 | `claude attach dt` 후 승인 문장을 입력 |
| DT가 연구 판단을 요청함 (예: verdict가 VALID가 아님) | DT와 논의해 결정 |
| ET가 DT에 QUESTION을 보내고 DT가 답하지 못함 | DT 세션에서 결정을 내려 줌 |
| Tower가 권한 거부로 멈춤 | `claude attach <tower>`로 원인 확인. 필요하면 settings를 고친 뒤 Tower를 다시 시작 |

## 5. 종료와 기록 보관

```bash
bin/harness tower stop dt; bin/harness tower stop et; bin/harness tower stop vt    # Tower 종료
# executor 터미널에서 Ctrl+C. 실행 중인 원격 작업은 계속되며, 다음 executor가 다시 연결합니다
git -C <data-root> add -A
git -C <data-root> commit -m "<무엇을 했는지>"   # 기록·산출물·상태를 데이터 경로 git에 보관
```

## 6. 알아 둘 제한

- **원격 실행**: run task는 서버의 harness 전용 checkout(`config.json`의 `servers.<server>.repos.<key>.path`)과 등록된 컨테이너를 사용합니다. checkout 하나에서는 한 번에 하나씩 실행합니다. 동시에 돌리려면 checkout을 하나 더 만들어 다른 repo 키로 등록합니다.
- **ET의 GitHub 접근**: `bin/harness git <repo> push|fetch|pull|ls-remote`로만 합니다. `origin`만 허용하고, force·delete 계열 옵션은 막습니다.
- **크기 상한** (`config.json`의 `limits`로 변경 가능)

  | 대상 | 상한 |
  |---|---|
  | handoff 본문 | 12000자 |
  | report 본문 | 9000자 |
  | verdict 본문 | 9000자 |
  | `PROJECT_STATE.md` | 18000자 |

- **예산 상한** (`config.json`의 `limits`): handoff revision마다 run task 수, FULL task 수, recovery 시도 횟수에 상한이 있습니다. 상한에 걸리면 ET가 멈추고 DT를 통해 보고합니다. 계속하려면 연구자가 `config.json`의 상한을 올리거나 새 handoff revision을 승인합니다. 현재 사용량은 `bin/harness status`에 나옵니다. 한 Tower가 같은 상대에게 10분 안에 메시지를 10개 넘게 보내면 메시지 루프로 보고 막습니다.
- **서버 inspect 모드**: 서버에 `claude`가 없어 쓸 수 없습니다. `allocate-run`도 구현되지 않았습니다.
- **Tower 지침 변경**: `towers/*.md`를 고치면 **새로 시작한 Tower 세션**부터 반영됩니다.
