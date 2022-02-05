from __future__ import annotations
from typing import Type, Dict, List, Any, Optional, Union
import abc

from sqlalchemy import text, Index
from sqlalchemy.schema import Constraint
import dataclasses
import enum

# just some sane value, feel free to change
_DEFAULT_STRING_INDEX_LENGTH = 64


class Relations(enum.Enum):
    ONE_TO_MANY = 1
    MANY_TO_ONE = 2


@dataclasses.dataclass
class EnhancedFieldResult:
    """
    A class that holds the information needed by :py:mod:`rubedo.backend.model` to apply all the EnhancedFields
    """
    unwrapped_type: Optional[Type] = None
    table_constraints: List[Constraint] = dataclasses.field(default_factory=list)
    column_arguments: Dict[str, Any] = dataclasses.field(default_factory=dict)
    relation: Optional[Relations] = None

    def __iadd__(self, other):
        self.unwrapped_type = other.unwrapped_type
        self.table_constraints += other.table_constraints
        self.column_arguments.update(other.column_arguments)
        if self.relation is not None:
            if other.relation is not None and self.relation != other.relation:
                raise TypeError('More than one relation requested')
        else:
            self.relation = other.relation
        if self.relation == Relations.ONE_TO_MANY and self.column_arguments.get('primary_key', False):
            raise TypeError('ONE_TO_MANY relationship cannot be used as a primary key')
        return self


class EnhancedFieldBase(abc.ABC):
    """
    Base class for defining EnhancedFields. Enhanced fields are used as annotations to a class definition
    in order to change the corresponding definition of :py:class:`sqlalchemy.Column`.

    Example:

    .. code-block:: python

        >>> @rubedo_model('examples', 'example')
            class ExampleModel:
                some_column: Indexed(PrimaryKey(str))
        # This will create a table with one column named 'some_column', which is both the pk and indexed

    if the EnhancedField is applied to a column that will result in an anonymous table,
    the EnhancedField will be applied to the appropiate column in the anonymous table

    Example:

    .. code-block:: python

        >>> @rubedo_model('examples', 'example')
            class ExampleModel:
                anonymi: Indexed(List[str])
        # This will create an anonymous table, with a pk column, an anonymi column which is indexed,
        # and foriegn pk column back to ExampleModel (which is also indexed)

    .. warning::

        For now one-to-one and many-to-many ralationship are not supported by any backed
        (like :py:mod:`rubedo.backend.sqlsorcery`) so be sure to implement them if you want to create an EnhancedField
        which uses them.
    .. warning::

        When creating new EnhancedFields, always remember to set the `unwrapped_type` field of the result to the
        apropriate field (unless you like infinite recursion). see Indexed for an example

    """
    # TODO the second example isn't showing in readthedocs
    def __init__(self, field_type: Union[Type, EnhancedFieldBase]):
        self._field_type = field_type

    @property
    def field_type(self):
        return self._field_type

    @abc.abstractmethod
    def build(self, orig_cls: Type, field: dataclasses.Field) -> EnhancedFieldResult:
        """
        Builds the :py:class:`EnhancedFieldResult` that needs to be applied by this Field
        :param orig_cls: The model class from which the field originated
        :param field: The field to build upon
        :return: The resulting :py:class:`EnhanceFieldResult`
        """
        raise NotImplementedError()

    def post(self, orig_cls: Type, field: dataclasses.Field, result: EnhancedFieldResult) -> None:
        """
        A function which will be called at the end of parse, with the result of the parsing
        """
        pass

    @classmethod
    def parse(cls, orig_cls: Type, field: dataclasses.Field) -> EnhancedFieldResult:
        """
        Parses enhanced columns recursively, returning an EnhancedColumnResult object to be applied to the ORM.
        Implicitly parses 'field: List[]' as a ONE_TO_MANY relation,
        and 'field: OtherTable' as MANY_TO_ONE relation
        :param orig_cls: The model class from which the field originated.
        :param field: The field to parse.
        :return: The resulting :py:class:`EnhanceFieldResult` of *all* the used enhanced fields.
        """
        field_type = field.type
        result = EnhancedFieldResult(unwrapped_type=field_type)
        enhanced_fields = []
        while isinstance(field_type, cls):
            enhanced_fields.append(field_type)
            result += field_type.build(orig_cls, field)
            field_type = result.unwrapped_type

        relation_result = EnhancedFieldResult(unwrapped_type=result.unwrapped_type)

        origin = getattr(field_type, '__origin__', None)
        if origin is not None and issubclass(origin, List):
            relation_result.relation = Relations.ONE_TO_MANY
            relation_result.unwrapped_type, = field_type.__args__
        elif getattr(field_type, '__enhancedfields__', None) is not None:
            relation_result.relation = Relations.MANY_TO_ONE
        result += relation_result

        for enhanced_field in enhanced_fields:
            enhanced_field.post(orig_cls, field, result)

        return result

    def __call__(self):
        # XXX Support for `typing.get_type_hints` to work even with forward references in future-annotations
        # (typing._type_check)
        return self


class Indexed(EnhancedFieldBase):
    """
    Builds a sql index for the field
    """
    def __init__(self, field_type: Union[Type, EnhancedFieldBase], length: int = _DEFAULT_STRING_INDEX_LENGTH):
        self._length = length
        super().__init__(field_type)

    def build(self, orig_cls: Type, field: dataclasses.Field) -> EnhancedFieldResult:
        return EnhancedFieldResult(unwrapped_type=self._field_type)

    def post(self, orig_cls: Type, orig_field: dataclasses.Field, result: EnhancedFieldResult) -> None:
        column_name = orig_field.name
        full_column_name = f'{orig_cls.__uniquename__}__{column_name}'
        field_type = result.unwrapped_type
        if field_type in (bytes, str):
            index = Index(f'ix_{full_column_name}', text(f'substr(`{column_name}`, 1, {self._length})'))
        else:
            index = Index(f'ix_{full_column_name}', column_name)

        result.table_constraints.append(index)


class PrimaryKey(EnhancedFieldBase):
    """
    Marks the field's column as a primary key for the table.
    If the field is an int, it is also auto incremented (unless some other enhanced fields says otherwise)
    """
    def __init__(self, field_type: Union[Type, EnhancedFieldBase], **kwargs):
        self._kwargs = kwargs
        super().__init__(field_type)

    def build(self, orig_cls: Type, field: dataclasses.Field) -> EnhancedFieldResult:
        column_args = dict(primary_key=True)
        column_args.update(self._kwargs)
        return EnhancedFieldResult(
            self.field_type,
            [],
            column_args,
        )

    def post(self, orig_cls: Type, field: dataclasses.Field, result: EnhancedFieldResult) -> None:
        if result.unwrapped_type is int:
            if result.column_arguments.get('autoincrement', None) is None:
                result.column_arguments['autoincrement'] = True


class Unique(EnhancedFieldBase):
    """
    Marks the field's column as unique
    """
    def build(self, orig_cls: Type, field: dataclasses.Field) -> EnhancedFieldResult:
        return EnhancedFieldResult(
            unwrapped_type=self.field_type,
            column_arguments=dict(unique=True),
        )


class NonNullable(EnhancedFieldBase):
    """
    Marks the field's column as non nullable
    """
    def build(self, orig_cls: Type, field: dataclasses.Field) -> EnhancedFieldResult:
        return EnhancedFieldResult(
            unwrapped_type=self.field_type,
            column_arguments=dict(nullable=False),
        )

# TODO: add DiskBacked enhanced field that saves the field to disk and loads it automatically on queries - issue #86
