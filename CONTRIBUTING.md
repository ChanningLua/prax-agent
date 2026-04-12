# Contributing to Prax

Thank you for contributing to Prax! This guide covers development setup, code standards, and the PR process.

---

## Contribution Priorities

We value contributions in this order:

1. **Bug fixes** — crashes, incorrect behavior, data loss
2. **Cross-platform compatibility** — Windows, macOS, Linux
3. **Security hardening** — injection vulnerabilities, privilege escalation
4. **Performance and robustness** — retry logic, error handling
5. **Documentation** — fixes, clarifications, examples
6. **New features** — discuss in an issue first

---

## Development Setup

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.10+** | Required |
| **Git** | For cloning and version control |

### Clone and install

```bash
git clone https://github.com/ChanningLua/prax-agent.git
cd prax

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

### Run tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_agent_loop.py

# Run with coverage
pytest --cov=core --cov=tools --cov=agents --cov=commands --cov=workflows

# Run only unit tests (skip evals that call real APIs)
pytest -m "not eval"
```

---

## Code Standards

### Style

- **Formatter/Linter**: Ruff
- **Type hints**: Required for public APIs

```bash
# Lint
ruff check core/ tools/ agents/ commands/ workflows/ tui/ runtime/ integrations/ tests/
```

### Testing

- **Unit tests** for core logic (core/, runtime/)
- **Integration tests** for agent loops and workflows
- **Eval tests** (marked with `@pytest.mark.eval`) for real LLM API calls

New features must include tests. Bug fixes should include a regression test.

---

## PR Process

1. **Fork** the repository
2. **Create a branch** (`git checkout -b fix-memory-leak`)
3. **Make changes** and add tests
4. **Run tests** (`pytest`)
5. **Lint and format** (`ruff check ...` and your editor/formatter setup)
6. **Commit** with clear message (`git commit -m "Fix memory leak in session store"`)
7. **Push** (`git push origin fix-memory-leak`)
8. **Open PR** on GitHub

### PR checklist

- [ ] Tests pass (`pytest`)
- [ ] Code linted/formatted (`ruff check ...`)
- [ ] Type hints added for new public APIs
- [ ] Documentation updated (if adding features)
- [ ] CHANGELOG.md updated (if user-facing change)

---

## Project Structure

```
prax/
├── core/              # Agent loop, tools, memory
├── agents/            # Ralph, Sisyphus, Team
├── tools/             # Built-in tools
├── commands/          # Slash command handlers
├── workflows/         # Task orchestration
├── runtime/           # NativeRuntime entry point
├── integrations/      # Claude Code integration
├── assets/            # Claude Code assets
├── cli.py             # prax CLI commands
├── main.py            # prax CLI entry point
├── tests/             # Test suite
│   ├── unit/          # Unit tests
│   ├── integration/   # Integration tests
└── docs/              # Architecture notes, benchmark reports, and release docs
```

---

## Security Considerations

If you discover a security vulnerability, please email 543370794@qq.com instead of opening a public issue.

When contributing code that handles:
- User input
- Shell commands
- File paths
- API keys

Please ensure proper validation and sanitization.

---

## Questions?

- Open an issue for bugs or feature requests
- Join discussions in GitHub Discussions
- Check existing issues before creating new ones

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
