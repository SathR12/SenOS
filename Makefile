.DEFAULT_GOAL := help
export NAME UNIT TEXT INTENT DB KEEP DURATION ARGS
.PHONY: help demo demo-offline demo-status proof results demo-stop status register remove ask apply prioritize restore cli test

help:
	@bash scripts/run-python.sh scripts/commands.py help

demo:
	@bash scripts/web-demo.sh --mode ai

demo-offline:
	@bash scripts/web-demo.sh --mode deterministic

demo-status proof results demo-stop status register remove ask apply prioritize restore cli:
	@bash scripts/run-python.sh scripts/commands.py $@

test:
	@bash scripts/run-python.sh -m pytest tests/unit
