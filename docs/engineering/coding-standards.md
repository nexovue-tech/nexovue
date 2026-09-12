# Coding standards

These are current enforced conventions, extracted from code and tooling rather than imposed as new product requirements.

- Keep changes narrow and explain *why* in comments; migrations record their own reasoning.
- Use intention-revealing names, focused routines, low coupling, named constants rather than magic
  values, and deliberate invalid-input handling.
- Keep comments to the non-obvious reason in a line or two; restructure code that needs a long
  explanation of what it does.
- Configuration is explicit: required values fail boot by name; credentials have no code default.
- Use structured logs with shared correlation, trace, tenant, and project fields. Never log secrets.
- Generated code under `packages/contracts/gen` and vendored OTel demo code are committed but not hand-edited or linted as application code.
- Go: format with gofmt/goimports, wrap errors with `%w`, document exported identifiers.
- Python: async service paths, Ruff, timezone-aware datetimes, no service `print`, strict mypy.
- TypeScript: no explicit `any` or non-null assertions; no `console` outside the designated logger; use the shared API client and design tokens.

Where code goes, what each file in a domain or feature is for, and the patterns already in
use are in [code structure](code-structure.md).

Run `just fmt` to format and `just check` before handoff.
