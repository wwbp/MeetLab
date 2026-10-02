import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.url import database_url, pool_options

_DATABASE_URL = database_url(os.environ)

engine = create_async_engine(_DATABASE_URL, pool_pre_ping=True, **pool_options(os.environ))

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)
