# OLIVER-agent

OLIVER is an autonomous planner-executor coding harness (LCC × DevClub AI Coding Harness Hackathon). It wraps one foundation model, called through LiteLLM under the alias `OLIVER`, and resolves software tasks with verified patches.

## Commands

- Tests: `python3 -m pytest -q` (offline; uses the scripted mock model)
- Constraint check: `python3 scripts/check_constraints.py` (whole repo) or pass files or directories
- Eval: `python3 eval/run_eval.py` (mock) or `OLIVER_MODEL=<litellm id> python3 eval/run_eval.py --real`
- Run: `python3 -m oliver --repo PATH --task-file ISSUE.md [--model mock:SCRIPT.json]`

## Hard coding rules (enforced by a PostToolUse hook and by the tests)

1. Purely procedural Python: no class definitions, no decorators (including pytest fixtures), no meta-programming builtins (`exec`, `eval`, `setattr`, `getattr`, three-argument `type`).
2. No instance-attribute access anywhere. The checker bans the four-letter receiver word in any case, in any file, including prose, so avoid words like "itself" and "yourself".
3. No dictionary get-method calls: check `key in d`, then index with brackets. The checker also rejects any dotted get-prefixed call and any `get` followed by an opening parenthesis, so names ending in "get" (for example "target" or "budget" followed by a call) are out too. For environment variables use `os.environ[name]` after an `in` check.
4. Web output (HTML, CSS, SVG, and HTML built in Python): no CSS custom properties and no var function, no media-query rules, a rigid 960px layout, and no timeline elements. Colors and sizes come from `docs/design/DESIGN_LANGUAGE.md`.
5. The retired project name must not appear anywhere; the product is OLIVER.

## Layout

- `oliver/`: orchestrator (phase state machine), foundation_model (LiteLLM, text protocol, mock), context_manager, tools, verification, recovery (shadow-git checkpoints), prompts, report, main
- `eval/tasks/<name>/`: `repo/`, `issue.md`, `hidden_tests/`, `mock_script.json`
- `docs/`: `architecture.html`, `design/` (stylesheet, skeleton, spec), `sample-report.html`
- `runs/`: run artifacts (gitignored)

## Conventions

- Plain dicts carry all state; model responses are converted to dicts in `foundation_model.py`.
- Tools return strings; errors start with `ERROR:` and say how to fix the call.
- Every tool call gets exactly one tool result message.
