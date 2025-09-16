import asyncpg
from asyncpg.pool import Pool
import logging

logger = logging.getLogger(__name__)

# Глобальная переменная для пула соединений
db_pool: Pool = None

async def init_db(database_url):
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(database_url, ssl='require', min_size=1, max_size=10)
        logger.info("Database pool created successfully")
        return db_pool
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise

async def close_db():
    """Закрытие пула соединений с базой данных"""
    try:
        if db_pool:
            await db_pool.close()
            logger.info("Database pool closed successfully")
    except Exception as e:
        logger.error(f"Error closing database pool: {e}")
