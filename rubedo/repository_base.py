from __future__ import annotations

import abc
import dataclasses
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Type

from .model_base import ModelBase
from .utils import RubedoDict


class ViewBase(abc.ABC):
    """
    Base class for all views - representing a set of some instances of a model,
    and supporting methods to mutate the set in a backend agnostic way.
    Stands as a "handle" a repository gives to a group.
    A view should also implement the `all_XXX()` methods for every field of the model class.
    """

    @abc.abstractmethod
    def all(self) -> List[ModelBase]:
        """
        Retrieve a list of all the instances represented by this view.
        """
        raise NotImplementedError()

    def all_pk(self) -> List[int]:
        """
        Retrieve a list of all the pk's of the instances represented by this view.
        """
        return [model.pk for model in self.all()]

    @abc.abstractmethod
    def where(self, *args, **kwargs) -> ViewBase:
        """
        Create a new view, filtering instances in this view according to the arguments.
        .. note::
            The arguments are not backend agnostic.

        :return: The new filtered view.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def count(self) -> int:
        """
        Return the number of element in this view.

        .. warning::
            Under some backends (Sqlalchemy based ones), this number may not equal to `len(self.all())`
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def first(self) -> ModelBase:
        """
        Return the first element of this view
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def last(self) -> ModelBase:
        """
        Return the last element of this view
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def union(self, views: Iterable[ViewBase]) -> ViewBase:
        """
        Unite this view with `views`, creating a new view representing all the instances
        that appeared in any of the united views
        :param views: iterable of other views to unite with.
        :return: A new united view.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def limit(self, count: int) -> ViewBase:
        """
        Create a new view, limiting the number of elements in this view to `count`.
        :param count: maximum amount of elements in the new view.
        :return: A new view with limited element count.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def __getitem__(self, item):
        raise NotImplementedError()


@dataclasses.dataclass
class RepositorySearchFieldResult:
    matches: Dict[int, Any]
    view: ViewBase


@dataclasses.dataclass
class RepositorySearchResult:
    matching_pks: List
    results: RubedoDict[str, RepositorySearchFieldResult]


class RepositoryBase(abc.ABC):
    """
    Base class of all repositories - abstracting saving and retrieving models from any backend in a standard API.
    """

    MODEL_CLS = None

    def __init__(self, context):
        self._context = context

    @abc.abstractmethod
    def view(self, pks: List = None) -> ViewBase:
        """
        Return a view of all the elements in this repository,
        optionally filtering to only elements whose pk is in `pks`.
        :param pks: list of pk to create the view from.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def search(
        self,
        view: ViewBase,
        pattern: str,
        search_fields: List[str],
    ) -> RepositorySearchResult:
        """
        Search a view for a pattern contained in any field of the `search_fields`
        :param view: the view to search on.
        :param pattern: the pattern to search for.
        :param search_fields: list of fields of the model class to search in.

        :return: The results of the search
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def add(self, obj: ModelBase) -> None:
        """
        Add a model instance to the repository, saving it persistently.
        :param obj: the model instance to add.
        """
        raise NotImplementedError()

    def add_all(self, obj: ModelBase) -> None:
        """
        Add a model to this repository and all it's submodels to the corrosponding repository.
        :param obj: the model instance to add.
        """
        self.add(obj)
        for field in dataclasses.fields(obj):
            value = getattr(obj, field.name)
            if isinstance(value, list):
                if value and isinstance(value[0], ModelBase):
                    repo = value[0].repository_cls(self._context)
                    for item in value:
                        repo.add_all(item)
            elif isinstance(value, ModelBase):
                repo = value.repository_cls(self._context)
                repo.add_all(value)

    @abc.abstractmethod
    def remove(self, obj: ModelBase) -> None:
        """
        Remove a model instance from the repository, deleting it forever :(
        :param obj: the model instance to remove.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def build_submodel_view(
        self,
        view: ViewBase,
        field_name: str,
        submodel_cls: Type[ModelBase],
    ) -> ViewBase:
        """
        Build a new view, representing all the values of model.<field_name> for models of `view`.
        :param view: The view to build from
        :param field_name: Name of a field of type `submodel_cls` to build on.
        :param submodel_cls: The type of model.<field_name>.
        :return: A new view, of objects with type `submodel_cls`, representing the values of submodel.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def build_supermodel_view(
        self,
        view: ViewBase,
        field_name: str,
        super_model_cls: Type[ModelBase],
    ) -> ViewBase:
        """
        Build a new view, representing all the values of model.<field_name> for models of `view`.
        :param view: The view to build from
        :param field_name: Name of a field of type `super_model_cls` to build on.
        :param super_model_cls: The type of model.<field_name>.
        :return: A new view, of objects with type `super_model_cls`, representing the values of super model.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    @contextmanager
    def uow(self):
        """
        A unit of work for this repository.
        """
        # TODO: have one uow per backend and not per repository
        raise NotImplementedError()
