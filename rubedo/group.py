from __future__ import annotations

import dataclasses
from typing import List, Type

from .model import SUPER_MODELS, ModelBase
from .repository_base import RepositoryBase, ViewBase


class Group:
    def __init__(
        self,
        context,
        view: ViewBase = None,
    ):
        self._context = context
        self._repo: RepositoryBase = self._MODEL_CLS.repository_cls(context)
        if view is not None:
            self._view = view
        else:
            self._view = self._repo.view()

    def __dir__(self) -> List[str]:
        extra_keys = []
        if self._SUPER_GROUP_NAME is not None:
            extra_keys.append(self._SUPER_GROUP_NAME)

        return list(type(self)._SUBGROUPS.keys()) + extra_keys + super().__dir__()

    def __getattribute__(self, key):
        if key in type(self)._SUBGROUPS:
            # TODO: Do more lolz
            return self._build_subgroup(key)
        elif (
            type(self)._SUPER_GROUP_NAME is not None
            and key == type(self)._SUPER_GROUP_NAME
        ):
            return self._build_super_group()

        return super().__getattribute__(key)

    def __getitem__(self, slice):
        return self._view.__getitem__(slice)

    def _find_related_group_field_name(self, other_group, is_subgroup: bool) -> str:
        """
        When building a super/sub group, the field name doesn't always match the group's name.
        This function searches for the real field name.

        :param is_subgroup: whethe the group, is a subgroup (True) or a super_group (False)
        """
        # TODO: cache the results / or use the backrefs lists
        other_model = other_group._MODEL_CLS
        for field_name, enhanced_result in self._MODEL_CLS.__enhancedfields__.items():
            if enhanced_result.unwrapped_type == other_model:
                return field_name

        # not found in this group's model fields, so must it be defined in the other group's model.
        if is_subgroup:
            # in this case, the backref always uses the plural name of the subgroup's model.
            return other_model.__membersname__
        else:
            # in this case, the backref always uses the singular name of the super group's model.
            return other_model.__membername__

    def _build_subgroup(self, name: str) -> Group:
        subgroup_cls = type(self)._SUBGROUPS[name]
        field_name = self._find_related_group_field_name(subgroup_cls, is_subgroup=True)
        submodel_cls = subgroup_cls._MODEL_CLS
        view = self._repo.build_submodel_view(self._view, field_name, submodel_cls)
        return subgroup_cls(self._context, view=view)

    def _build_super_group(self) -> Group:
        super_group_cls = type(self)._SUPER_GROUP
        super_model_cls = super_group_cls._MODEL_CLS
        field_name = self._find_related_group_field_name(
            super_group_cls,
            is_subgroup=False,
        )
        view = self._repo.build_supermodel_view(self._view, field_name, super_model_cls)
        return super_group_cls(self._context, view=view)

    def count(self) -> int:
        """
        :return: Return a count of results this Group holds.

        .. warning::
            Under some backends (Sqlalchemy based ones), this number may not equal to `len(self.all())`
        """
        return self._view.count()

    def all(self) -> List[ModelBase]:
        """
        Perform the underlying view.all(), and return a list of the Models

        :return: List of results
        """
        return self._view.all()

    def first(self) -> ModelBase:
        """
        Return the first result

        :return: The first result, or ``None`` if no results apply
        """
        return self._view.first()

    def last(self) -> ModelBase:
        """
        Return the last result

        :return: The last result, or ``None`` if no results apply
        """
        return self._view.last()

    def union(self, *others) -> Group:
        """
        :param others: Other group(s) to union to
        :type others: :class:`Iterator[Group]`

        :return: Unioned group (doesn't modify ``self`` or ``other``)
        """
        view = self._view.union(other._view for other in others)
        return type(self)(
            context=self._context,
            view=view,
        )

    # TODO: support regexes
    def search(self, pattern: str) -> SearchResults:  # noqa: F821
        """
        Search recursively for a pattern contained in any string field
        (specified by the :func:`~rubedo.group.search_fields` class decorator),
        and any subgroup.

        Example:

        .. code-block:: python

            >>> cupboards.search('everything')
            # This will search in: (coarse example, this isn't the precise structure - some fields are omitted)
            #   cupboard
            #       shelves
            #           shelf_name
            #           canned_foods
            #               name
            #               can_type
            #               ...
            #       drawers
            #           drawer_name
            #           content
            #               ...

        .. warning::

            For now doesn't support regular expressions, only performs the following:

            .. code-block:: sql

                ... LIKE '%<pattern>%'

        :param pattern: The pattern to search for

        :return: The compounded recursive search results
        :rtype: :py:class:`rubedo.search.SearchResults`
        """
        from .common import Dict
        from .search import Result, SearchResults

        matching_pks = []
        # Get subgroups' search results
        subresults = Dict()
        for field, subgroup in self._SUBGROUPS.items():
            subresults[field] = getattr(self, field).search(pattern)
        if subresults:
            first_subview, *subviews = (
                getattr(res.group, res.group._SUPER_GROUP_NAME)._view
                for res in subresults.values()
            )
            subresults_view = first_subview.union(subviews)
            matching_pks += subresults_view.all_pk()

        results = Dict()

        # Search for immediate results (direct fields)
        # my_matching_pks, my_results =
        search_result = self._repo.search(self._view, pattern, self._SEARCH_FIELDS)
        matching_pks += search_result.matching_pks
        for field_name, field_result in search_result.results.items():
            group = None
            if field_result.view is not None:
                group = type(self)(self._context, view=field_result.view)
            results[field_name] = Result(
                matches=field_result.matches,
                group=group,
            )

        # this is always a subset of the original group even though we lose self._query
        # as the matching pks are always inside our group
        matching_pks_view = self._repo.view(pks=matching_pks)
        full_group = type(self)(context=self._context, view=matching_pks_view)

        return SearchResults(
            group=full_group,
            subresults=subresults,
            results=results,
        )

    # TODO: Inspect the field types by names of the model's fields (delegate_args)
    def where(self, *args, **kwargs) -> Group:
        """
        Filter the query by the parameters specific to the model represented by this Group.
        The parameter's are specific to each backend but currently are passed to sqlalchemy's:
        `:func:sqlalchemy.orm.Query.filter()` and :func:sqlalchemy.orm.Query.filter_by()`
        functions.

        For more info: https://docs.sqlalchemy.org/en/13/orm/query.html#sqlalchemy.orm.query.Query.filter

        .. code-block:: python

            >>> cheese.where(CheeseModel.matured_years >= 6, origin='England')

        :return: Newly built group object (doesn't affect ``self``)
        """
        view = self._view.where(*args, **kwargs)
        return type(self)(self._context, view=view)

    def limit(self, count: int) -> Group:
        """
        Limit the group

        :param count: The count to limit by

        :return: Newly built group object (doesn't affect ``self``)
        """
        view = self._view.limit(count)
        return type(self)(self._context, view=view)

    @classmethod
    def _register_subgroup(cls, subgroup_cls: Type):
        key = getattr(
            subgroup_cls._MODEL_CLS,
            # XXX shortcut when using sqlalchemy
            "_container_relationship",
            subgroup_cls._MODEL_CLS.__membersname__,
        )

        if key in cls._SUBGROUPS:
            raise RuntimeError(
                f"trying to reregister sub-group: '{key}' ({subgroup_cls._MODEL_CLS})",
            )
        cls._SUBGROUPS[key] = subgroup_cls

    @classmethod
    def _register_super_group(cls, super_group_cls: Type):
        cls._SUPER_GROUP = super_group_cls
        cls._SUPER_GROUP_NAME = super_group_cls._MODEL_CLS.__membersname__


def __create_columns_alls(cls):
    model = cls._MODEL_CLS
    singular_name = model.__membername__

    # bypass late/lazy binding
    def get_factory(field_name):
        doc = (
            f"Returns [{singular_name}.{field_name} for {singular_name} in this_{cls.__name__.lower()}.all()], "
            f"or the union of the results, if the field refers to a list"
        )

        def get_func(self) -> List:
            view_get_method = getattr(self._view, f"all_{field_name}", None)
            if view_get_method is None:
                raise RuntimeError(
                    f"view {self._view} doesn't support all_{field_name}",
                )
            return view_get_method()

        get_func.__doc__ = doc
        return get_func

    for field in dataclasses.fields(cls._MODEL_CLS):
        setattr(cls, f"all_{field.name}", get_factory(field.name))


def group_class(model_cls: Type) -> Type[Group]:
    def wrap(_cls):
        _cls._SUBGROUPS = dict()
        _cls._SUPER_GROUP_NAME = None
        _cls._MODEL_CLS = model_cls
        _cls._SEARCH_FIELDS = list()
        __create_columns_alls(_cls)
        return _cls

    return wrap


def subgroup_class(super_group: Type[Group], model_cls: Type) -> Type[Group]:
    def wrap(_cls: Type):
        # TODO: Add recursion, and more sanity checks...
        _cls = group_class(model_cls=model_cls)(_cls)
        super_models = getattr(model_cls, SUPER_MODELS, [])
        super_models.append(super_group._MODEL_CLS)
        setattr(model_cls, SUPER_MODELS, super_models)
        super_group._register_subgroup(_cls)
        _cls._register_super_group(super_group)
        return _cls

    return wrap


def search_fields(*fields) -> Type:
    def wrap(_cls: Type):
        if getattr(_cls, "_MODEL_CLS", None) is None:
            raise RuntimeError(
                "`search_fields` may only be used after the `(sub)group_class` decorator, "
                "(placed above the `(sub)group_class` decorator",
            )
        model_fields = [field.name for field in dataclasses.fields(_cls._MODEL_CLS)]
        for field in fields:
            if field not in model_fields:
                raise RuntimeError(
                    f"field: {field}, is not applicable for model: {_cls._MODEL_CLS}",
                )
            _cls._SEARCH_FIELDS.append(field)
        return _cls

    return wrap
