import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from rubedo.sqlsorcery import metadata


@pytest.fixture(scope="module")
def engine() -> Engine:
    """
    Create and setup an sqlite-in-memory engine
    """

    engine = create_engine("sqlite:///:memory:")
    # Create all of the tables that were declared using
    # sqlsorcery's DeclaritiveBase for ORM
    metadata.create_all(engine)
    # `engine` doesn"t manage a connection itself
    return engine


@pytest.fixture(scope="module")
def sql_session(engine: Engine) -> Session:
    """
    Create and return an sqlalchemy ORM session
    :param engine: The SQL engine to create bind a session to
    """

    engine_session_maker = sessionmaker(bind=engine)
    session = engine_session_maker()
    yield session
    session.close()
