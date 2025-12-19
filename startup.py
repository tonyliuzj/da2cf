import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import uvicorn


# Ensure the src/ directory is on sys.path so `da2cf` can be imported
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Load .env from project root so config, admin user, passwords, etc. are set.
load_dotenv(BASE_DIR / ".env")


if __name__ == "__main__":
    uvicorn.run(
        "da2cf.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
