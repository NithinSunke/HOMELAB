"""Runtime settings, all overridable by environment variable."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = os.getenv("RMAN_CONSOLE_DB", str(BASE_DIR / "rman_console.db"))
# SQLAlchemy wants forward slashes even on Windows, where the configured
# path will normally contain backslashes and a drive letter.
DATABASE_URL = "sqlite:///" + DB_PATH.replace("\\", "/")

API_TOKEN = os.getenv("RMAN_CONSOLE_TOKEN", "")

# Host key verification. Point this at a known_hosts file containing every
# target. asyncssh rejects hosts that are not listed.
KNOWN_HOSTS = os.getenv(
    "RMAN_CONSOLE_KNOWN_HOSTS", str(Path.home() / ".ssh" / "known_hosts")
)

REMOTE_DIR = os.getenv("RMAN_CONSOLE_REMOTE_DIR", "/tmp/rman-console")

# How often the runner pulls new log bytes off the target, in seconds.
POLL_INTERVAL = float(os.getenv("RMAN_CONSOLE_POLL_INTERVAL", "2"))

# Give up on a job after this many hours of no completion.
JOB_TIMEOUT_HOURS = float(os.getenv("RMAN_CONSOLE_JOB_TIMEOUT_HOURS", "24"))
