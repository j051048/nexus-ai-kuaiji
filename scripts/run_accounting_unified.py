"""Run the unified assistant/queue/migration worker with explicit configuration."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))
from app.unified_worker import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
