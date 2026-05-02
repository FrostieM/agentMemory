"""Quick status check: Ollama, model, memory service, DB, agent configs.

python scripts/status.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from setup_agent import diagnose, print_diagnosis


def main() -> int:
    print_diagnosis(diagnose())
    return 0


if __name__ == "__main__":
    sys.exit(main())
