import pprint
from sqlalchemy import Text
from sqlalchemy.ext.associationproxy import (
    ASSOCIATION_PROXY,
    ColumnAssociationProxyInstance,
)
from sqlalchemy.orm import Mapper, RelationshipProperty
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.inspection import inspect
from typing import Optional, List, Dict, Union, Any
import dataclasses
from functools import wraps

from ..model import SUPER_MODELS


class GetColumnsMixin:
    @staticmethod
    def _get_target_model(relation: RelationshipProperty):
        target_model = relation.argument
        if isinstance(target_model, Mapper):
            return target_model.class_
        return target_model

    @classmethod
    def get_columns(
        cls,
        show_pk: bool = False,
        show_hidden: bool = False,
        show_super: bool = True,
    ) -> Dict[
        str,
        Union[
            InstrumentedAttribute,
            RelationshipProperty,
            ColumnAssociationProxyInstance,
        ],
    ]:
        """
        Parses the classes attributes and returns a dictionary containing only the columns of the class
        by default, the pk column, and columns starting with '_' are not shown.
        Columns representing relationships to one of the supermodels of the cls (set using
        :py:mod:`rubedo.frontend.groups`) are also not shown by default.
        :param show_pk: If True also shows the pk column
        :param show_hidden: If True, also shows columns starting with '_'
        :param show_super: If True, also shows supermodels relationships
        :return: A dictionary of column name -> column type for all the columns of the model.
        """
        inspector: Mapper = inspect(cls)
        super_models = getattr(cls, SUPER_MODELS, None)
        result = {}
        # normal columns
        for name in inspector.columns.keys():
            if name == "pk" and not show_pk:
                continue
            if name[0] == "_" and not show_hidden:
                continue
            result[name] = getattr(cls, name)

        # association proxy (List[str])
        target_collections = []
        for name, descriptor in inspector.all_orm_descriptors.items():
            if descriptor.extension_type is ASSOCIATION_PROXY:
                result[name] = getattr(cls, name)
                target_collections.append(descriptor.target_collection)

        for name, relation in inspector.relationships.items():
            is_super = cls._get_target_model(relation) in super_models
            if name in target_collections or (
                is_super and not show_super
            ):  # skip anonymous tables
                continue
            result[name] = relation
        return result


class PPrintMixin(GetColumnsMixin):
    def __asdict_parse_relation(
        self,
        relation_prop: RelationshipProperty,
        relation_value: Any,
        show_hidden: bool,
        show_super: bool,
        expand_level: int,
        print_id: bool,
        passed_tables: List,
    ) -> Any:
        target_table = self._get_target_model(relation_prop)
        if target_table in passed_tables:
            return None
        if print_id:
            return f"{type(relation_value)} object at {hex(id(relation_value))}"
        if relation_prop.uselist:
            return [
                relation_member._asdict(
                    show_hidden=show_hidden,
                    show_super=show_super,
                    expand_level=expand_level,
                    passed_tables=passed_tables,
                )
                for relation_member in relation_value
            ]
        elif getattr(relation_value, "_asdict", None) is not None:
            return relation_value._asdict(
                show_hidden=show_hidden,
                show_super=show_super,
                expand_level=expand_level,
                passed_tables=passed_tables,
            )  # don't pass blacklist
        else:
            return relation_value

    def _asdict(
        self,
        show_hidden: bool = False,
        show_super: bool = False,
        expand_level: int = -1,
        field_blacklist: Optional[List] = None,
        passed_tables: List = None,
    ) -> Dict:
        if passed_tables is None:
            passed_tables = list()
        if field_blacklist is None:
            field_blacklist = list()
        result = dict()
        columns = self.get_columns(
            show_pk=show_hidden,
            show_hidden=show_hidden,
            show_super=show_super,
        )
        passed_tables.insert(0, type(self))
        print_id = expand_level == 0
        if expand_level > 0:
            expand_level -= 1
        for name, column in columns.items():
            if name in field_blacklist:
                continue
            value = getattr(self, name)
            if value is None or not isinstance(column, RelationshipProperty):
                result[name] = value
                continue
            relation_result = self.__asdict_parse_relation(
                column,
                value,
                show_hidden,
                show_super,
                expand_level,
                print_id,
                passed_tables,
            )
            if relation_result is not None:
                result[name] = relation_result
        passed_tables.pop(0)
        return result

    def asdict(
        self,
        show_hidden: bool = False,
        show_super: bool = False,
        expand_level: int = -1,
        field_blacklist: Optional[List] = None,
    ) -> Dict:
        """
        Returns all of the model's fields in a dictionary (recursively).
        if show_hidden is False, pk and protected fields (those who start with '_') are not returned.
        if show_super is False, the supermodels are not recursed. The supermodels are set when defining groups and
        subgroups.
        field_blacklist is an optional list of fields to not show (in the current model).
        """
        # TODO: limit the output length of these functions (we don't want print(str(rom)) to make the world explode)
        # maybe reprlib?
        return self._asdict(show_hidden, show_super, expand_level, field_blacklist)

    def __str__(self):
        return pprint.pformat(self.asdict(), indent=2)


def _init_init_and_repr(cls):
    """
    Create an init and repr functions for the model, using dataclasses, so the docs of the function will show
    all the needed fields.
    TODO: maybe rerun this function after all relationships have been created
    TODO: figure out why sphinx doesn't register the new functions
    """
    from .sqlsorcery import _SQLALCHEMY_TYPES

    annotations = cls.__annotations__
    namespace = {}
    columns = cls.get_columns(show_super=True)
    for column_name, column in columns.items():
        annotation = None
        default = None
        if column is None:
            continue
        if isinstance(column, RelationshipProperty):
            if column.uselist:
                annotation = List[column.argument]
                default = dataclasses.field(default_factory=list)
            else:
                annotation = column.argument
                default = None
        else:
            uselist = False
            if isinstance(column, ColumnAssociationProxyInstance):
                column_attr = column.remote_attr
                uselist = True
            else:
                column_attr = column
            column_instance = column_attr.prop.columns[0]
            column_type = column_instance.type
            for py_type, sql_type in _SQLALCHEMY_TYPES.items():
                # XXX str -> Text() is an instance
                if isinstance(sql_type, Text):
                    sql_type = Text
                if isinstance(column_type, sql_type):
                    if uselist:
                        annotation = List[py_type]
                        default = dataclasses.field(default_factory=list)
                    else:
                        annotation = py_type
                        default = column_instance.default
                        if default is not None:
                            default = default.arg
                    break
        annotations[column_name] = annotation
        namespace[column_name] = default

    temp_cls = dataclasses.make_dataclass(
        cls.__name__,
        annotations.items(),
        namespace=namespace,
    )
    old_init = cls.__init__

    @wraps(temp_cls.__init__)
    def new_init(*args, **kwargs):
        self, *args = args
        temp = temp_cls(*args, **kwargs)
        # don't use dataclasses.asdict because we don't want to deepcopy the fields
        old_init(self, **temp.__dict__)

    cls.__init__ = new_init
    cls.__repr__ = temp_cls.__repr__
