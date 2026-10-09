"""Version of the database schema and the steps that upgrade an older database.

create_all (in init_db) adds new tables and indexes, but cannot change existing tables or convert their data: such a
change gets an upgrade step, a function that brings the database from the previous version to its own, run once in one
transaction. Version 1 is the schema created by create_all when the versions were introduced: a database saved before
has no version and is at version 1 once create_all has run. A new database starts at the latest version. Before running
any step, the database file is copied next to it (degiro.db.bak-v{version}).
"""

import logging
import shutil
import time
from collections.abc import Callable

from sqlalchemy import Connection, Engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from portfolio_manager.config.accounts import Accounts
from portfolio_manager.storage.models import SchemaVersion
from portfolio_manager.storage.queries import query_benchmark, save_benchmark

logger = logging.getLogger(__name__)

BASELINE_VERSION = 1
# version -> step upgrading the database from the previous version
UPGRADE_STEPS: dict[int, Callable[[Connection], None]] = {}


def latest_version() -> int:
    """Version of the schema of this code."""
    return max(UPGRADE_STEPS, default=BASELINE_VERSION)


def schema_version(engine: Engine) -> int | None:
    """Version of the database schema, None if the database has none yet."""
    with engine.connect() as connection:
        return connection.execute(select(SchemaVersion.version)).scalar()


def _set_version(connection: Connection, version: int):
    stmt = sqlite_insert(SchemaVersion).values(id=1, version=version)
    connection.execute(stmt.on_conflict_do_update(index_elements=[SchemaVersion.id], set_={'version': version}))


def backup_database(engine: Engine, version: int) -> str:
    """Copy the database file next to it (degiro.db.bak-v{version}) and return the path of the copy."""
    path = engine.url.database
    backup_path = f'{path}.bak-v{version}'
    engine.dispose()  # no open connection while copying
    start = time.time()
    shutil.copyfile(path, backup_path)
    logger.info('Database copied to %s before upgrading it (%.0f s)', backup_path, time.time() - start)
    return backup_path


def upgrade_schema(engine: Engine, new_database: bool):
    """Run the upgrade steps the database misses, after copying it; record the version of a database without one."""
    latest = latest_version()
    version = schema_version(engine)
    if version is None:
        version = latest if new_database else BASELINE_VERSION
        with engine.begin() as connection:
            _set_version(connection, version)
    if version > latest:
        raise RuntimeError(
            f'The database has schema version {version}, newer than the version of this code ({latest}): '
            'run a newer version of the code'
        )
    pending = [v for v in sorted(UPGRADE_STEPS) if v > version]
    if not pending:
        return
    backup_database(engine, version)
    for step_version in pending:
        with engine.begin() as connection:
            UPGRADE_STEPS[step_version](connection)
            _set_version(connection, step_version)
        logger.info('Database upgraded to schema version %d', step_version)


def seed_default_settings():
    """Save the default benchmark of each account that has no saved settings (saved settings are never changed)."""
    for account in Accounts:
        if account.default_benchmark is not None and query_benchmark(account.name) is None:
            save_benchmark(account.name, account.default_benchmark)
