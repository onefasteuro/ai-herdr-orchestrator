# orchestrator

Run spec-driven development with one root agent and role-specific subagents (Claude Code, Codex, Pi) inside [Herdr](https://herdr.dev).

- The root agent runs in your current pane.
- `TASKS.md` renders live with Glow in a `Tasks` pane on the right, taking 40% of the tab.
- Every subagent runs in one shared `agents` tab, so the main tab stays quiet.
- Results come back as files. Herdr state never proves that a task is done; the root agent decides.

Requirements: Herdr (run inside a Herdr pane), Python 3.9+, `glow`, and whichever of `claude`, `codex` and `pi` your roles use.

## Install

```bash
./install.sh
```

It links `~/.local/bin/orchestrator` to `bin/orchestrator` and the `orchestrator` skill into `~/.agents/skills` (read by Codex), `~/.claude/skills` and `~/.pi/agent/skills`. It is safe to rerun and never replaces a real file.

## Usage

```bash
orchestrator                      # = orchestrator start: open the Tasks pane, run the root agent here
orchestrator start --kind codex --model gpt-6-astra -- <extra native args>
orchestrator tasks-pane           # open or reuse the Tasks pane only

orchestrator dispatch reviewer --brief briefs/T3-review.md --task T3-review
orchestrator dispatch implementer --brief briefs/T3.md --task T3 --no-wait
orchestrator wait T3
orchestrator status
orchestrator read T3              # recent pane output
orchestrator close T3

orchestrator consult "Why does this test hang under pytest-xdist?" --files tests/test_io.py
orchestrator roles
```

Add `--dry-run` to `start`, `dispatch`, `consult` or `tasks-pane` to print the herdr and agent commands without running anything.

`dispatch` prints one JSON object with `status` (`done`, `failed`, `blocked`, `no_result`, `timeout`, `running`), the agent identity, the pane and the `result` path.

## Roles

`roles.json` maps each role to a CLI, model, effort, mode and access level. Subagent instructions live in `roles/<role>.md`, and `roles/common.md` applies to every subagent. The root agent's instructions are the `orchestrator` skill (`skills/orchestrator/SKILL.md`): `orchestrator start` passes its body as the system prompt, and an agent started any other way finds it as a skill.

| Role | CLI | Model | Effort | Mode | Access |
|---|---|---|---|---|---|
| orchestrator | claude | opus + `--advisor fable` | xhigh | interactive (your pane) | write |
| researcher | claude | sonnet + `--advisor opus` | medium | headless | read-only, web |
| advisor | codex | gpt-6-astra | xhigh | headless | read-only |
| implementer | pi | ollama-cloud/deepseek-v4.1-flash | high | interactive | write |
| reviewer | codex | gpt-6-astra | high | headless | read-only |
| debugger | claude | opus + `--advisor fable` | xhigh | interactive | write |
| consultant | claude | fable | high | used by `consult` | read-only |

Override per dispatch with `--kind`, `--model`, `--effort` and `--mode`. Changing `--kind` without `--model` falls back to that CLI's default model. Point `ORCHESTRATOR_ROLES` at another file to swap the whole table.

Advice comes in three forms:

- **Mid-turn, native:** Claude roles pass `--advisor <model>`, so Claude Code consults a stronger Claude model inside the same turn. The advisor must be at least as capable as the main model, and its advice is not readable.
- **Mid-turn, any CLI:** write roles may run `orchestrator consult "<question>"` (at most 3 per task, read-only, cannot nest). It uses the `consultant` role by default, or `--role advisor` for Codex.
- **Phase gate:** the root agent dispatches `advisor` on the spec and plan for a cross-family second opinion with a written record.

## TASKS.md

The Tasks pane renders `TASKS.md` at the checkout root. The root agent keeps its plan there, and `orchestrator` owns one section between `<!-- orchestrator:tasks:start -->` and `<!-- orchestrator:tasks:end -->`: a `Dispatched tasks` table (task, role, agent, status, last update, result link) rebuilt from the task records whenever a task record changes. It creates the file when missing, appends the section when absent, and leaves everything outside the markers unchanged. A failed update only prints a warning.

## How a dispatch runs

1. `dispatch` refuses to run inside a subagent (`ORCHESTRATOR_DEPTH` is set in every subagent pane).
2. It creates `.orchestrator/tasks/<task>/` at the checkout root (`.orchestrator/.gitignore` ignores everything) with `brief.md`, `prompt.md` (role rules plus brief) and `meta.json`.
3. It finds or creates the `agents` tab with `--no-focus`, splitting the largest pane right when wide and down when tall.
4. Headless roles run `run.sh` in that pane: the CLI's final message becomes `result.md`, the exit code goes to `exit`, and the pane closes after success (`--keep-pane` keeps it).
5. Interactive roles start through `herdr agent start`, get the prompt through `herdr agent prompt --wait`, and must write `result.md` themselves. Their panes stay open until `orchestrator close`.

A `timeout` or `no_result` does not prove the agent did nothing: run `orchestrator read <task>` before dispatching the same work again under a new task id.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use `tests/fake_herdr.py` and `tests/fake_agent.py`; they never touch the live Herdr session or call a model.
