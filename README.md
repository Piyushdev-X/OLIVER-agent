# OLIVER · AI Coding Harness

OLIVER turns one standardized foundation model into a careful software engineer. It reads an issue, finds the relevant code, makes a minimal change, and proves the change with tests before it calls the task done.

Built for the LCC × DevClub AI Coding Harness Hackathon: *same model, different harnesses.*

## How it works

`run_agent_loop(task, repo_path)` runs a state machine made of plain functions:

| Phase | What happens |
|---|---|
| INIT | Index the repository (tree-sitter symbols), detect the test command, run the **baseline** tests, and take a shadow-git snapshot |
| PLAN | The model explores with read-only tools and writes a JSON plan: root cause, files, steps, a reproduction command |
| EXECUTE | The model edits with exact-match `edit_file` (edits that break syntax are rolled back automatically), runs tests, and calls `finish` |
| VERIFY | The **harness** checks syntax, runs the reproduction command, and compares the test run with the baseline. Only new failures count |
| RECOVER | Classifies the failure, trims the traceback into a focused recovery prompt, and rolls back and replans when the same failure repeats |
| REVIEW | One short model critique of the verified diff against the task |

Budgets on steps, tokens and wall time end the run in FAILED and keep the best verified checkpoint. See [`docs/architecture.html`](docs/architecture.html).

## Quick start

```bash
pip install -r oliver/requirements.txt

# offline dry run with a scripted model
python3 -m oliver --repo eval/tasks/stats_average/repo --task-file eval/tasks/stats_average/issue.md \
  --model mock:eval/tasks/stats_average/mock_script.json

# real model: any LiteLLM model id sits behind the OLIVER alias
export OLIVER_MODEL=anthropic/claude-sonnet-4-5   # or openai/gpt-4o, hosted_vllm/..., etc.
python3 -m oliver --repo path/to/repo --task-file issue.md
```

Useful flags: `--tool-mode text` (for models without native tool calling), `--sandbox docker` (runs commands in a container with networking off), `--test-command`, `--max-steps`, `--max-tokens`, `--timeout`, `--no-review`.

Each run writes `runs/<id>/`: `patch.diff`, `trajectory.jsonl`, `verification.json`, `result.json`, `summary.md` and a standalone `report.html` ([sample](docs/sample-report.html)).

## Evidence

```bash
python3 -m pytest -q                 # 32 tests, offline
python3 eval/run_eval.py             # 6 toy tasks, scored by hidden tests
python3 scripts/check_constraints.py # hackathon coding rules
```

The offline eval uses scripted trajectories, so it shows the harness mechanics work (planning, the syntax guard, regression recovery, verification, reporting), **not** model quality. Run `eval/run_eval.py --real` with a real `OLIVER_MODEL` for real scores.

## Coding rules

Purely procedural code, no classes, no instance attributes, no dictionary get-method calls; web output with no CSS custom properties, no media queries, a fixed 960px layout, no timeline elements, and dedicated logo slots. `scripts/check_constraints.py` enforces all of this, and runs as a Claude Code hook and inside the test suite. Details are in [`CLAUDE.md`](CLAUDE.md) and [`docs/design/DESIGN_LANGUAGE.md`](docs/design/DESIGN_LANGUAGE.md).

## Known limits

- Local command execution is confined by timeouts, a command blocklist and an environment with secrets removed, not by the filesystem. Use `--sandbox docker` for isolation. The Docker path has not been exercised in CI (no daemon was available).
- chromadb semantic search is optional (`oliver/requirements-optional.txt`); lexical search plus the symbol index is the default.
