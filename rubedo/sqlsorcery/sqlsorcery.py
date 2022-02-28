import dataclasses
import datetime
import enum

import sqlalchemy
from sqlalchemy import (
    Boolean,
    BigInteger,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    MetaData,
    Table,
)
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import (
    relationship,
    synonym,
    registry,
    column_property,
    sessionmaker,
)
from typing import Tuple, Type, Dict, Any, Optional
import inspect

from ..enhanced_fields import EnhancedFieldResult, Relations
from ..model import ModelBase, PLURAL_NAME, SINGULAR_NAME, ENHANCED, UNIQUE_NAME
from ..utils import RubedoDict
from ..backend_base import BackendBase
from .sqlalchemy_repository import SqlalchemyRepositoryBase
from .sql_mixins import PPrintMixin, _init_init_and_repr


# Explicitly state the max length, so sqlalchemy can use MEDIUMTEXT/LONGTEXT in MySQL
_MAX_TEXT_LENGTH = 2 << 23
_SQLALCHEMY_TYPES = {
    bool: Boolean,
    bytes: LargeBinary,
    int: BigInteger,
    float: Float,
    str: Text(_MAX_TEXT_LENGTH),  # TODO: support unicode (collation="utf8")
    datetime.datetime: DateTime,  # TODO: Timezone support, doesn"t seem to actually work (Sqlite at least)
}


metadata = MetaData()
mapper_registry = registry(metadata=metadata)


class SqlSorceryBackend(BackendBase):
    """
    A class that parses Models, creates a suitable sqlalchemy table for them, and maps the model the table
    allowing for a "dataclasses" like definition of ORM compliant models, under the repository pattern.
    XXX:
    fields created by this backend are not instances of py:class:`rubedo.backend.field_descriptors.InstrumentedFieldBase`
    (they use sa fields instead), but provide the same functionality.
    """

    TABLE_NAME = "__tablename__"
    DEFAULT_PK_NAME = "pk"

    def __init__(self, model_cls: Type[ModelBase]):
        """
        :param model_cls: an rubedo model - must be a dataclass (after parsing of enhanced fields)
        """

        if not dataclasses.is_dataclass(model_cls):
            raise TypeError("model class must be a dataclass")
        super().__init__(model_cls)
        self._metadata = metadata
        self._mapper_registry = mapper_registry

        self._plural_name = getattr(model_cls, PLURAL_NAME)
        self._tablename = getattr(model_cls, UNIQUE_NAME)
        setattr(self._model_cls, self.TABLE_NAME, self._tablename)
        self._singular_name = getattr(model_cls, SINGULAR_NAME)

        # get the enhanced fields results (a mapping between field names, and their enhanced field parsing result)
        self._enhanced_fields_results: Dict = getattr(model_cls, ENHANCED)

        # TODO: super models
        # SUPER_MODELS: getattr(cls, SUPER_MODELS, [])

        self._properties: Dict[str, Any] = {}

        # will be set by ``_create_pk_if_doesnt_exists``
        self._pk_type: Type = None

        # create empty table now and append columns to it, so MANY_TO_ONE relationships would be able to target
        # this table while it is being created
        self._table = Table(
            self._tablename,
            self._metadata,
        )

    def _map_model(self):
        """
        Maps a model imperatively to SqlAlchemy's ORM, and initializes the model's __init__ and __repr__ methods.
        """

        model_cls = self._model_cls
        self._mapper_registry.map_imperatively(
            model_cls,
            self._table,
            properties=self._properties,
        )
        _init_init_and_repr(model_cls)

    def create_repository(self) -> Type[SqlalchemyRepositoryBase]:
        """
        Creates a repository for the model class of this instance.
        """

        constraints = []
        model_cls = self._model_cls
        enhanced_results = self._enhanced_fields_results

        pk_field = self._create_pk_if_doesnt_exists()

        for field in dataclasses.fields(model_cls):
            ignore_constraints = False
            enhanced_result = enhanced_results[field.name]
            if field == pk_field:
                # already created (but the constraints were not added then)
                constraints += enhanced_result.table_constraints
                continue

            if enhanced_result.relation is not None:
                ignore_constraints = self._parse_relation(field, enhanced_result)
            else:
                self._table.append_column(
                    self._parse_simple_cell(field, enhanced_result),
                )
            if not ignore_constraints:
                constraints += enhanced_result.table_constraints

        for constraint in constraints:
            self._table.append_constraint(constraint)

        self._map_model()

        class SqlRepository(SqlalchemyRepositoryBase, model_cls=model_cls):
            pass

        return SqlRepository

    def _create_pk_if_doesnt_exists(self) -> Optional[dataclasses.Field]:
        """
        finds pk, as we need its type to create relations, if found, extract its type and create it later,
        and add pk as a synonym to it. If not, create an auto incrementing Integer primary key - pk.
        sets the _pk_type of this instance
        """
        pk_type = Integer
        pk_field = None
        pk_enhanced_result = None
        for field in dataclasses.fields(self._model_cls):
            enhanced_result = self._enhanced_fields_results[field.name]
            if enhanced_result.column_arguments.get("primary_key", False):
                pk_type = _SQLALCHEMY_TYPES.get(field.type, None)
                if pk_type is None:
                    raise TypeError("pk type cannot be mapped to a native SQL type")
                pk_field = field
                pk_enhanced_result = enhanced_result
                break

        if pk_field is None:
            pk_column = Column(
                self.DEFAULT_PK_NAME,
                pk_type,
                primary_key=True,
                autoincrement=True,
            )
        # create the pk now (and skip it during normal parsing), and create a synonym to "pk" if needed
        else:
            pk_column = self._parse_simple_cell(pk_field, pk_enhanced_result)
            if pk_field.name != self.DEFAULT_PK_NAME:
                self._properties[self.DEFAULT_PK_NAME] = synonym(pk_field.name)
        self._table.append_column(pk_column)
        self._pk_type = pk_type
        return pk_field

    def _parse_relation(
        self,
        field: dataclasses.Field,
        enhanced_result: EnhancedFieldResult,
    ) -> bool:
        """
        Parse and create the relationship between current table and field,
        creating all the necessary tables and foreign pks.
        Supports:
        - ONE_TO_MANY from current to native SQL Type (creating an "anonymous table")
        - ONE_TO_MANY from current to other table
        - MANY_TO_ONE from current to other table
        :param field: The field that is currently being parsed
        :param enhanced_result: The result of the EnhancedFields parsing for this field

        :return: True iff the enhanced_result should be ignored for this field (which is the case when creating
                anonymous table, where all the constraints are passed to that table instead)
        """

        relation = enhanced_result.relation
        unwrapped_type = enhanced_result.unwrapped_type
        other_tablename = getattr(unwrapped_type, self.TABLE_NAME, None)
        if relation == Relations.ONE_TO_MANY:
            try:
                self._create_anonymous_table(field, enhanced_result)
                return True
            except TypeError:  # a relationship between existing table
                pass

            if other_tablename is None:
                raise TypeError(
                    f"ONE_TO_MANY is only supported on SQL types or tables (got {unwrapped_type})",
                )
            # XXX breaks the abstraction
            fk_name = f"_{self._tablename}_pk"
            fk_column = Column(
                fk_name,
                self._pk_type,
                ForeignKey(self._table.columns.pk),
                index=True,
                nullable=True,
            )
            self_referencing = self._table.name == getattr(
                enhanced_result.unwrapped_type, UNIQUE_NAME
            )
            foreign_table = (
                self._table
                if self_referencing
                else enhanced_result.unwrapped_type.__table__
            )
            foreign_table.append_column(fk_column)
            if self_referencing:
                # TODO: remote_side???
                self._properties[field.name] = relationship(unwrapped_type)
            else:
                sqlalchemy.inspect(enhanced_result.unwrapped_type).add_property(
                    fk_name, column_property(fk_column)
                )
                self._properties[field.name] = relationship(
                    unwrapped_type, backref=self._singular_name
                )
            return False

        if relation == Relations.MANY_TO_ONE:
            if other_tablename is None:
                raise TypeError(
                    f"MANY_TO_ONE is only supported on SQL tables (got {field.type})"
                )
            other_pks = list(field.type.__table__.primary_key.columns)
            if len(other_pks) != 1:
                raise TypeError(
                    f"Cannot create MANY_TO_ONE relationship with composite pk (got {str(other_pks)})"
                )
            other_pk = other_pks[0]
            self._table.append_column(
                Column(
                    f"_{field.name}_pk",
                    type(other_pk.type),
                    ForeignKey(f"{other_tablename}.pk"),
                    index=True,
                    nullable=True,
                )
            )
            self._properties[field.name] = relationship(
                field.type, backref=self._plural_name
            )
        return True

    def _create_anonymous_table(
        self, field: dataclasses.Field, enhanced_result: EnhancedFieldResult
    ) -> None:
        """
        Creates an "anonymous" table making a ONE_TO_MANY relation between this class and the anonymous table
        The anonymous table has 3 columns - integer pk, foreign key to this class, and the value required
        The relationship between the tables is abstracted to this class using an :py:class:`AssociationProxy`
        through the wanted field name, which exposes the table as a simple list of values
        :param field: The field currently being parsed
        :param enhanced_result: The result of the EnhancedFields parsing
        """
        new_namespace = RubedoDict()
        new_namespace[self.TABLE_NAME] = f"{self._tablename}_{field.name}_table"
        new_namespace[PLURAL_NAME] = field.name
        cell_type, default = self._parse_simple_cell_type(
            enhanced_result.unwrapped_type, field.default
        )
        if isinstance(cell_type, Enum):
            setattr(
                self._model_cls,
                enhanced_result.unwrapped_type.__name__,
                enhanced_result.unwrapped_type,
            )

        kwargs = RubedoDict(default=default)
        kwargs.update(enhanced_result.column_arguments)

        new_namespace[field.name] = Column(
            field.name,
            cell_type,
            **kwargs,
        )
        new_namespace[self.DEFAULT_PK_NAME] = Column(
            self.DEFAULT_PK_NAME,
            Integer,
            primary_key=True,
            autoincrement=True,
        )

        fk_name = f"_{self._model_cls.__name__}_pk"
        new_namespace[fk_name] = Column(
            self._pk_type,
            ForeignKey(self._table.columns.pk),
            index=True,
            nullable=False,
        )
        new_namespace.fk = synonym(fk_name)
        relation_name = f"_{field.name}_table"

        # create table:
        new_table = type(new_namespace[self.TABLE_NAME], self.mixins(), new_namespace)

        self._mapper_registry.map_declaratively(new_table)

        # pass the table constraints to the new table instead
        constraints = enhanced_result.table_constraints
        enhanced_result.table_constraints = []
        for constraint in constraints:
            new_table.__table__.append_constraint(constraint)

        self._properties[relation_name] = relationship(
            new_table, backref=self._singular_name
        )
        # XXX: silently breaks the abstraction
        setattr(
            self._model_cls,
            field.name,
            association_proxy(
                relation_name,
                field.name,
                creator=lambda value: new_table(**{field.name: value}),
            ),
        )

    @staticmethod
    def _parse_simple_cell_type(
        field_type: Type, field_default: Any
    ) -> Tuple[Type, Any]:
        """
        Parses a simple cell type - from a python type / Enum to a sql type / Enum, also setting the default value
        :param field_type: The type to be parsed
        :param field_default: The default value for the field
        :return: A tuple of the sql type, and the default value for the column
        """
        is_enum = inspect.isclass(field_type) and issubclass(field_type, enum.Enum)
        if field_type not in _SQLALCHEMY_TYPES and not is_enum:
            raise TypeError()

        if isinstance(field_default, dataclasses._MISSING_TYPE):
            default = None
        else:
            default = field_default
        if is_enum:
            column_type = Enum(field_type)
        else:
            column_type = _SQLALCHEMY_TYPES[field_type]
        return column_type, default

    @classmethod
    def _parse_simple_cell(
        cls,
        field: dataclasses.Field,
        enhanced_result: EnhancedFieldResult,
    ) -> Column:
        """
        Parses a simple cell, returning the appropriate Column
        :param field: The field that is currently being parsed
        :param enhanced_result: The result of the EnhancedFields parsing
        :return: A :py:class:`Column` instance creating this fields column.
        """
        column_type, default = cls._parse_simple_cell_type(
            enhanced_result.unwrapped_type, field.default
        )
        return Column(
            field.name,
            column_type,
            default=default,
            **enhanced_result.column_arguments,
        )

    @classmethod
    def mixins(cls) -> Tuple:
        return (PPrintMixin,)

    @classmethod
    def use_autosetattr(cls) -> bool:
        return False

    @classmethod
    def initialize_backend(cls, context):
        from .sqlutils import create_all

        with context.timeit("creating tables"):
            create_all(context.sql_engine)
        session_cls = sessionmaker()
        session_cls.configure(bind=context.sql_engine)
        context.sql_session = session_cls()
