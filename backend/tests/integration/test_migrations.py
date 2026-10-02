import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from insights.db.models import Base

pytestmark = pytest.mark.integration


def test_migration_upgrade_indexes_constraints_and_downgrade(postgres_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    async def verify():
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:

            def check(sync):
                inspector = inspect(sync)
                assert set(Base.metadata.tables) <= set(inspector.get_table_names())
                for table in Base.metadata.sorted_tables:
                    actual = {c["name"]: c for c in inspector.get_columns(table.name)}
                    assert set(actual) == set(table.columns.keys())
                    for column in table.columns:
                        assert actual[column.name]["nullable"] == column.nullable
                        if column.server_default:
                            assert actual[column.name]["default"] is not None
                    indexes = {i["name"] for i in inspector.get_indexes(table.name)}
                    assert {index.name for index in table.indexes} <= indexes
                    for fk in inspector.get_foreign_keys(table.name):
                        assert fk["options"]["ondelete"] in {"CASCADE", "SET NULL"}
                assert any(
                    set(u["column_names"]) == {"repo_id", "number"}
                    for u in inspector.get_unique_constraints("pull_requests")
                )
                assert any(
                    set(u["column_names"])
                    == {
                        "snapshot_id",
                        "audience",
                        "lang",
                        "prompt_version",
                        "model_id",
                        "pack_hash",
                    }
                    for u in inspector.get_unique_constraints("narratives")
                )

            await connection.run_sync(check)
        await engine.dispose()

    asyncio.run(verify())
    command.downgrade(config, "base")

    async def verify_empty():
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            names = await connection.run_sync(lambda c: inspect(c).get_table_names())
            assert set(names) <= {"alembic_version"}
        await engine.dispose()

    asyncio.run(verify_empty())
    command.upgrade(config, "head")
