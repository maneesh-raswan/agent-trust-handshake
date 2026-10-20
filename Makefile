.PHONY: setup phase1-demo phase1-test phase2-demo phase2-test test clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev,phase2]"

# Phase 1: in-process handshake. Needs only `cryptography`.
phase1-demo:
	.venv/bin/python -m phase1.demo

phase1-test:
	.venv/bin/pytest -q tests/phase1

# Phase 2: Registry, Producer, Consumer as A2A services, MCP tool servers, autonomous run.
phase2-demo:
	.venv/bin/python -m phase2.runner

phase2-test:
	.venv/bin/pytest -q tests/phase2

test:
	.venv/bin/pytest -q

clean:
	rm -rf .venv .pytest_cache build *.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
