# AGENTS

## Scope and entrypoint
- This repo is a single interactive Python CLI app; runtime logic is in `src/RivetRook.py`, data/config in `src/config.json`.
- Default startup reads `src/config.json`; you can pass an alternate config path as argv[1] (`python src/RivetRook.py path/to/config.json`).

## Run and verify
- Main local run: `python src/RivetRook.py` (or `python3 src/RivetRook.py` on Linux/macOS).
- Windows helper launcher: `src/Execute_RivetRook.bat` (it can install Python and PowerShell 7, then starts `RivetRook.py` in `pwsh`).
- There is no test/lint/typecheck config or CI in this repo; validation is manual CLI smoke testing.

## Config contract (high impact)
- Tool definitions live in `config.json` under `tools` and `ides`; menu order follows JSON key order.
- `resolve_command()` only resolves `all`, `windows`, `macos`, `linux`, and Linux family keys (e.g. `debian`/`fedora`/`arch`/`default`). Keys like `all_alt`/`linux_alt` are ignored by runtime.
- `run` is required for install detection. Version detection uses `version_cmd`/`version_regex` when present; otherwise probes `--version`, `-v`, `version`.
- Use `skip_version_probe: true` for GUI apps that should not be launched during detection.
- If `uninstall` is omitted, uninstall is inferred from `install` only for known patterns (`winget`, `brew`, `npm`, `bun`, `apt-get`, `dnf`/`yum`, `pacman`).

## Strings and UX changes
- Any user-facing text should come from `config.json` i18n keys via `_t(...)`; update both `i18n.pt-br` and `i18n.en`.
- Keep comments/docstrings in English in source code.

## Platform behavior to preserve
- Windows command execution is via PowerShell (`pwsh` preferred); do not replace with `cmd` semantics when editing command runners.
- `needs_git` enforcement only applies on Windows (with optional winget Git install flow).
- npm global install/upgrade paths include Node major-version gating (v20+) and npm user-prefix remediation; avoid bypassing this flow for npm-based tools.
