#!/usr/bin/env python3
"""Fake claude/codex/pi CLI: logs argv and prints a canned result."""

import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["FAKE_AGENT_LOG"], "a") as log:
    log.write(json.dumps({"bin": name, "args": args, "cwd": os.getcwd(),
                          "consult": os.environ.get("ORCHESTRATOR_CONSULT")}) + "\n")
answer = f"RESULT from {name}"
if name == "codex" and "-o" in args:
    Path(args[args.index("-o") + 1]).write_text(answer + "\n")
else:
    print(answer)
sys.exit(int(os.environ.get("FAKE_AGENT_EXIT", "0")))
