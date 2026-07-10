.PHONY: test lint typecheck check

test:
	uv run python -m pytest --tb=short -q

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

typecheck:
	uv run mypy --package verdx

check: lint typecheck test
