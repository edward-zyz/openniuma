.PHONY: install dev test lint format clean

install:
	pip install -e .

dev:
	pip install -e ".[dev,tui]"

test:
	python -m pytest tests/ -v

lint:
	ruff check src/ tests/
	mypy src/openniuma/

format:
	ruff format src/ tests/

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
