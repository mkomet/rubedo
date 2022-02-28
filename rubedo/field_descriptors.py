import abc
import operator
from typing import Callable


class FieldComparator(abc.ABC):
    def __init__(self, filter_: Callable):
        self._filter = filter_

    def __or__(self, other: "FieldComparator"):
        return type(self)(lambda model: self._filter(model) or other._filter(model))

    def __and__(self, other: "FieldComparator"):
        return type(self)(lambda model: self._filter(model) and other._filter(model))

    # TODO: add every function from InstrumentedFieldBase, and `and` this comparator and the resulting filter.

    def evaluate(self, model) -> bool:
        return self._filter(model)


class InstrumentedFieldBase(abc.ABC):
    """
    Base class for field descriptors able to provide a consistent py:func:`.filter` functionality
    between different backends.
    """

    @classmethod
    def create(cls, owner, name):
        new_field = cls(owner, name)
        original_field = getattr(owner, name, None)
        setattr(owner, name, new_field)
        if original_field is not None:
            setattr(owner, new_field._raw_field, original_field)

    def __init__(self, owner, name):
        self._name = name
        self._raw_field = f"_raw_{name}"
        self._owner = owner

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return getattr(instance, self._raw_field)

    def __set__(self, instance, value):
        return setattr(instance, self._raw_field, value)

    def _create_comparator(self, func: Callable, *args, **kwargs) -> FieldComparator:
        def filter_(model) -> bool:
            value = getattr(model, self._raw_field)
            return value is not None and func(value, *args, **kwargs)

        return FieldComparator(filter_)

    def __lt__(self, other) -> FieldComparator:
        return self._create_comparator(operator.lt, other)

    def __le__(self, other) -> FieldComparator:
        return self._create_comparator(operator.le, other)

    def __eq__(self, other) -> FieldComparator:
        return self._create_comparator(operator.eq, other)

    def __ne__(self, other) -> FieldComparator:
        return self._create_comparator(operator.ne, other)

    def __gt__(self, other) -> FieldComparator:
        return self._create_comparator(operator.gt, other)

    def __ge__(self, other) -> FieldComparator:
        return self._create_comparator(operator.ge, other)

    def __neg__(self, other) -> FieldComparator:
        return self._create_comparator(operator.neg, other)

    def __contains__(self, item) -> FieldComparator:
        return self._create_comparator(operator.contains, item)

    def is_distinct_from(self, other) -> FieldComparator:
        raise NotImplementedError()

    def is_not_distinct_from(self, other) -> FieldComparator:
        raise NotImplementedError()

    def concat(self, other) -> FieldComparator:
        raise NotImplementedError()

    def like(self, other, escape=None) -> FieldComparator:
        raise NotImplementedError()

    def ilike(self, other, escape=None) -> FieldComparator:
        raise NotImplementedError()

    def in_(self, other) -> FieldComparator:
        def in_(model):
            value = getattr(model, self._raw_field)
            return value is not None and model in value

        return FieldComparator(in_)

    def not_in(self, other) -> FieldComparator:
        raise NotImplementedError()

    def not_like(self, other, escape=None) -> FieldComparator:
        raise NotImplementedError()

    def not_ilike(self, other, escape=None) -> FieldComparator:
        raise NotImplementedError()

    def is_(self, other) -> FieldComparator:
        raise NotImplementedError()

    def is_not(self, other) -> FieldComparator:
        raise NotImplementedError()

    def startswith(self, other, **kwargs) -> FieldComparator:
        raise NotImplementedError()

    def endswith(self, other, **kwargs) -> FieldComparator:
        raise NotImplementedError()

    def contains(self, other, **kwargs) -> FieldComparator:
        return self.__contains__(other)

    def match(self, other, **kwargs) -> FieldComparator:
        raise NotImplementedError()

    def regexp_match(self, pattern, flags=None) -> FieldComparator:
        raise NotImplementedError()

    def regexp_replace(self, pattern, replacement, flags=None) -> FieldComparator:
        raise NotImplementedError()

    def desc(self) -> FieldComparator:
        raise NotImplementedError()

    def asc(self) -> FieldComparator:
        raise NotImplementedError()

    def nulls_first(self) -> FieldComparator:
        raise NotImplementedError()

    def nulls_last(self) -> FieldComparator:
        raise NotImplementedError()

    def collate(self, expression, collation) -> FieldComparator:
        raise NotImplementedError()

    def between(self, cleft, cright, symmetric=False) -> FieldComparator:
        raise NotImplementedError()

    def distinct(self) -> FieldComparator:
        raise NotImplementedError()

    def any_(self) -> FieldComparator:
        raise NotImplementedError()

    def all_(self) -> FieldComparator:
        raise NotImplementedError()
