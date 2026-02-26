"""SQLAlchemy async engine and session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import Settings


def create_db_engine(settings: Settings):
    """Create an async SQLAlchemy engine."""
    return create_async_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the engine."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
