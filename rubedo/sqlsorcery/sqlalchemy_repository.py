from __future__ import annotations

from contextlib import ExitStack, contextmanager
from itertools import chain
from typing import Any, Dict, Iterable, List, Type

import sqlalchemy.ext.associationproxy
import sqlalchemy.orm
import sqlalchemy.orm.attributes
import sqlalchemy.sql
from sqlalchemy.orm import Session

from ..model import ModelBase
from ..repository_base import (
    RepositoryBase,
    RepositorySearchFieldResult,
    RepositorySearchResult,
    ViewBase,
)
from ..utils import RubedoDict


class SqlalchemyView(ViewBase):
    """
    A view to a set of models backed by SqlAlchemy
    """

    def __init__(
        self,
        repo: SqlalchemyRepositoryBase,
        query: sqlalchemy.orm.Query,
        subquery: sqlalchemy.sql.Alias = None,
        clean: bool = True,
    ):
        self._repo = repo
        self._model_cls: Type[ModelBase] = repo._model_cls
        self._query = query
        self._clean = clean
        if subquery is not None:
            self._clean = False
            self._query = self._query.join(subquery)
        self.__create_columns_alls()

    def __getitem__(self, item):
        with self._repo.uow():
            return self._query.__getitem__(item)

    def all(self) -> List[ModelBase]:
        with self._repo.uow():
            return self._query.all()

    def _from_self(self) -> sqlalchemy.orm.Query:
        # TODO: from_self is deprecated since SA 1.4, will be removed in 2.0
        if self._clean:
            return self._query
        return self._query.from_self()

    def __create_columns_alls(self):
        """
        Create the all_XXX methods for every column of the table.
        TODO: currently generates them at view init time instead of at class creation time, which is slower.
        """
        model = self._model_cls

        # bypass late/lazy binding
        def get_factory_normal(column_type):
            def get_func(self) -> List:
                with self._repo.uow():
                    return list(chain(*self._query.with_entities(column_type).all()))

            return get_func

        def get_factory_proxy(column_type, column_name):
            def get_func(self) -> List:
                proxied_model = column_type.target_class
                proxied_column = getattr(proxied_model, column_name)
                session = self._repo._session
                with self._repo.uow():
                    pks = chain(*self._query.with_entities(self._model_cls.pk).all())
                    return list(
                        chain(
                            *session.query(proxied_column)
                            .filter(proxied_model.fk.in_(pks))
                            .all(),
                        ),
                    )

            return get_func

        def get_factory_relation(column_type):
            def get_func(self) -> List:
                session = self._repo._session
                with self._repo.uow():
                    return (
                        session.query(column_type.argument)
                        .join(self._query.subquery())
                        .all()
                    )

            return get_func

        singular_name = model.__membername__
        for column, column_type in model.get_columns(show_pk=True).items():
            doc_base = f"[{singular_name}.{column} for {singular_name} in this_{type(self).__name__.lower()}.all()]"
            if isinstance(column_type, sqlalchemy.orm.attributes.InstrumentedAttribute):
                get_func = get_factory_normal(column_type)
                doc = f"Returns {doc_base}\n"
            elif isinstance(
                column_type,
                sqlalchemy.ext.associationproxy.ColumnAssociationProxyInstance,
            ):
                get_func = get_factory_proxy(column_type, column)
                doc = f"Returns the union of {doc_base}\n"
            elif isinstance(
                column_type,
                sqlalchemy.orm.properties.RelationshipProperty,
            ):
                get_func = get_factory_relation(column_type)
                doc = f"Returns the union of {doc_base}, this is equivalent to a subgroup / supergroup\n"
            else:
                continue
            get_func.__doc__ = doc
            # bind the function to the instance:
            bound_get_func = get_func.__get__(self, type(self))
            setattr(self, f"all_{column}", bound_get_func)

    def union(self, views: Iterable[SqlalchemyView]) -> SqlalchemyView:
        # SQLAlchemy's union doesn't support generators
        query = self._from_self().union(
            *[view._repo._calculate(view)._query for view in views],
        )
        return type(self)(self._repo, query, clean=False)

    def count(self) -> int:
        """
        :return: Return a count of results this Group holds.

        .. warning::

            Taken from sqlalchemy's docs:

            .. warning::

                It is important to note that the value returned by count() is not the same as the number of
                ORM objects that this Query would return from a method such as the .all() method.
                The Query object, when asked to return full entities, will deduplicate entries based on primary key,
                meaning if the same primary key value would appear in the results more than once,
                only one object of that primary key would be present.
                This does not apply to a query that is against individual columns.

        """
        with self._repo.uow():
            return self._query.count()

    def first(self) -> ModelBase:
        with self._repo.uow():
            return self._query.first()

    def last(self) -> ModelBase:
        with self._repo.uow():
            return self._query.order_by(self._model_cls.pk.desc()).first()

    # TODO: Inspect the field types by names of the model's fields (delegate_args)
    def where(self, *args, **kwargs) -> SqlalchemyView:
        """
        Filter the query by the parameters specific to the model represented by this View.
        Uses sqlalchemy's :func:sqlalchemy.orm.Query.filter()` and :func:sqlalchemy.orm.Query.filter_by()`
        functions.

        For more info: https://docs.sqlalchemy.org/en/13/orm/query.html#sqlalchemy.orm.query.Query.filter

        .. code-block:: python

            >>> roms_view.where(RomModel.android_version_major >= 6, brand='xiaomi')
            <rubedo.backend.sqlsorcery.sqlalchemy_repository.View at 0x7fca9f8f2850>
            >>> # _.all()
            >>> # _.count()

        :return: Newly built view (doesn't affect ``self``)
        """

        # XXX Lazily use `from_self()` (encompassing the previous joins)
        #   so as to filter the table (doing it upon each join could cause "parser stack overflow"
        #   surpassing the max depth for the aliased subquery tables)
        query = self._from_self()
        if len(args) > 0:
            query = query.filter(*args)
        if len(kwargs) > 0:
            # XXX: after a call to from_self(), sqlalchemy aliases each column of the query with:
            # <table_name>_<column_name>
            # TODO: use sqlalchemy to actually solve the problem
            if self._clean:
                query = query.filter_by(**kwargs)
            else:
                fixed_kwargs = {
                    f"{self._model_cls.__tablename__}_{key}": value
                    for key, value in kwargs.items()
                }
                query = query.filter_by(**fixed_kwargs)

        return type(self)(self._repo, query, clean=False)

    def limit(self, count: int) -> SqlalchemyView:
        query = self._query.limit(count)
        return type(self)(self._repo, query, clean=False)


class SqlalchemyRepositoryBase(RepositoryBase):
    def __init__(self, context):
        super().__init__(context)
        self._session: Session = context.sql_session
        try:
            self._query = self._session.query(self._model_cls)
        except Exception as error:
            raise RuntimeError(
                f"caught exception: '{error}', while querying: '{context.sql_engine.url}', "
                f"for table: '{self._model_cls.__tablename__}'",
            )

    def __init_subclass__(cls, model_cls=None, **kwargs):
        super().__init_subclass__(**kwargs)
        if model_cls is None:
            raise TypeError()
        cls._model_cls = model_cls

    def add(self, obj: ModelBase):
        self._session.add(obj)

    def add_all(self, obj: ModelBase):
        self.add(obj)

    def remove(self, obj: ModelBase):
        self._session.delete(obj)

    @property
    def query(self):
        return self._query

    def view(self, pks: List = None) -> SqlalchemyView:
        query = self._query
        if pks is not None:
            query = query.filter(self._model_cls.pk.in_(pks))
        return SqlalchemyView(self, query)

    def _calculate(self, view: SqlalchemyView) -> SqlalchemyView:
        """
        Reduce a view to a basic query that only checks if contained in a list of PKs.
        Useful when reaching the end of the SQLite stack (while traversing the group hierarchy).

        .. note::

            Pretty hardcore - use carefully, and only when necessary

        :return: Newly created "flat" view
        """
        pks = view.all_pk()
        return self.view(pks=pks)

    def _search_association_proxy(
        self,
        view: SqlalchemyView,
        pattern: str,
        field_name: str,
        field: sqlalchemy.ext.associationproxy.ColumnAssociationProxyInstance,
    ) -> Dict[int, Any]:  # noqa: F821
        """
        Search for a pattern contained inside relevant values of an anonymous table

        :param pattern: The pattern to search for
        :param field_name: The field name of the association proxy property
        :param field: The association proxy property
        :return: A mapping between the fk's in the anonymous table, and the corresponding values in the same table,
            that matched the pattern
        """
        pks = view._query.from_self(self._model_cls.pk).all()
        pks = list(map(lambda p: p[0], pks))
        query = (
            self._session.query(
                field.target_class,
            )
            .join(
                self._model_cls,
            )
            .filter(
                self._model_cls.pk.in_(pks),
            )
            .filter(
                getattr(field.target_class, field_name).contains(pattern),
            )
        )

        return {row.fk: getattr(row, field_name) for row in query.all()}

    # TODO: support regex (sa 1.4 has column.regexp_match)
    def search(
        self,
        view: SqlalchemyView,
        pattern: str,
        search_fields: List[str],
    ) -> RepositorySearchResult:
        results = RubedoDict()
        matching_pks = []
        for field_name in search_fields:
            field = getattr(self._model_cls, field_name, None)
            if field is None:
                raise RuntimeError()
            if isinstance(
                field,
                sqlalchemy.ext.associationproxy.ColumnAssociationProxyInstance,
            ):
                matches = self._search_association_proxy(
                    view,
                    pattern,
                    field_name,
                    field,
                )
                result_view = None  # TODO: maybe automatically create views for associations proxies?
            else:
                result_view = view.where(field.contains(pattern))
                matches = dict(
                    result_view._query.from_self(
                        self._model_cls.pk,
                        field,
                    ).all(),
                )
            matching_pks += matches.keys()
            results[field_name] = RepositorySearchFieldResult(
                matches=matches,
                view=result_view,
            )
        return RepositorySearchResult(matching_pks, results)

    @contextmanager
    def uow(self):
        """
        unit of work under SA - rollback if something bad happened, commit if all is good
        """
        with ExitStack() as stack:
            stack.callback(self._session.rollback)
            yield
            self._session.commit()
            # cancel the rollback if everything went smoothly
            stack.pop_all()

    def _build_relation_view(
        self,
        view: ViewBase,
        related_model_cls: Type[ModelBase],
    ) -> SqlalchemyView:
        """
        Build a view across SA's relationship property. support both MANY_TO_ONE and ONE_TO_MANY relations
        """
        repo_cls: Type[SqlalchemyRepositoryBase] = related_model_cls.repository_cls
        repo = repo_cls(self._context)
        query = repo.query
        subquery = view._query.subquery()
        return SqlalchemyView(repo, query, subquery=subquery, clean=False)

    def build_submodel_view(
        self,
        view: ViewBase,
        field_name: str,
        submodel_cls: Type[ModelBase],
    ) -> SqlalchemyView:
        return self._build_relation_view(view, submodel_cls)

    def build_supermodel_view(
        self,
        view: ViewBase,
        field_name: str,
        super_model_cls: Type[ModelBase],
    ) -> SqlalchemyView:
        return self._build_relation_view(view, super_model_cls)
