# Contributing to openNiuMa

## Quick Setup

```bash
git clone https://github.com/edward-zyz/openniuma.git
cd openniuma
pip install -e ".[dev,tui]"
python -m pytest tests/ -v
```

## Commit Convention

```
feat: / fix: / docs: / refactor: / test: / chore:
```

## Prompt Contributions

`src/openniuma/prompts/` is a core asset. Changes to prompts must include:

1. **Motivation** — what problem does this solve?
2. **Validation** — which project did you test on?
3. **Before/after** — how did AI behavior change?
4. **Full task log** — at least one complete execution log

## RFC Process

Major changes (schema changes, new phases, security model) require:

1. Open an RFC Issue with the `rfc` label
2. Maintainer discussion (at least 1 week)
3. Approval from at least 1 Maintainer
4. Implementation

## Error Message Guidelines

User-visible errors must include:
1. What happened
2. Likely cause
3. Suggested fix

## Code Style

- Formatter: `ruff format`
- Linter: `ruff check`
- Type checker: `mypy`
- Target Python: 3.10+
- Line length: 100
- All source files must have `# SPDX-License-Identifier: MIT` header

## Testing

```bash
python -m pytest tests/ -v
python -m pytest tests/ --cov=openniuma --cov-report=term-missing
```
