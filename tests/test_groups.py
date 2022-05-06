from __future__ import annotations

from typing import List

import pytest

from rubedo import FrontendContext
from rubedo.enhanced_fields import Indexed
from rubedo.group import Group, group_class, search_fields, subgroup_class
from rubedo.model import ModelBase, rubedo_model

_ADDRESS1 = "Tel Aviv, Israel"


@rubedo_model("houses", "house")
class HouseModel:
    address: str


@rubedo_model("shelves", "shelf")
class ShelfModel:
    content: List[str]
    length: float


@rubedo_model("kitchens", "kitchen")
class KitchenModel:
    house: HouseModel
    shelves: List[ShelfModel]


@rubedo_model("books", "book")
class BookModel:
    title: Indexed(str)
    author: str
    description: str


@rubedo_model("dens", "den")
class DenModel:
    house: HouseModel
    books: List[BookModel]


@group_class(HouseModel)
class HouseGroup(Group):
    pass


@subgroup_class(HouseGroup, KitchenModel)
class KitchenGroup(Group):
    def get_big_kitchens(self) -> KitchenGroup:
        return self.shelves.where(ShelfModel.length >= 30).kitchens


@search_fields("content")
@subgroup_class(KitchenGroup, ShelfModel)
class ShelfGroup(Group):
    pass


@subgroup_class(HouseGroup, DenModel)
class DenGroup(Group):
    pass


@search_fields("title", "author", "description")
@subgroup_class(DenGroup, BookModel)
class BookGroup(Group):
    pass


def _add(context: FrontendContext, model: ModelBase) -> None:
    repo = type(model).repository_cls(context)
    repo.add_all(model)


def _get_uow(testing_context: FrontendContext):
    return HouseModel.repository_cls(testing_context).uow


def _add_stuff(testing_context: FrontendContext) -> None:
    house1 = HouseModel(address=_ADDRESS1)

    shelf1 = ShelfModel(content=["pasta", "quinoa"], length=20.5)
    shelf2 = ShelfModel(content=["flour"], length=30)
    shelf3 = ShelfModel(
        content=["blueberry", "blackberry", "raspberry"],
        length=50,
    )
    kitchen1 = KitchenModel(house=house1, shelves=[shelf1, shelf2, shelf3])

    book1 = BookModel(title="Huckleberry Finn", author="Mark Twain")
    den1 = DenModel(house=house1, books=[book1])

    with _get_uow(testing_context)():
        _add(testing_context, house1)
        _add(testing_context, kitchen1)
        _add(testing_context, den1)


@pytest.fixture(scope="module")
def frontend_context() -> FrontendContext:
    from rubedo.sqlsorcery import metadata

    context = FrontendContext("sqlite:///:memory:")
    metadata.create_all(context.sql_engine)

    _add_stuff(context)

    return context


@pytest.fixture
def houses(frontend_context) -> HouseGroup:
    return HouseGroup(frontend_context)


@pytest.fixture
def kitchens(frontend_context) -> KitchenGroup:
    return KitchenGroup(frontend_context)


@pytest.fixture
def dens(frontend_context) -> DenGroup:
    return DenGroup(frontend_context)


@pytest.fixture
def shelves(frontend_context) -> ShelfGroup:
    return ShelfGroup(frontend_context)


def test_hierarchy(
    houses: HouseGroup,
    kitchens: KitchenGroup,
    dens: DenGroup,
):
    assert (
        houses.count()
        == len(houses.where().kitchens.get_big_kitchens().houses.all())
        == 1
    )
    assert houses.dens.count() == dens.count()

    # Test long lines (long subqueries, for now each subquery queries the db)
    assert houses.count() == len(
        houses.kitchens.shelves.kitchens.shelves.kitchens.houses.dens.houses.all(),
    )


def test_search(houses: HouseGroup, shelves: ShelfGroup):
    (flour_shelf,) = shelves.where(length=30).all()
    shelf_search = shelves.search("flour")
    assert shelf_search.group.count() == 1
    assert shelf_search.group.first() == flour_shelf

    house_findings = houses.search("berry")
    assert house_findings.group.count() == 1
    assert house_findings.group.first().address == _ADDRESS1

    shelf_findings = house_findings.subresults.kitchens.subresults.shelves
    assert shelf_findings.group.count() == 1
    assert shelf_findings.group.all_content() == [
        "blueberry",
        "blackberry",
        "raspberry",
    ]

    book_findings = house_findings.subresults.dens.subresults.books
    assert book_findings.group.count() == 1
    book1 = book_findings.group.last()
    assert book1 == houses.dens.books.first()


def test_empty_search(houses, kitchens, dens, shelves):
    for root in [houses, kitchens, dens, shelves]:
        findings = root.search("this will produce no results")
        assert findings.group.count() == 0
        assert findings.group.all() == []
        # for value in findings.results.values():
        for key, value in findings.results.items():
            assert value.matches == dict()
            if (
                value.group is not None
            ):  # results from anonymous tables return a None group
                assert value.group.count() == 0
                assert value.group.all() == []


def test_all_content(kitchens: KitchenGroup):
    assert set(kitchens.shelves.all_content()) == {
        "pasta",
        "quinoa",
        "flour",
        "blueberry",
        "blackberry",
        "raspberry",
    }
