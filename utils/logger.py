import logging
from pathlib import Path

LOG_FILE = Path("logs") / "bot.log"


def setup_logging() -> None:
    """Configure application-wide logging handlers."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
