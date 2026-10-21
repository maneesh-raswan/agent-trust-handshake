.PHONY: setup phase1-demo phase1-test phase2-demo phase2-test phase3-demo phase3-demo-stub phase3-test test clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev,phase2,phase3]"

# Phase 1: in-process handshake. Needs only `cryptography`.
phase1-demo:
	.venv/bin/python -m phase1.demo

phase1-test:
	.venv/bin/pytest -q tests/phase1

# Phase 2: Registry, Bureau Core and source agents as A2A services, MCP tool servers, autonomous run.
phase2-demo:
	.venv/bin/python -m phase2.runner

phase2-test:
	.venv/bin/pytest -q tests/phase2

# Phase 3: LLM brains on the source side. The live demo calls a real model and needs credentials
# (see .env.example); the stub demo uses scripted stand-in brains and needs nothing.
phase3-demo:
	.venv/bin/python -m phase3.runner

phase3-demo-stub:
	.venv/bin/python -m phase3.runner --stub

phase3-test:
	.venv/bin/pytest -q tests/phase3

test:
	.venv/bin/pytest -q

clean:
	rm -rf .venv .pytest_cache build *.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
