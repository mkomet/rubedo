from sqlalchemy.engine import create_engine

from .sqlsorcery import raw_sql_session


class FrontendContext:
    def __init__(
        self,
        sql_uri: str,
    ):
        self.sql_engine = create_engine(sql_uri)
        self.sql_session = raw_sql_session(self.sql_engine)
