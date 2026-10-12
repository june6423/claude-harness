# 설치 가이드

이 문서는 새 컴퓨터에 harness를 설치하고, 첫 프로젝트를 구성해서 Tower 세 개(DT, ET, VT)와 executor를 시작하는 방법을 설명합니다. 설치가 끝난 뒤의 일상 운영은 `docs/OPERATIONS.md`를 보면 됩니다.

아래에서 쓰는 이름은 다음 뜻입니다.

| 표기 | 뜻 | 예 |
|---|---|---|
| `~/harness` | harness repo를 clone한 위치 | `/home/alice/harness` |
| `<data-root>` | 프로젝트 하나의 데이터 경로 | `~/harness-data/my-project` |
| `<server>` | 원격 실행 서버 이름(`config.json` 안의 키) | `a6000` |

## 1. 준비물

### 로컬 컴퓨터

| 항목 | 확인 명령 | 비고 |
|---|---|---|
| Linux | `cat /etc/os-release` | Ubuntu 24.04에서 검증했습니다 |
| Claude Code CLI 2.1.29x 이상 | `claude --version` | 로그인되어 있어야 합니다. cross-session 메시지, 백그라운드 세션, `claude respawn`을 씁니다. DT와 VT에 Fable을 쓰려면 계정에서 Fable을 쓸 수 있어야 합니다 |
| Python 3.10 이상 | `python3 --version` | 표준 라이브러리만 씁니다. 추가로 설치할 패키지는 없습니다 |
| git | `git --version` | |
| bubblewrap, socat | `which bwrap socat` | Bash sandbox에 필요합니다. 없으면 `sudo apt install bubblewrap socat` |
| GitHub 인증 | `gh auth status` | ET가 실험 코드를 push할 때 씁니다. `gh auth login` 후 `gh auth setup-git` |
| SSH | `ssh -o BatchMode=yes <ssh_host> true` | 각 서버에 비밀번호 없이(키 방식) 접속되어야 합니다 |

### 원격 서버

| 항목 | 비고 |
|---|---|
| `git`, `docker`, `bash`, `setsid`, `base64`, `sha256sum` | executor가 SSH로 실행합니다 |
| GitHub 읽기 권한 | 서버에서 연구 코드 repo를 clone하고 fetch할 수 있어야 합니다 |
| 실행용 docker 컨테이너 | 미리 띄워 두어야 합니다. executor는 컨테이너를 만들지 않습니다 |
| harness 전용 checkout | 아래 4단계에서 만듭니다. 사람이나 다른 도구가 쓰는 checkout을 harness가 쓰면 안 됩니다. executor가 그 checkout에서 브랜치를 바꾸기 때문입니다 |

## 2. Bash sandbox 준비 (Ubuntu 24.04)

Ubuntu 24.04는 일반 프로그램이 user namespace를 쓰지 못하게 막습니다. 그대로 두면 bubblewrap이 시작하지 못합니다. bubblewrap 하나에만 이 권한을 주는 AppArmor profile을 설치합니다.

```bash
sudo install -m 644 ~/harness/ops/apparmor/bwrap /etc/apparmor.d/bwrap
sudo apparmor_parser -r /etc/apparmor.d/bwrap
bwrap --ro-bind / / --unshare-net true; echo "exit=$?"     # exit=0이면 성공
```

sandbox를 쓰지 않으려면 이 단계를 건너뛰고 `config.json`에서 `"sandbox": false`로 둡니다. 다만 그러면 Tower가 실행한 스크립트는 사용자 권한으로 어디에나 쓸 수 있고, 네트워크에도 제한 없이 접속할 수 있습니다.

## 3. harness 설치와 데이터 경로 만들기

```bash
git clone <harness repo URL> ~/harness
cd ~/harness
python3 -m unittest discover -s tests -t .           # 모두 OK여야 합니다
bin/harness init ~/harness-data/<project>             # 데이터 경로를 만들고, 이 repo가 그곳을 가리키게 합니다
bin/harness root                                      # 방금 만든 경로가 나와야 합니다
```

`init`은 `<data-root>` 안에 `records/`, `state/`, `worklog/`, `artifacts/`, `workspace/`, `runtime/`, `config.json`을 만들고, `<data-root>`를 별도 git repo로 초기화합니다. `runtime/`과 `workspace/`는 그 git에서 제외됩니다.

## 4. 연구 코드와 서버 준비

```bash
# 로컬: 연구 코드 repo를 데이터 경로 안에 clone
git clone https://github.com/<owner>/<repo>.git <data-root>/workspace/<repo>

# 서버마다: harness 전용 checkout과 출력 폴더
ssh <ssh_host> 'mkdir -p ~/harness/outputs && chmod 777 ~/harness/outputs && git clone git@github.com:<owner>/<repo>.git ~/harness/<repo>'
```

컨테이너는 harness 전용 checkout이 보이도록 마운트되어 있어야 합니다. 예를 들어 컨테이너가 서버의 `/home/<user>`를 `/workspace`로 마운트한다면, 컨테이너 안에서 checkout 경로는 `/workspace/harness/<repo>`가 됩니다. 확인 방법:

```bash
ssh <ssh_host> 'docker exec <container> test -d /workspace/harness/<repo> && echo OK'
```

## 5. config.json 작성

`docs/config.example.json`을 `<data-root>/config.json`에 복사하고 `<...>` 부분을 채웁니다.

| 키 | 내용 |
|---|---|
| `towers.models` | Tower별 모델. subagent도 같은 모델로 고정되고, Claude Code 내부 보조 호출은 `background_model`을 씁니다 |
| `towers.sandbox` | 2단계를 했으면 `true` |
| `limits` | handoff revision마다의 run·FULL task 수, recovery 횟수, 메시지 빈도, 세션 교체 기준. 빼면 기본값을 씁니다 |
| `workspaces.<key>.remote` | 로컬 workspace의 `origin` 주소와 정확히 같아야 합니다. `bin/harness git`이 이것을 확인합니다 |
| `workspaces.<key>.server_outputs` | 컨테이너 안에서 실행 결과를 쓸 폴더 |
| `servers.<server>.repos.<key>.path` | 서버 호스트에서 본 harness 전용 checkout 경로 |
| `servers.<server>.repos.<key>.containers.<name>.workdir` | 컨테이너 안에서 본 같은 checkout 경로 |

작성한 뒤 데이터 경로를 commit합니다.

```bash
git -C <data-root> add -A && git -C <data-root> commit -m "Configure project"
```

## 6. 사람마다 바꿀 것

| 파일 | 바꿀 내용 |
|---|---|
| `towers/dt.md`의 "Researcher rules" 절 | 원래 작성자의 개인 연구 원칙입니다. 자기 원칙으로 바꾸거나 지웁니다. 이 절은 DT에만 적용됩니다 |
| `.claude/skills/harness-writing/SKILL.md`, `towers/*.md` | 연구자에게 보고하는 언어가 한국어로 정해져 있습니다. 필요하면 바꿉니다 |
| `config.json` | 모델, 예산, 서버 |

`towers/*.md`나 settings를 바꾼 뒤에는 Tower를 다시 시작해야 반영됩니다(`docs/OPERATIONS.md` 참고).

## 7. Claude Code가 harness 폴더를 신뢰하게 하기

백그라운드 세션은 신뢰한 폴더에서만 시작됩니다. harness 폴더에서 Claude Code를 한 번 실행해 신뢰 확인 창에서 "Yes, I trust this folder"를 고른 뒤 종료합니다.

```bash
cd ~/harness && claude
```

## 8. 시작

```bash
cd ~/harness
setsid nohup bin/harness executor watch >> <data-root>/runtime/logs/executor-daemon.log 2>&1 < /dev/null &
bin/harness tower start dt
bin/harness tower start et
bin/harness tower start vt
claude agents                                         # dt, et, vt가 보여야 합니다
claude attach dt                                      # DT와 실험 기획 시작
```

executor daemon은 원격 task 실행, Tower 세션 교체, 멈춘 Tower 되살리기를 맡습니다. **계속 켜 두어야 합니다.**

## 9. 설치 확인

| 확인 | 명령 | 기대 결과 |
|---|---|---|
| 단위 테스트 | `python3 -m unittest discover -s tests -t .` | 모두 OK |
| 데이터 경로 | `bin/harness root` | `<data-root>` |
| 기록 무결성 | `bin/harness verify` | `0 problem(s)` |
| 서버 GPU 조회 | ET에게 gpu-status task를 하나 요청하거나, 연구자가 task 초안을 `bin/harness publish task <draft> --actor RESEARCHER`로 게시 | result의 outcome이 PASS |
| GitHub 인증 | `bin/harness git <data-root>/workspace/<repo> ls-remote` | 브랜치 목록 출력 |
