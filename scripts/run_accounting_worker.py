"""Run only the explicit accounting worker configuration, never the legacy environment."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))
from app.jobs.worker import main

if __name__ == "__main__":
    raise SystemExit(main())
