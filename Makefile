.PHONY: test lint typecheck check acceptance

test:
	uv run --locked python -m pytest --tb=short -q

lint:
	uv run --locked ruff check src/ tests/ examples/
	uv run --locked ruff format --check src/ tests/ examples/

typecheck:
	uv run --locked mypy --package verdx

check: lint typecheck test

acceptance:
	uv run --locked verdx check . --output text
