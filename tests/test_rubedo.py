from __future__ import annotations

from typing import List, Tuple

import pytest
from sqlalchemy.orm import Session

from rubedo import rubedo_model

_DEFAULT_BYTES = b"wah-ne day more"
_DEFAULT_STR = "mario... mario!! mario!!!!?!?"

_NAME = "silent bob"
_TITLE = "kevin smith"


@rubedo_model("cornucopias", "cornucopias")
class Cornucopia:
    """
    A Cornucopia of value types!
    """

    flag: bool
    num: int
    percent: float
    data: bytes = _DEFAULT_BYTES
    name: str = _DEFAULT_STR


@rubedo_model("wrappers", "wrapper")
class Wrapper:
    """
    Wrapper is a bad ass class
    """

    title: str
    cornucopias: List[Cornucopia]
    s: List[int]


@rubedo_model("sushi", "sush")
class Sushi:
    title: str
    cornucopias: List[Cornucopia]


@rubedo_model("dimensions", "dimension")
class Dimension:
    name: str
    sub_dimensions: List[Dimension]


def test_names():
    """
    Test that all auto-generated table and
    column names are as expected
    """
    module = Wrapper.__module__.replace(".", "_")
    assert Wrapper.__tablename__ == f"{module}_wrappers"
    assert Sushi.__tablename__ == f"{module}_sushi"
    assert (
        Wrapper.Cornucopia.__tablename__
        == Sushi.Cornucopia.__tablename__
        == f"{module}_cornucopias"
    )

    assert (
        Wrapper.pk.name
        == Wrapper.Cornucopia.pk.name
        == Sushi.pk.name
        == Sushi.Cornucopia.pk.name
        == "pk"
    )

    foreign_keys = set(fk.target_fullname for fk in Cornucopia.__table__.foreign_keys)
    assert foreign_keys == {f"{module}_wrappers.pk", f"{module}_sushi.pk"}


def test_docs():
    assert Wrapper.__doc__.strip() == "Wrapper is a bad ass class"
    assert Wrapper.Cornucopia.__doc__.strip() == "A Cornucopia of value types!"

    assert Sushi.__doc__ is None
    assert Sushi.Cornucopia.__doc__.strip() == "A Cornucopia of value types!"


def test_db_usage(sql_session: Session):
    """
    Test for insertion and querying
    all supported types with `sqlsorcery.sqlmodel`

    :param sql_session: SQL session with the db
    """
    # TODO: When two MANY-TO-ONE relationships are defined on a table, we want to make sure at least on of the
    # foreign pks is not NULL (issue #88)
    wrapper = Wrapper()
    c1 = Wrapper.Cornucopia(name=_NAME)
    c1.num = 0x1337
    c2 = Wrapper.Cornucopia(percent=0.67)
    c3 = Wrapper.Cornucopia(flag=True)

    wrapper.cornucopias.extend([c1, c2, c3])
    wrapper.title = _TITLE
    sql_session.add(wrapper)
    sql_session.commit()

    (wrapper,) = sql_session.query(Wrapper).all()
    assert wrapper.title == _TITLE
    assert wrapper.pk == 1

    c1, c2, c3 = wrapper.cornucopias
    assert c2.name == c3.name == _DEFAULT_STR
    assert c1.data == c2.data == c3.data == _DEFAULT_BYTES

    assert c1.name == _NAME
    assert c1.num == 0x1337
    assert c1.flag is c1.percent is None

    assert c2.percent == 0.67
    assert c2.flag is c2.num is None

    assert c3.flag
    assert c3.num is c3.percent is None


def test_error_class():
    """
    Test that a class with an usupported type
    (Tuple[Cornucopia]), raises an exception
    """

    with pytest.raises(TypeError):

        @rubedo_model("tablename", "member")
        class ErrorClass1:
            cornucopias: Tuple[Cornucopia]


def test_self_referencing(sql_session: Session):
    previous_dimensions = []
    for i in range(5):
        dimension = Dimension(name=f"dim_{i}", sub_dimensions=previous_dimensions)
        previous_dimensions = [dimension]

    sql_session.add(dimension)
    sql_session.commit()
    (dim_4,) = sql_session.query(Dimension).where(Dimension.name == "dim_4").all()

    previous_dimension = dim_4
    for i in range(5, 0, -1):
        assert previous_dimension.name == f"dim_{i - 1}"
        if i > 1:
            (previous_dimension,) = previous_dimension.sub_dimensions
