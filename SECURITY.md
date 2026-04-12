# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Prax, please email 543370794@qq.com.

**Do not** open a public issue for security vulnerabilities.

We will respond within 48 hours and work with you to understand and fix the issue.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Security Best Practices

When using Prax:

1. **API Keys**: Store in environment variables, never commit to git
2. **Permission Modes**: Use `read-only` for untrusted tasks
3. **Sandboxing**: Consider Docker isolation for high-risk operations
4. **Input Validation**: Prax validates tool inputs against their declared schemas before execution
5. **Workspace Boundaries**: File-writing tools are blocked from modifying paths outside the active workspace unless `danger-full-access` is explicitly enabled
6. **Shell Review**: Schema validation does not make shell commands safe by itself; review destructive commands before allowing them
