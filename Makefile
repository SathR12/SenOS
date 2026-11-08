.DEFAULT_GOAL := help
export NAME UNIT TEXT INTENT DB KEEP DURATION ARGS
.PHONY: help demo demo-offline demo-status proof results demo-stop status register remove ask apply prioritize restore cli test

help:
	@bash scripts/run-python.sh scripts/commands.py help

demo:
	@bash scripts/web-demo.sh --mode ai --adaptive

demo-offline:
	@bash scripts/web-demo.sh --mode deterministic

demo-status proof results demo-stop status register remove ask apply prioritize restore cli:
	@bash scripts/run-python.sh scripts/commands.py $@

test:
	@bash scripts/run-python.sh -m pytest tests/unit

.PHONY: terminal-app terminal-build terminal-cleanup terminal-status terminal-ai terminal-apply
terminal-app:
	@bash scripts/demo/start-app.sh
terminal-build:
	@bash scripts/demo/start-build.sh
terminal-cleanup:
	@bash scripts/demo/cleanup.sh
terminal-status:
	@bash scripts/run-python.sh scripts/demo/control.py status
terminal-ai:
	@bash scripts/run-python.sh scripts/demo/control.py ai
terminal-apply:
	@bash scripts/run-python.sh scripts/demo/control.py apply

.PHONY: game game-ai
game:
	@bash scripts/web-demo.sh --game --mode deterministic
game-ai:
	@bash scripts/web-demo.sh --game --mode ai --adaptive

.PHONY: adapt
adapt:
	@bash scripts/run-python.sh -c 'import os; from senos.cli import app; app(["agent", "run", os.environ.get("TEXT", ""), "--watch", "--adaptive", "--apply", "--interval", "10", "--max-cycles", "12"])'
