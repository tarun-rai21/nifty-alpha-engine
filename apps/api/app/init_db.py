import logging

from app.db import Base, engine
import app.models  # noqa: F401 — registers all ORM models with Base.metadata

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_db() -> None:
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Done. Tables created:")
    for table_name in sorted(Base.metadata.tables.keys()):
        logger.info("  %s", table_name)


if __name__ == "__main__":
    init_db()