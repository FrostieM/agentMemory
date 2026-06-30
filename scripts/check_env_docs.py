"""Gate: every operator-facing settings env var is documented in .env.example.

The service is configured entirely through environment variables (the ``Settings``
model's ``validation_alias`` for each field). Those silently drifted ahead of the
``.env.example`` reference -- ~46 vars (incl. the async-write knobs) had no doc, so
an operator could not discover them. This gate FAILs (``--enforce``) when a settings
env var is missing from ``.env.example``; ``--write`` backfills the missing ones
with their default values so the reference stays complete.

    python scripts/check_env_docs.py --enforce   # CI gate
    python scripts/check_env_docs.py --write      # backfill missing vars
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"


def settings_env_vars() -> dict[str, str]:
    """Map each field's env alias -> its default value, formatted for .env."""
    from agent_memory_lite.config.settings import Settings  # noqa: PLC0415

    out: dict[str, str] = {}
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        if not isinstance(alias, str):
            continue  # only simple string aliases are operator env vars
        default = getattr(Settings(), name)
        out[alias] = _format(default)
    return out


def _format(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def documented_vars(env_path: Path) -> set[str]:
    if not env_path.exists():
        return set()
    text = env_path.read_text(encoding="utf-8")
    # A var is "documented" if it appears as NAME= (optionally commented).
    return set(re.findall(r"(?m)^#?\s*([A-Z][A-Z0-9_]+)=", text))


def _missing(env_path: Path) -> list[str]:
    declared = settings_env_vars()
    documented = documented_vars(env_path)
    return sorted(alias for alias in declared if alias not in documented)


def _write(env_path: Path) -> int:
    missing = _missing(env_path)
    if not missing:
        print(".env.example already documents every settings env var.")
        return 0
    declared = settings_env_vars()
    block = ["", "# --- Backfilled settings (auto: scripts/check_env_docs.py --write) ---"]
    block += [f"{alias}={declared[alias]}" for alias in missing]
    existing = env_path.read_text(encoding="utf-8").rstrip("\n")
    env_path.write_text(existing + "\n" + "\n".join(block) + "\n", encoding="utf-8")
    print(f"backfilled {len(missing)} env var(s) into {env_path.name}.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Settings env-var documentation gate.")
    parser.add_argument("--enforce", action="store_true", help="Exit non-zero on missing vars.")
    parser.add_argument(
        "--write", action="store_true", help="Backfill missing vars into .env.example."
    )
    args = parser.parse_args(argv)

    if args.write:
        return _write(ENV_EXAMPLE)

    missing = _missing(ENV_EXAMPLE)
    if missing:
        print(f"FAIL: {len(missing)} settings env var(s) missing from {ENV_EXAMPLE.name}:")
        for alias in missing:
            print(f"  {alias}")
        print("\nRun `python scripts/check_env_docs.py --write` to backfill them.")
        return 1 if args.enforce else 0
    print(f"OK: every settings env var is documented in {ENV_EXAMPLE.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
