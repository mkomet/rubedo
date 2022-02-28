import contextlib
import re

from sqlalchemy import Index
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .sqlsorcery import metadata


def create_all(engine: Engine):
    # XXX Hack for MySQL Databases
    _SQLITE_EXPR_RE = re.compile(r"substr\(`(.*?)`, 1, ([\d]+)\)")
    if "mysql" in engine.dialect.name:
        for _, table in metadata.tables.items():
            indexes = set()
            # Copy the list of indices to the side, in order to mutate it
            old_indexes = list(table.indexes)
            for ix in old_indexes:
                if (
                    len(ix.expressions) == 1
                    and getattr(ix.expressions[0], "text", None) is not None
                ):
                    match = _SQLITE_EXPR_RE.match(ix.expressions[0].text)
                    if match is not None:
                        column_name, length = match.groups()
                        ix = Index(
                            ix.name,
                            getattr(table.c, column_name),
                            mysql_length=int(length),
                        )
                indexes.add(ix)
            table.indexes = indexes

    metadata.create_all(bind=engine)


def raw_sql_session(engine: Engine) -> Session:
    engine_session_maker = sessionmaker(bind=engine)
    session = engine_session_maker()
    return session


@contextlib.contextmanager
def sql_session(engine: Engine):
    session = raw_sql_session(engine=engine)
    try:
        yield session
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def build_sqlite_uri(db_path: str) -> str:
    return f"sqlite:///{db_path}"


def build_mysql_uri(user: str, remote_host: str, remote_port: int) -> str:
    return f"mysql+pymysql://{user}@{remote_host}:{remote_port}/rubedo"
