# OLIVER · AI Coding Harness

OLIVER is an autonomous planner-executor harness for one text-only foundation
model. Given a repository and an issue, it finds the relevant code, makes a
minimal change, and proves the change with the repository's tests before
calling the task done. The verdict comes from test evidence, never from the
model's own claim.

Built for the LCC × DevClub AI Coding Harness Hackathon: *same model, different harnesses.*

## Evaluation quick start

```bash
git clone <TEAM_REPOSITORY>
cd <TEAM_REPOSITORY>
export AI_API_KEY="<PROVIDED_API_KEY>"
make setup
make run
```

`make run` opens the evaluation console and asks for the repository and the
issue or test case. Nothing else needs to be installed, edited or configured.
The [setup guide](#setup-guide) below covers prerequisites, the expected output
of each step and troubleshooting.

## Setup guide

Run every command from the repository folder, where the `Makefile` is.

### 1. Check the prerequisites

| Needed | Check | Install if missing |
|---|---|---|
| Python 3.10–3.14 | `python3 --version` | Ubuntu 22.04+ and Debian 12 ship one (`sudo apt-get install -y python3`). Fedora: `sudo dnf install -y python3`. macOS: `brew install python@3.12` (the system `python3` 3.9 is too old). |
| git | `git --version` | `sudo apt-get install -y git`. macOS: `xcode-select --install`. |
| make and bash | `make --version` | `sudo apt-get install -y make`. macOS: included with `xcode-select --install`. |
| Network access | | PyPI during `make setup`, and the model provider's API during `make run`. GitHub only when a git or issue URL is given. |
| ripgrep (optional) | `rg --version` | Faster repository search: `sudo apt-get install -y ripgrep` or `brew install ripgrep`. |

- **Platforms.** Linux needs glibc 2.28 or newer (Ubuntu 20.04+, Debian 10+, RHEL 8+). macOS works on Intel and Apple silicon. On Windows, use WSL with Ubuntu.
- **Not needed.** No system-wide Python packages, and no `python3-venv`. If venv support is missing, `make setup` installs pip from PyPI. Docker is only needed for `sandbox = "docker"`.

### 2. Get the code and provide the credential

```bash
git clone <TEAM_REPOSITORY>
cd <TEAM_REPOSITORY>
export AI_API_KEY="<PROVIDED_API_KEY>"
```

OLIVER reads the key only from this environment variable. Never write it into a
file in the repository. `make test` fails if a key is ever committed.

### 3. Install with `make setup`

`make setup` does the following:
1. picks the first Python 3.10–3.14 on `PATH`;
2. creates `.venv/`;
3. installs the pinned dependencies from `dependency-files/requirements.txt` (usually under a minute);
4. checks the result.

The output ends like this:

```text
OLIVER installation check
  Configuration  configuration-files/oliver.toml
  Model          anthropic/claude-sonnet-5   from configuration-files/oliver.toml ([auto_models] anthropic)
  AI_API_KEY     set (anthropic key format)
  Python         3.12.3 at /path/to/repo/.venv/bin/python
  Packages       litellm 1.102.1, pydantic 2.13.5, tree-sitter 0.26.0, tree-sitter-python 0.25.0, pytest 9.1.1
  git            /usr/bin/git
  ripgrep        /usr/bin/rg
Ready. Start the harness with: make run
Setup complete. Next: make run
```

To choose the interpreter, run `make setup PYTHON=python3.11`. Running
`make setup` again is safe, and nothing is installed outside the repository
folder.

### 4. Model (set once by the team)

The model is defined in `configuration-files/oliver.toml` (`model = "provider/model"`)
and is printed by `make setup` and `make run`. Evaluators do not need to change it.
[Model and credential](#model-and-credential) explains the options and overrides.

### 5. Run with `make run`

The console asks for the repository, the issue or test case, and optionally the
test command (see [Evaluation mode](#evaluation-mode)). To solve one task without
prompts, run `make run REPO=/path/to/repo ISSUE=/path/to/issue.md`.

### 6. Optional: `make test`, then `make clean`

`make test` takes about a minute, needs no credential and makes no API calls. It
ends with the number of tests `passed` and `Resolved by hidden tests: 6/6`.
`make clean` removes `.venv/`, `runs/` and `workspace/`, returning the folder to
a fresh clone.

### Troubleshooting

| Message | Cause | What to do |
|---|---|---|
| `ERROR: OLIVER needs Python 3.10 to 3.14 and none was found` | No suitable Python on `PATH` | Install one (step 1), or name it: `make setup PYTHON=/path/to/python3.12` |
| `ERROR: git is required` | git is missing | Install git (step 1) |
| `This Python has no ensurepip (python3-venv); installing pip from PyPI instead` | Debian or Ubuntu Python without `python3-venv` | Nothing; setup continues |
| `No matching distribution found for litellm==1.102.1` | Linux older than glibc 2.28, or a Python outside 3.10–3.14 | Use a newer system, or point `PYTHON=` at a supported Python |
| `Cannot start: ... AI_API_KEY is not set` | The key is not exported in this shell | `export AI_API_KEY="<PROVIDED_API_KEY>"`, then `make run` |
| `Cannot start: the provider of AI_API_KEY is not recognised` | `model = "auto"` cannot tell the provider from the key | Set `model` in `configuration-files/oliver.toml`, or `export OLIVER_MODEL=provider/model` |
| `Checking the model... rejected` | The provider refused the key or the model id | Check that the key belongs to the provider named in `model` |
| `Checking the model... no answer yet` | Network trouble or a rate limit | Nothing; tasks retry transient errors |
| `Configuration error: ... unknown setting` | A typo in `configuration-files/oliver.toml` | Fix the key named in the message |
| `Could not clone ...: the repository does not exist or is private` | Wrong URL, or no git credentials for a private repository | Check the URL, or clone it and enter the local path |
| `Could not fetch <issue URL>` | The GitHub API is unreachable or rate limited | Paste the issue text instead |
| `That directory contains the OLIVER harness` | The path points at this repository | Enter the repository to fix |

## Make targets

| Command | What it does |
|---|---|
| `make setup` | Creates `.venv/` with the first Python 3.10–3.14 found on `PATH` (or `PYTHON=...`). If that Python lacks `python3-venv`, pip is installed from PyPI instead. Then it installs the pinned `dependency-files/requirements.txt` and checks the configuration, the credential and every dependency. It is safe to run again. |
| `make run` | Launches the harness in evaluation mode: the interactive console described below. `make run REPO=<path or git URL> ISSUE=<file or GitHub issue URL>` solves one task without prompts, exiting 0 when resolved and 1 when not. |
| `make test` | Runs the offline test suite (scripted mock model, with the key withheld), then solves the 6 bundled tasks and scores them with hidden tests. `make test REAL=1` scores the configured model on the same tasks using `AI_API_KEY`. |
| `make clean` | Removes the generated artefacts `.venv/`, `runs/`, `workspace/` and caches. |

`make run` and `make test` run `make setup` first if it has not been run yet.

## Evaluation mode

The console is plain text: no colours and no full-screen drawing, so it behaves
the same in every terminal and also accepts piped input. It asks three questions:

| Prompt | Accepted answers |
|---|---|
| `repository>` | A local path. A git URL, which is cloned into `workspace/`; add a branch, tag or commit after a space to check it out. A GitHub issue URL, which clones that repository and offers the issue at the next prompt. |
| `issue>` | Pasted issue or test-case text, ended by a line containing only `END` (or Ctrl-D). The path of a text file. A GitHub issue or pull request URL (title and body are read from the public GitHub API). |
| `tests>` | Enter to detect the test command (pytest, `npm test`, `go test`, `cargo test`, `make test`), or the command that runs the tests. |

Example session (abridged):

```text
========================================================================
  OLIVER  |  AI Coding Harness  |  evaluation mode
========================================================================
  Model        anthropic/claude-sonnet-5   from configuration-files/oliver.toml ([auto_models] anthropic)
  Credential   AI_API_KEY is set (anthropic key format)
  Decoding     temperature 0.0, seed 42
  Limits       60 model turns, 2,000,000 tokens, 1800s per task
  Evidence     runs/
========================================================================
Checking the model... ok

repository> /work/stats
issue> # average() returns the wrong value
average([2, 4]) returns 2.0 instead of 3.0.
END
tests>
========================================================================
OLIVER is working on: # average() returns the wrong value
========================================================================
[OLIVER    0.5s] init: indexed 3 files; backend native; test command: python3 -m pytest; baseline: 2 passed, 0 failed, 0 errors, 0 skipped
[OLIVER    0.5s] tool search_repo def average -> ok
[OLIVER    0.5s] plan: root cause: average() divides by len(values) + 1 instead of len(values)
[OLIVER    0.8s] tool edit_file stats.py -> ok
[OLIVER    1.7s] verify: PASSED | PASS changes: stats.py, tests/test_average_regression.py
========================================================================
Result: RESOLVED: the patch passed verification
  PASS syntax: all changed files parse
  PASS reproduction: exit code 0
  PASS tests: 3 passed, 0 failed, 0 errors, 0 skipped
Changed files: stats.py (M), tests/test_average_regression.py (A)
Repository: /work/stats (patch applied)
Evidence: runs/20260926-123636-average-returns-the-wrong-value/
--- patch.diff ---------------------------------------------------------
-    return total(values) / (len(values) + 1)
+    return total(values) / len(values)
========================================================================
Solve another issue? [y/N]
```

The patch stays applied in the repository, so the official tests can run on it
directly. Every task also writes an evidence bundle to `runs/<id>/`:
`patch.diff`, a standalone `report.html`, `summary.md`, `trajectory.jsonl`
(every model turn and tool call), `verification.json` (baseline and final test
outcomes) and `result.json`. The verdict is one of the following:

- **RESOLVED:** verification passed.
- **NOT VERIFIED:** a patch exists, but it failed verification.
- **FAILED:** no usable patch.

Scripted input works too, for example:
`printf '%s\n' /path/to/repo /path/to/issue.md '' | make run`.

## Model and credential

- **Model configuration.** `configuration-files/oliver.toml` defines the model and every run setting.
  - `model` takes a LiteLLM id written `provider/model`, for example `anthropic/claude-sonnet-5`, `openai/gpt-4.1` or `gemini/gemini-2.5-flash`. Set it to the model prescribed for the evaluation.
  - With the default `model = "auto"`, OLIVER uses the `[auto_models]` entry for the provider that issued `AI_API_KEY`, recognised from the key prefix:

    | Prefix | Provider |
    |---|---|
    | `sk-ant-` | Anthropic |
    | `sk-proj-` or `sk-` | OpenAI |
    | `AIza` | Google Gemini |
    | `gsk_` | Groq |
    | `sk-or-` | OpenRouter |
    | `xai-` | xAI |
    | `csk-` | Cerebras |

  - The model in use is printed when the harness starts. Before any task, one tiny request confirms that the model and the key work together.
- **Credential.** The only credential is `AI_API_KEY`.
  - It is read from the environment when a request is made and passed to LiteLLM.
  - It never enters the settings, is redacted from errors and evidence files, and is never echoed by the Makefile.
  - It is removed from the environment of every command the harness runs in the repository. That environment drops every variable whose name contains KEY, TOKEN, SECRET, PASSWORD, CREDENTIAL or AUTH.
  - `.env.example` documents the variable. No file in this repository holds or reads a key, and `make test` fails if one appears (rule R10 below).
- **Optional overrides, no file edits needed.**
  - `OLIVER_MODEL` replaces `model`.
  - `OLIVER_API_BASE` points to an OpenAI-compatible gateway.
  - `OLIVER_CONFIG` selects another configuration file.
- **Text only.** Prompts, tool calls and tool results are plain text. No image, audio or video input is used.

### Reproducibility

- **Decoding:** temperature 0.0 and seed 42 (sent where the provider supports seeding) are set in the configuration file.
- **Settings in one place:** every limit and phase parameter lives next to them in that file. Unknown keys and wrong value types are rejected, so a typo cannot silently change a run.
- **No other randomness:** the harness has none that affects results. Retry jitter only changes waiting time.
- **Pinned dependencies:** the direct dependencies are pinned to the versions the tests pass with.

## How it works

`run_agent_loop(task, repo_path, settings)` drives a state machine made of plain functions:

| Phase | What happens |
|---|---|
| INIT | Index the repository (tree-sitter symbols), detect the test command, run the **baseline** tests, and snapshot the tree in a shadow git repository kept outside the target |
| PLAN | The model explores with read-only tools and writes a JSON plan: root cause, files, steps, reproduction command |
| EXECUTE | The model edits with exact-match `edit_file` (edits that break syntax are rolled back automatically), runs tests and calls `finish` |
| VERIFY | The **harness** checks syntax, runs the reproduction command and compares the test run with the baseline; only new failures count |
| RECOVER | Classifies the failure and turns the trimmed traceback into a focused recovery prompt. After the same failure twice, it rolls back and replans |
| REVIEW | One short model critique of the verified diff against the task |

Limits on steps, tokens and wall time stop a run. If an earlier attempt passed verification, that checkpoint is restored and reported. Otherwise the run ends as NOT VERIFIED or FAILED. Long conversations are compressed without ever separating a tool call from its result.

| Module (`source-code/oliver/`) | Responsibility |
|---|---|
| `orchestrator.py` | Phase state machine, limits, final verdict |
| `foundation_model.py` | LiteLLM access behind the `OLIVER` alias, `AI_API_KEY`, retries, text tool protocol, mock backend |
| `context_manager.py` | Repository index, ranked repo map, working memory, context compression |
| `tools.py` | `read_file`, `write_file`, `edit_file`, `search_repo`, `list_files`, `run_command`, `run_tests`, `finish`, with path confinement and syntax guard |
| `verification.py` | Command execution (local or docker), test detection, JUnit parsing, baseline comparison |
| `recovery.py` | Failure classification, loop detection, shadow-git checkpoints and rollback |
| `prompts.py` | System, plan, recovery and review prompts |
| `config.py`, `console.py`, `main.py` | Configuration layers, the evaluation console, the command line |
| `report.py`, `report.css` | Evidence bundle and standalone HTML report |

## Repository layout

```text
.
├── Makefile                  setup / run / test / clean: the evaluation interface
├── README.md
├── .env.example              documents AI_API_KEY (no value)
├── source-code/
│   ├── oliver/               the harness (procedural Python)
│   ├── tests/                pytest suite and the constraint checker
│   └── eval/                 6 bundled tasks with hidden tests, and their runner
├── configuration-files/
│   ├── oliver.toml           model and run settings
│   └── pytest.ini            test-runner settings
└── dependency-files/
    └── requirements.txt      pinned Python dependencies
```

`make setup`, `make run` and `make test` generate `.venv/`, `runs/` and
`workspace/`. They are git-ignored and removed by `make clean`.

## Coding rules

- **Python:** purely procedural. No classes, no decorators, no meta-programming builtins, no instance attributes and no dictionary get-method calls.
- **Web output (the HTML report):** no CSS custom properties, no media queries, a fixed 960px layout and no timeline elements.
- **Credentials:** none may be hard-coded (rule R10).
- **Enforcement:** `source-code/tests/check_constraints.py` checks all of this across the whole repository as part of `make test`.

## Known limits

- **Command isolation.** Local command execution is confined by timeouts, a command blocklist and an environment with secrets removed, not by the filesystem. `sandbox = "docker"` adds container isolation, but that path has not been exercised in CI (no Docker daemon was available).
- **What the offline scores show.** The offline evaluation replays scripted trajectories. It shows that the harness mechanics work, not how good a model is. `make test REAL=1` gives real scores for the configured model.
