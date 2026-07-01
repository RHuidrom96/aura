import os
import secrets
import logging

from config import DATA_DIR

logger = logging.getLogger(__name__)


def load_secret_key():
    """Use the env var if set; otherwise persist a generated key to data/secret_key
    so sessions survive restarts and multiple workers share the same key."""
    env_key = os.environ.get("MT_EVAL_SECRET_KEY")
    if env_key:
        return env_key
    key_file = DATA_DIR / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    try:
        key_file.write_text(key)
        logger.warning(
            "MT_EVAL_SECRET_KEY not set; generated and saved one to %s. "
            "For production, set MT_EVAL_SECRET_KEY explicitly.", key_file)
    except Exception:
        logger.exception("Could not persist generated secret key")
    return key
