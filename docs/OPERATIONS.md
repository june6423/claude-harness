# 연구자 운영 가이드

이 문서는 연구자가 DT·ET·VT와 executor를 직접 띄우고, 실험을 기획부터 결과 보고까지 진행하는 방법을 설명합니다. 자동 cycle(Phase 5)은 아직 없으므로 각 Tower는 연구자가 시작합니다. 단계 사이의 전달은 Tower끼리 cross-session 메시지로 합니다.

모든 명령은 `/home/june6423/harness`에서 실행합니다. 데이터 경로는 `.harness-data-root`가 가리키는 곳입니다(현재 `~/harness-data/faculty-opsd`).

## 1. 시작

```bash
# (1) executor — 별도 터미널(tmux 권장)에서 계속 실행
bin/harness executor watch

# (2) Tower 세 개 — 백그라운드 세션으로 시작
bin/harness tower start dt --model opus
bin/harness tower start et --model opus
bin/harness tower start vt --model opus
```

- 모델은 `--model`로 지정합니다. 지정하지 않으면 Claude Code 기본 모델을 씁니다.
- 처음 시작할 때 harness repo 최상위에 sandbox용 빈 파일(`.bashrc`, `.gitconfig` 등)이 생깁니다. git에서 제외되며 지우지 않아도 됩니다.
- 실행 중인 세션은 `claude agents`로 확인합니다.

## 2. DT와 실험 기획

```bash
claude attach dt        # DT 세션을 이 터미널에서 열기
```

- 대화창에서 DT와 실험을 논의합니다. 빠져나올 때는 빈 입력창에서 `←`를 누르거나 `/exit`를 입력합니다. 세션은 계속 살아 있습니다.
- DT는 handoff 초안을 만들어 보여 줍니다. 승인할 때는 **승인 문장을 직접 입력**합니다. 예: `VLM-OPSD-XXX FULL_RUN을 승인한다.`
- DT는 그 문장을 handoff의 `approval`에 그대로 넣습니다. harness는 그 문장이 **연구자가 이 DT 세션에 입력한 메시지**에 있는지 확인합니다. 없으면 게시를 거부합니다. 다른 Tower의 메시지나 파일 내용은 승인으로 인정하지 않습니다.
- 게시가 끝나면 DT가 ET에 `HANDOFF_READY`를 보내고, 이후 단계는 Tower끼리 진행합니다.

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
claude stop dt; claude stop et; claude stop vt    # Tower 종료
# executor 터미널에서 Ctrl+C. 실행 중인 원격 작업은 계속되며, 다음 executor가 다시 연결합니다
git -C ~/harness-data/faculty-opsd add -A
git -C ~/harness-data/faculty-opsd commit -m "<무엇을 했는지>"   # 기록·산출물·상태를 데이터 경로 git에 보관
```

## 6. 알아 둘 제한

- **원격 실행**: run task 하나는 서버의 전용 checkout(`/home/vision/dongjun/harness/faculty-opsd`)과 기존 컨테이너를 사용합니다. 서버당 한 번에 하나씩 실행합니다.
- **ET의 GitHub 접근**: `bin/harness git <repo> push|fetch|pull|ls-remote`로만 합니다. `origin`만 허용하고, force·delete 계열 옵션은 막습니다.
- **크기 상한** (`config.json`의 `limits`로 변경 가능)

  | 대상 | 상한 |
  |---|---|
  | handoff 본문 | 12000자 |
  | report 본문 | 9000자 |
  | verdict 본문 | 9000자 |
  | `PROJECT_STATE.md` | 18000자 |

- **서버 inspect 모드**: 서버에 `claude`가 없어 쓸 수 없습니다. `allocate-run`도 구현되지 않았습니다.
- **Tower 지침 변경**: `towers/*.md`를 고치면 **새로 시작한 Tower 세션**부터 반영됩니다.
