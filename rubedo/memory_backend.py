from __future__ import annotations
import dataclasses
import functools
import operator

from typing import List, Type, Iterable
from contextlib import contextmanager
from .repository_base import (
    RepositoryBase,
    ViewBase,
    RepositorySearchResult,
    RepositorySearchFieldResult,
)
from .model_base import ModelBase
from .field_descriptors import InstrumentedFieldBase, FieldComparator
from .backend_base import BackendBase
from .utils import RubedoDict


class MemoryField(InstrumentedFieldBase):
    pass


class MemoryView(ViewBase):
    def __init__(self, repo: MemoryRepositoryBase, pks: List = None):
        self._repo = repo
        self._items: List[ModelBase] = list()
        if pks is None:
            self._items = list(repo.get_items())
        else:
            for pk in pks:
                item = repo.get_items()[pk]
                if item is not None:
                    self._items.append(item)
        self.__create_columns_alls()

    def __getitem__(self, item):
        return self._items.__getitem__(item)

    def all(self) -> List[ModelBase]:
        with self._repo.uow():
            return list(self._items)

    def __create_columns_alls(self):
        model_cls = self._repo._model_cls

        def get_field_factory(field_name):
            def get_func(self):
                with self._repo.uow():
                    return [getattr(model, field_name) for model in self._items]

            return get_func

        def get_field_use_list_factory(field_name):
            def get_func(self):
                with self._repo.uow():
                    fields = [getattr(model, field_name) for model in self._items]
                    return list(set().union(*fields))

            return get_func

        for field in dataclasses.fields(model_cls):
            origin = getattr(field.type, "__origin__", None)
            if origin is not None and issubclass(origin, List):
                get_func = get_field_use_list_factory(field.name)
            else:
                get_func = get_field_factory(field.name)
            bound_get_func = get_func.__get__(self, type(self))
            setattr(self, f"all_{field.name}", bound_get_func)

    def union(self, views: Iterable[MemoryView]) -> MemoryView:
        pks = self.all_pk()
        for view in views:
            for pk in view.axll_pk():
                if pk not in pks:
                    pks.append(pk)
        return type(self)(self._repo, pks=pks)

    def count(self) -> int:
        return len(self._items)

    def first(self) -> ModelBase:
        return self._items[0]

    def last(self) -> ModelBase:
        return self._items[-1]

    def where(self, *args, **kwargs) -> MemoryView:
        def base_filter(_):
            return True

        filter_ = base_filter

        # args handling:
        args: List[FieldComparator]

        # and between all the comparators
        if args:
            filter_ = functools.reduce(operator.and_, args)
            # get the evaluation function itself:
            filter_ = filter_.evaluate

        # kwargs handling:
        for key, value in kwargs.items():
            # XXX eagerly evalute filter_ to prevent infinite loop
            def new_filter(model, filter_=filter_):
                return getattr(model, key) == value and filter_(model)

            filter_ = new_filter

        pks = [model.pk for model in self._items if filter_(model)]
        return type(self)(self._repo, pks=pks)

    def limit(self, count: int) -> MemoryView:
        pks = self.all_pk()[:count]
        return type(self)(self._repo, pks=pks)


class MemoryRepositoryBase(RepositoryBase):
    _items: List = None

    def __init__(self, context):
        super().__init__(context)

        # this repository keeps the list of all items added as a class variable (_items)
        # check if it the list was already initialized for this subclass, and if not create a new list:
        if type(self)._items is None:
            type(self)._items = []

    def __init_subclass__(cls, model_cls=None, **kwargs):
        super().__init_subclass__(**kwargs)
        if model_cls is None:
            raise TypeError()
        cls._model_cls = model_cls

    def get_items(self) -> List[ModelBase]:
        return type(self)._items

    def _verify_obj(self, obj: ModelBase):
        if not isinstance(obj, self._model_cls):
            raise TypeError(f"model {type(obj)} is not valid for this repository")

    def add(self, obj: ModelBase):
        self._verify_obj(obj)
        pk = getattr(obj, "pk")
        if pk is None:
            obj.pk = len(self.get_items())
            self.get_items().append(obj)
        else:
            self.get_items()[pk] = obj

    def remove(self, obj: ModelBase) -> None:
        self._verify_obj(obj)
        pk = getattr(obj, "pk")
        if pk is None:
            raise ValueError(f"model {obj} was never added to the repository")
        self.get_items()[pk] = None  # keep something at this index so pks won't change

    def view(self, pks: List = None) -> MemoryView:
        return MemoryView(self, pks=pks)

    def search(
        self,
        view: MemoryView,
        pattern: str,
        search_fields: List[str],
    ) -> RepositorySearchResult:
        results = RubedoDict()
        matching_pks = []
        for field_name in search_fields:
            field = getattr(self._model_cls, field_name, None)
            if field is None:
                raise RuntimeError()
            result_view = view.where(field.contains(pattern))
            matches = dict(
                (model.pk, getattr(model, field_name)) for model in result_view.all()
            )
            matching_pks += matches.keys()
            results[field_name] = RepositorySearchFieldResult(
                matches=matches,
                view=result_view,
            )
        return RepositorySearchResult(matching_pks, results)

    @contextmanager
    def uow(self):
        yield

    def build_supermodel_view(
        self,
        view: MemoryView,
        field_name: str,
        super_model_cls: Type[ModelBase],
    ) -> MemoryView:
        repo = super_model_cls.repository_cls(None)
        pks = []
        for model in view.all():
            pks.append(getattr(model, field_name).pk)
        return MemoryView(repo, pks=pks)

    def build_submodel_view(
        self,
        view: MemoryView,
        field_name: str,
        submodel_cls: Type[ModelBase],
    ) -> MemoryView:
        repo = submodel_cls.repository_cls(None)
        pks = []
        for model in view.all():
            for submodel in getattr(model, field_name):
                if submodel.pk not in pks:
                    pks.append(submodel.pk)
        return MemoryView(repo, pks=pks)


class MemoryBackend(BackendBase):
    def __init__(self, model_cls: Type[ModelBase]):
        self._model_cls = model_cls

    def _add_pk(self):
        model_cls = self._model_cls

        orig_init = model_cls.__init__

        @functools.wraps(model_cls.__init__)
        def new_init(self, *args, pk: int = None, **kwargs):
            self.pk = pk
            orig_init(self, *args, **kwargs)

        model_cls.__init__ = new_init
        MemoryField.create(self._model_cls, "pk")

    def create_repository(self) -> Type[RepositoryBase]:
        if "pk" not in [field.name for field in dataclasses.fields(self._model_cls)]:
            self._add_pk()

        for field in dataclasses.fields(self._model_cls):
            MemoryField.create(self._model_cls, field.name)

        # TODO: add field descriptor for backreffed fields
        # for _ in self.__backref__:
        #     pass

        class MemoryRepository(MemoryRepositoryBase, model_cls=self._model_cls):
            pass

        return MemoryRepository

    @classmethod
    def initialize_backend(cls, context):
        pass
