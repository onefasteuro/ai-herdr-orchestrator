import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ORCH = REPO / "bin" / "orchestrator"
TESTS = REPO / "tests"


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orch-test-")).resolve()
        self.project = self.tmp / "project dir's"
        self.project.mkdir()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        for name, src in [("herdr", "fake_herdr.py"), ("claude", "fake_agent.py"),
                          ("codex", "fake_agent.py"), ("pi", "fake_agent.py")]:
            target = TESTS / src
            target.chmod(0o755)
            (bindir / name).symlink_to(target)
        self.state = self.tmp / "herdr-state.json"
        self.herdr_log = self.tmp / "herdr-log.jsonl"
        self.agent_log = self.tmp / "agent-log.jsonl"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("ORCHESTRATOR_", "FAKE_"))}
        self.env.update(
            PATH=f"{bindir}:{os.environ['PATH']}", HERDR_ENV="1", HERDR_PANE_ID="w1:p1",
            HERDR_WORKSPACE_ID="w1", FAKE_HERDR_STATE=str(self.state),
            FAKE_HERDR_LOG=str(self.herdr_log), FAKE_AGENT_LOG=str(self.agent_log),
            GIT_CEILING_DIRECTORIES=str(self.tmp))
        self.brief = self.project / "brief.md"
        self.brief.write_text("# T1\n\nDo the thing.\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    # helpers

    def run_orch(self, *args, env=None, check=True):
        proc = subprocess.run([sys.executable, str(ORCH), *args], cwd=self.project,
                              env=dict(self.env, **(env or {})), capture_output=True, text=True)
        if check and proc.returncode != 0:
            self.fail(f"orchestrator {args} exited {proc.returncode}: {proc.stderr}")
        return proc

    def herdr_calls(self):
        if not self.herdr_log.exists():
            return []
        return [json.loads(line) for line in self.herdr_log.read_text().splitlines()]

    def agent_calls(self):
        if not self.agent_log.exists():
            return []
        return [json.loads(line) for line in self.agent_log.read_text().splitlines()]

    def calls(self, group, cmd):
        return [c for c in self.herdr_calls() if c[:2] == [group, cmd]]

    def herdr_state(self):
        return json.loads(self.state.read_text())

    def set_state(self, **changes):
        state = {"next": 2, "tabs": [{"tab_id": "w1:t1", "label": "1"}],
                 "panes": [{"pane_id": "w1:p1", "tab_id": "w1:t1", "cwd": str(self.project),
                            "rect": {"width": 200, "height": 50}}],
                 "agents": {}, "fail": {}, "foreground": {}}
        state.update(changes)
        self.state.write_text(json.dumps(state))

    def dispatch(self, role, *extra, check=True, env=None):
        proc = self.run_orch("dispatch", role, "--brief", str(self.brief), *extra, check=check, env=env)
        return proc, (json.loads(proc.stdout) if proc.stdout.strip() else None)

    # Tasks pane

    def test_tasks_pane_opens_right_at_40_percent(self):
        self.run_orch("tasks-pane")
        split = self.calls("pane", "split")
        self.assertEqual(split, [["pane", "split", "--pane", "w1:p1", "--direction", "right",
                                  "--ratio", "0.6", "--cwd", str(self.project), "--no-focus"]])
        self.assertEqual(self.calls("pane", "rename"), [["pane", "rename", "w1:p2", "Tasks"]])
        run = self.calls("pane", "run")
        self.assertEqual(len(run), 1)
        self.assertEqual(run[0][:3], ["pane", "run", "w1:p2"])
        argv = shlex.split(run[0][3])
        self.assertEqual(argv[:2], ["sh", "-c"])
        self.assertEqual(argv[2], 'until [ -f "$1" ]; do sleep 1; done; exec glow -t "$1"')
        self.assertEqual(argv[3:], ["tasks", str(self.project / "TASKS.md")])

    def test_tasks_pane_reused_when_busy(self):
        self.run_orch("tasks-pane")
        self.herdr_log.unlink()
        out = json.loads(self.run_orch("tasks-pane").stdout)
        self.assertEqual(out, {"status": "reused", "pane": "w1:p2"})
        self.assertEqual(self.calls("pane", "split"), [])
        self.assertEqual(self.calls("pane", "run"), [])

    def test_tasks_pane_restarted_when_shell_idle(self):
        self.run_orch("tasks-pane")
        state = self.herdr_state()
        state["foreground"] = {"w1:p2": ["-zsh"]}
        self.state.write_text(json.dumps(state))
        self.herdr_log.unlink()
        out = json.loads(self.run_orch("tasks-pane").stdout)
        self.assertEqual(out["status"], "restarted")
        self.assertEqual(self.calls("pane", "split"), [])
        self.assertEqual(len(self.calls("pane", "run")), 1)

    # Headless dispatch

    def test_headless_reviewer_runs_in_new_agents_tab(self):
        proc, out = self.dispatch("reviewer", "--task", "T1")
        self.assertEqual(out["status"], "done")
        create = self.calls("tab", "create")
        self.assertEqual(len(create), 1)
        self.assertIn("--no-focus", create[0])
        self.assertEqual(create[0][create[0].index("--label") + 1], "agents")
        self.assertIn("ORCHESTRATOR_DEPTH=1", create[0])
        codex = self.agent_calls()[0]
        self.assertEqual(codex["bin"], "codex")
        args = codex["args"]
        self.assertEqual(args[0], "exec")
        self.assertEqual(args[args.index("-m") + 1], "gpt-6-astra")
        self.assertIn("model_reasoning_effort=high", args)
        self.assertEqual(args[args.index("-s") + 1], "read-only")
        result = Path(out["result"])
        self.assertEqual(result.read_text(), "RESULT from codex\n")
        self.assertTrue(str(result).startswith(str(self.project / ".orchestrator" / "tasks" / "T1")))
        self.assertEqual((self.project / ".orchestrator" / ".gitignore").read_text(), "*\n")
        prompt = (result.parent / "prompt.md").read_text()
        self.assertIn("# Reviewer", prompt)
        self.assertIn("Do the thing.", prompt)
        self.assertEqual(len(self.calls("pane", "close")), 1)

    def test_agents_tab_recreated_after_last_pane_closes(self):
        self.dispatch("reviewer", "--task", "T1")
        self.dispatch("advisor", "--task", "T2")
        self.assertEqual(len(self.calls("tab", "create")), 2)

    def test_second_dispatch_reuses_agents_tab(self):
        self.dispatch("reviewer", "--task", "T1", "--keep-pane")
        self.herdr_log.unlink()
        _, out = self.dispatch("advisor", "--task", "T2")
        self.assertEqual(out["status"], "done")
        self.assertEqual(self.calls("tab", "create"), [])
        split = self.calls("pane", "split")
        self.assertEqual(len(split), 1)
        self.assertIn("--no-focus", split[0])

    def test_claude_headless_read_only_and_stdout_result(self):
        _, out = self.dispatch("researcher", "--task", "R1")
        self.assertEqual(out["status"], "done")
        args = self.agent_calls()[0]["args"]
        self.assertEqual(args[0], "-p")
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.assertEqual(args[args.index("--advisor") + 1], "opus")
        self.assertEqual(args[args.index("--tools") + 1], "Read,Grep,Glob,WebSearch,WebFetch")
        self.assertEqual(Path(out["result"]).read_text(), "RESULT from claude\n")

    def test_failed_headless_keeps_pane(self):
        proc, out = self.dispatch("reviewer", "--task", "T1", check=False, env={"FAKE_AGENT_EXIT": "3"})
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["exit_code"], 3)
        self.assertEqual(self.calls("pane", "close"), [])

    def test_override_kind_and_model(self):
        _, out = self.dispatch("reviewer", "--task", "T1", "--kind", "claude", "--model", "opus",
                               "--effort", "max")
        self.assertEqual((out["kind"], out["model"], out["effort"]), ("claude", "opus", "max"))
        self.assertEqual(self.agent_calls()[0]["bin"], "claude")

    # Interactive dispatch

    def test_interactive_implementer(self):
        _, out = self.dispatch("implementer", "--task", "I1")
        self.assertEqual(out["status"], "done")
        start = self.calls("agent", "start")[0]
        self.assertEqual(start[start.index("--kind") + 1], "pi")
        native = start[start.index("--") + 1:]
        self.assertEqual(native, ["--model", "ollama-cloud/deepseek-v4.1-flash", "--thinking", "high"])
        prompt = self.calls("agent", "prompt")[0]
        self.assertIn("--wait", prompt)
        self.assertEqual(Path(out["result"]).read_text(), "RESULT from pi\n")
        prompt_md = (Path(out["dir"]) / "prompt.md").read_text()
        self.assertIn("orchestrator consult", prompt_md)
        self.assertEqual(self.calls("pane", "close"), [])

    def test_interactive_without_result_is_no_result(self):
        proc, out = self.dispatch("debugger", "--task", "D1", check=False, env={"FAKE_AGENT_WRITES": "0"})
        self.assertEqual(out["status"], "no_result")
        self.assertEqual(proc.returncode, 1)

    def test_interactive_blocked_at_start(self):
        self.set_state(fail={"agent start": "agent_not_ready"})
        proc, out = self.dispatch("implementer", "--task", "I1", check=False)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(self.calls("agent", "prompt"), [])

    def test_no_wait_then_wait(self):
        _, out = self.dispatch("implementer", "--task", "I1", "--no-wait")
        self.assertEqual(out["status"], "running")
        self.assertNotIn("--wait", self.calls("agent", "prompt")[0])
        out = json.loads(self.run_orch("wait", "I1").stdout)
        self.assertEqual(out["status"], "done")

    # Guards

    def test_subagent_cannot_dispatch(self):
        proc, _ = self.dispatch("reviewer", check=False, env={"ORCHESTRATOR_DEPTH": "1"})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("may not dispatch", proc.stderr)
        self.assertEqual(self.herdr_calls(), [])

    def test_duplicate_task_id_refused(self):
        self.dispatch("reviewer", "--task", "T1")
        proc, _ = self.dispatch("reviewer", "--task", "T1", check=False)
        self.assertIn("already exists", proc.stderr)

    def test_outside_herdr_refused(self):
        proc, _ = self.dispatch("reviewer", check=False, env={"HERDR_ENV": ""})
        self.assertIn("not running inside a Herdr pane", proc.stderr)
        self.assertFalse((self.project / ".orchestrator").exists())

    def test_dry_run_creates_nothing(self):
        proc, out = self.dispatch("reviewer", "--task", "T1", "--dry-run")
        self.assertEqual(out["status"], "dry-run")
        self.assertIn("+ herdr tab create", proc.stderr)
        self.assertIn("+ codex exec", proc.stderr)
        self.assertEqual(self.herdr_calls(), [])
        self.assertEqual(self.agent_calls(), [])
        self.assertFalse((self.project / ".orchestrator").exists())

    def test_dispatch_rejects_root_role(self):
        proc, _ = self.dispatch("orchestrator", check=False)
        self.assertIn("cannot be dispatched", proc.stderr)

    # Consult

    def test_consult_uses_read_only_claude_and_limit(self):
        tdir = self.tmp / "task"
        tdir.mkdir()
        env = {"ORCHESTRATOR_TASK_DIR": str(tdir), "ORCHESTRATOR_DEPTH": "1"}
        for _ in range(3):
            proc = self.run_orch("consult", "why", "does", "it", "fail?", env=env)
            self.assertEqual(proc.stdout.strip(), "RESULT from claude")
        call = self.agent_calls()[0]
        self.assertEqual(call["consult"], "1")
        args = call["args"]
        self.assertEqual(args[args.index("--model") + 1], "fable")
        self.assertEqual(args[args.index("--tools") + 1], "Read,Grep,Glob")
        self.assertIn("why does it fail?", args[1])
        proc = self.run_orch("consult", "again", env=env, check=False)
        self.assertIn("limit reached", proc.stderr)
        self.assertIn("Q: why does it fail?", (tdir / "consults.md").read_text())

    def test_consult_with_codex_advisor(self):
        proc = self.run_orch("consult", "--role", "advisor", "is", "this", "plan", "sound?")
        self.assertEqual(proc.stdout.strip(), "RESULT from codex")

    def test_consult_cannot_nest(self):
        proc = self.run_orch("consult", "x", env={"ORCHESTRATOR_CONSULT": "1"}, check=False)
        self.assertIn("cannot be nested", proc.stderr)

    def test_consult_refuses_write_role(self):
        proc = self.run_orch("consult", "--role", "implementer", "x", check=False)
        self.assertIn("read-only role", proc.stderr)

    # TASKS.md section

    def tasks_md(self):
        return (self.project / "TASKS.md").read_text()

    def test_dispatch_creates_tasks_md_section(self):
        self.dispatch("reviewer", "--task", "T1")
        text = self.tasks_md()
        self.assertTrue(text.startswith("# Tasks\n\n<!-- orchestrator:tasks:start -->"))
        self.assertIn("| Task | Role | Agent | Status | Updated | Result |", text)
        row = [l for l in text.splitlines() if l.startswith("| T1 ")][0]
        self.assertIn("| reviewer | codex gpt-6-astra high | done |", row)
        self.assertIn("[result](.orchestrator/tasks/T1/result.md)", row)
        self.assertTrue(text.rstrip().endswith("<!-- orchestrator:tasks:end -->"))
        self.assertEqual((self.project / "TASKS.md").stat().st_mode & 0o044, 0o044)
        self.assertEqual([p.name for p in self.project.glob(".TASKS.md.*")], [])

    def test_tasks_md_preserves_other_content(self):
        (self.project / "TASKS.md").write_text("# Plan\n\n- [ ] T1 review\n")
        self.dispatch("reviewer", "--task", "T1")
        self.dispatch("advisor", "--task", "T2")
        text = self.tasks_md()
        self.assertTrue(text.startswith("# Plan\n\n- [ ] T1 review\n\n<!-- orchestrator:tasks:start -->"))
        self.assertEqual(text.count("orchestrator:tasks:start"), 1)
        self.assertIn("| T1 | reviewer |", text)
        self.assertIn("| T2 | advisor |", text)

    def test_tasks_md_section_replaced_in_place(self):
        (self.project / "TASKS.md").write_text(
            "# Plan\n\n<!-- orchestrator:tasks:start -->\nstale\n<!-- orchestrator:tasks:end -->\n\n## Notes\n\nkeep me\n")
        self.dispatch("reviewer", "--task", "T1")
        text = self.tasks_md()
        self.assertNotIn("stale", text)
        self.assertTrue(text.endswith("<!-- orchestrator:tasks:end -->\n\n## Notes\n\nkeep me\n"))
        self.assertTrue(text.startswith("# Plan\n\n<!-- orchestrator:tasks:start -->"))

    def test_tasks_md_tracks_running_then_done(self):
        self.dispatch("implementer", "--task", "I1", "--no-wait")
        row = [l for l in self.tasks_md().splitlines() if l.startswith("| I1 ")][0]
        self.assertIn("| running |", row)
        self.run_orch("wait", "I1")
        row = [l for l in self.tasks_md().splitlines() if l.startswith("| I1 ")][0]
        self.assertIn("| done |", row)

    def test_tasks_md_failure_only_warns(self):
        (self.project / "TASKS.md").mkdir()
        proc, out = self.dispatch("reviewer", "--task", "T1")
        self.assertEqual(out["status"], "done")
        self.assertIn("warning: TASKS.md not updated", proc.stderr)

    def test_symlinked_tasks_md_is_not_followed(self):
        victim = self.tmp / "victim.txt"
        victim.write_text("precious\n")
        (self.project / "TASKS.md").symlink_to(victim)
        proc, out = self.dispatch("reviewer", "--task", "T1")
        self.assertEqual(out["status"], "done")
        self.assertIn("warning: TASKS.md not updated", proc.stderr)
        self.assertEqual(victim.read_text(), "precious\n")
        self.assertTrue((self.project / "TASKS.md").is_symlink())

    def test_symlinked_lock_is_not_truncated(self):
        victim = self.tmp / "victim.txt"
        victim.write_text("precious\n")
        state = self.project / ".orchestrator"
        state.mkdir()
        (state / "tasks.lock").symlink_to(victim)
        proc, out = self.dispatch("reviewer", "--task", "T1")
        self.assertEqual(out["status"], "done")
        self.assertEqual(victim.read_text(), "precious\n")

    def test_symlinked_state_dir_refused(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.project / ".orchestrator").symlink_to(elsewhere)
        proc, _ = self.dispatch("reviewer", "--task", "T1", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("is a symlink", proc.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(self.herdr_calls(), [])

    def test_dry_run_leaves_tasks_md_alone(self):
        self.dispatch("reviewer", "--task", "T1", "--dry-run")
        self.assertFalse((self.project / "TASKS.md").exists())

    # Install

    def test_install_links_skill_and_command(self):
        home = self.tmp / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".pi" / "agent").mkdir(parents=True)
        for _ in range(2):  # idempotent
            proc = subprocess.run(["sh", str(REPO / "install.sh")], env=dict(self.env, HOME=str(home)),
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((home / ".agents/skills/orchestrator").resolve(), REPO / "skills/orchestrator")
        self.assertEqual((home / ".claude/skills/orchestrator/SKILL.md").resolve(),
                         REPO / "skills/orchestrator/SKILL.md")
        self.assertEqual((home / ".pi/agent/skills/orchestrator/SKILL.md").resolve(),
                         REPO / "skills/orchestrator/SKILL.md")
        self.assertEqual((home / ".local/bin/orchestrator").resolve(), ORCH)

    def test_install_refuses_to_replace_real_files(self):
        home = self.tmp / "home"
        (home / ".agents/skills/orchestrator").mkdir(parents=True)
        proc = subprocess.run(["sh", str(REPO / "install.sh")], env=dict(self.env, HOME=str(home)),
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a symlink", proc.stderr)

    # Start, status, close

    def test_start_dry_run_shows_root_command(self):
        proc = self.run_orch("--dry-run")
        self.assertIn("+ herdr pane split --pane w1:p1 --direction right --ratio 0.6", proc.stderr)
        line = [l for l in proc.stderr.splitlines() if l.startswith("+ claude")][0]
        for part in ("--model opus", "--effort xhigh", "--advisor fable", "--append-system-prompt"):
            self.assertIn(part, line)
        self.assertIn("--append-system-prompt '# Orchestrator", line)
        self.assertNotIn("name: orchestrator", proc.stderr)
        self.assertEqual(self.herdr_calls(), [])

    def test_start_with_codex_root(self):
        proc = self.run_orch("start", "--dry-run", "--no-tasks-pane", "--kind", "codex",
                             "--model", "gpt-6-astra", "--", "--search")
        line = [l for l in proc.stderr.splitlines() if l.startswith("+ codex")][0]
        self.assertIn("-m gpt-6-astra", line)
        self.assertIn("developer_instructions=", line)
        self.assertTrue(line.endswith("--search"))
        self.assertNotIn("+ herdr", proc.stderr)

    def test_status_and_close(self):
        self.dispatch("implementer", "--task", "I1")
        status = self.run_orch("status").stdout
        self.assertIn("I1", status)
        self.assertIn("done", status)
        self.run_orch("close", "I1")
        self.assertEqual(len(self.calls("pane", "close")), 1)
        self.assertIn("closed", self.run_orch("status").stdout)
        self.assertIn("already closed", self.run_orch("close", "I1").stdout)


if __name__ == "__main__":
    unittest.main()
