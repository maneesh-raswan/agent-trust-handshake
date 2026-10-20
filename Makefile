.PHONY: setup test demo clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest -q

demo:
	.venv/bin/python demo.py

clean:
	rm -rf .venv .pytest_cache build *.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
