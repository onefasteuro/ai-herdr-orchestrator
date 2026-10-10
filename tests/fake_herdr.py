#!/usr/bin/env python3
"""Fake herdr CLI for tests.

State lives in $FAKE_HERDR_STATE (JSON); every call is appended to
$FAKE_HERDR_LOG (JSON lines). `pane run "sh <script>"` executes the script
synchronously so headless dispatches complete end to end.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

STATE = Path(os.environ["FAKE_HERDR_STATE"])
LOG = Path(os.environ["FAKE_HERDR_LOG"])


def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"next": 2, "tabs": [{"tab_id": "w1:t1", "label": "1"}],
            "panes": [{"pane_id": "w1:p1", "tab_id": "w1:t1", "cwd": os.getcwd(),
                       "rect": {"width": 200, "height": 50}}],
            "agents": {}, "fail": {}, "foreground": {}}


def save(state):
    STATE.write_text(json.dumps(state, indent=2))


def out(obj):
    print(json.dumps(obj))
    return 0


def fail(code, message="failed"):
    print(json.dumps({"error": {"code": code, "message": message}}), file=sys.stderr)
    return 1


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def opts(args, name):
    return [args[i + 1] for i, a in enumerate(args) if a == name]


def pane(state, pane_id):
    return next((p for p in state["panes"] if p["pane_id"] == pane_id), None)


def new_pane(state, tab_id, cwd, env):
    pane_id = f"w1:p{state['next']}"
    state["next"] += 1
    p = {"pane_id": pane_id, "tab_id": tab_id, "cwd": cwd, "env": env,
         "rect": {"width": 100, "height": 50}}
    state["panes"].append(p)
    return p


def env_of(args):
    return dict(e.split("=", 1) for e in opts(args, "--env"))


def main(args):
    LOG.open("a").write(json.dumps(args) + "\n")
    state = load()
    key = " ".join(args[:2])
    forced = state["fail"].get(key)
    if forced:
        return fail(forced)

    if key == "pane list":
        return out({"result": {"panes": state["panes"]}})
    if key == "tab list":
        return out({"result": {"tabs": state["tabs"]}})
    if key == "tab create":
        tab_id = f"w1:t{state['next']}"
        state["next"] += 1
        state["tabs"].append({"tab_id": tab_id, "label": opt(args, "--label")})
        root = new_pane(state, tab_id, opt(args, "--cwd"), env_of(args))
        save(state)
        return out({"result": {"tab": {"tab_id": tab_id}, "root_pane": root}})
    if key == "pane split":
        target = pane(state, opt(args, "--pane"))
        if target is None:
            return fail("pane_not_found")
        p = new_pane(state, target["tab_id"], opt(args, "--cwd"), env_of(args))
        save(state)
        return out({"result": {"pane": p}})
    if key == "pane layout":
        target = pane(state, opt(args, "--pane"))
        panes = [p for p in state["panes"] if p["tab_id"] == target["tab_id"]]
        return out({"result": {"layout": {"panes": panes}}})
    if key == "pane rename":
        pane(state, args[2])["label"] = args[3]
        save(state)
        return out({"result": {}})
    if key == "pane process-info":
        # A pane listed in "foreground" shows its own shell (pid 100) as foreground.
        idle = opt(args, "--pane") in state["foreground"]
        procs = [{"name": "zsh", "pid": 100}] if idle else [{"name": "glow", "pid": 200}]
        return out({"result": {"process_info": {"shell_pid": 100, "foreground_processes": procs}}})
    if key == "pane close":
        if pane(state, args[2]) is None:
            return fail("pane_not_found")
        state["panes"] = [p for p in state["panes"] if p["pane_id"] != args[2]]
        # Like Herdr, a tab disappears with its last pane.
        live = {p["tab_id"] for p in state["panes"]}
        state["tabs"] = [t for t in state["tabs"] if t["tab_id"] in live]
        save(state)
        return out({"result": {}})
    if key == "pane read":
        print("fake pane output")
        return 0
    if key == "pane run":
        p = pane(state, args[2])
        command = args[3]
        argv = shlex.split(command)
        if len(argv) == 2 and argv[0] == "sh" and argv[1].endswith("/run.sh"):
            env = dict(os.environ, **(p.get("env") or {}))
            subprocess.run(argv, env=env, stdout=subprocess.DEVNULL)
        return out({"result": {}})
    if key == "agent start":
        name = args[2]
        native = args[args.index("--") + 1:] if "--" in args else []
        state["agents"][name] = {"pane": opt(args, "--pane"), "kind": opt(args, "--kind"),
                                 "native": native, "status": "idle"}
        save(state)
        return out({"result": {"agent": {"name": name}}})
    if key == "agent prompt":
        agent = state["agents"].get(args[2])
        if agent is None:
            return fail("agent_not_found")
        prompt_file = re.search(r"Read and follow (.+)\.$", args[3]).group(1)
        result = re.search(r"to `([^`]+)`", Path(prompt_file).read_text())
        if result and os.environ.get("FAKE_AGENT_WRITES", "1") == "1":
            Path(result.group(1)).write_text(f"RESULT from {agent['kind']}\n")
        return out({"result": {}})
    if key == "agent wait":
        return out({"result": {}})
    if key == "agent get":
        agent = state["agents"].get(args[2])
        if agent is None:
            return fail("agent_not_found")
        return out({"result": {"agent": {"agent_status": agent["status"]}}})
    return fail("unknown_command", key)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
