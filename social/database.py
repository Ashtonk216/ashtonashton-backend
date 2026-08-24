from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def ensure_profile(user_id: str, db: AsyncSession) -> None:
    """Lazily create a profiles row the first time a user is seen."""
    await db.execute(
        text(
            "INSERT INTO profiles (user_id) VALUES (:user_id) "
            "ON CONFLICT (user_id) DO NOTHING"
        ),
        {"user_id": user_id},
    )
    await db.commit()
