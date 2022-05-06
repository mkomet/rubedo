import dataclasses
from typing import List, Type, get_type_hints

from . import backend_base
from .enhanced_fields import EnhancedFieldBase, Relations
from .model_base import (
    AUTO_SETATTR,
    BACKREF,
    BACKREF_USELIST,
    PLURAL_NAME,
    SINGULAR_NAME,
    UNIQUE_NAME,
    ModelBase,
)

ANNOTATIONS = "__annotations__"
DOC = "__doc__"
ENHANCED = "__enhancedfields__"
MODULE = "__module__"
SUPER_MODELS = "__supermodels__"


def rubedo_model(
    pluralname: str,
    singularname: str,
    backend: Type[backend_base.BackendBase] = None,
) -> Type[ModelBase]:
    """
    Class decorator for generating repositories for model classes,
    parsing EnhancedFields, and setting the necessary class properties of the model.
    Model classes are treated as dataclasses.

    # Class decorator for generating sqlachemy-compliant ORM classes.
    # supports converting Python builtin-typed fields into sqlalchemy's
    # abstract SQL types (BigInteger, Text, Blob, ...). also supports
    # lists of other dataclasses.

    :param pluralname: the plural noun for the newly created class
    :param singularname: singular noun for the table's represented entity:
        e.g. table *symbols* -> *symbol*
    :param backend: an optional specific backend to use instead of the default / set one.
    """
    chosen_backend = backend
    if backend is None:
        chosen_backend = backend_base.get_backend()

    def _wrap(cls) -> ModelBase:
        annotations = getattr(cls, ANNOTATIONS, dict())
        module = getattr(cls, MODULE)
        unique_name = f"{module.replace('.', '_')}_{pluralname}"
        namespace = {
            SINGULAR_NAME: singularname,
            PLURAL_NAME: pluralname,
            UNIQUE_NAME: unique_name,
            DOC: getattr(cls, DOC),
            ANNOTATIONS: annotations,
            MODULE: module,
            SUPER_MODELS: getattr(cls, SUPER_MODELS, []),
        }
        setattr(cls, SINGULAR_NAME, singularname)
        setattr(cls, PLURAL_NAME, pluralname)
        setattr(cls, UNIQUE_NAME, unique_name)
        bases = (cls,) + chosen_backend.mixins() + (ModelBase,)

        # fix classes that their annotations are lazily evaluated and stored as strings^M
        # (`from __future__ import annotations`, see: https://bugs.python.org/issue33453)^M
        evaluated_annotations = get_type_hints(
            cls,
            localns={cls.__name__: cls},
        )
        annotations.update(evaluated_annotations)

        temp_cls = dataclasses.make_dataclass(
            f"{cls.__name__}Dataclass",
            annotations.items(),
            bases=bases,
            namespace=namespace,
        )
        enhanced_results = {}
        model_classes = []
        lazy_annotated_fields = []
        backrefs = dict()
        backrefs_uselist = dict()

        for field in dataclasses.fields(temp_cls):
            enhanced_result = EnhancedFieldBase.parse(cls, field)
            # take note of self-referencing fields, to assign their type post-dataclass-creation
            if enhanced_result.unwrapped_type is cls:
                lazy_annotated_fields.append(enhanced_result)
            field_type = enhanced_result.unwrapped_type

            # TODO: make only some fields optional
            default = getattr(cls, field.name, None)
            enhanced_results[field.name] = enhanced_result

            if issubclass(field_type, ModelBase):
                if enhanced_result.relation is not None:
                    model_classes.append(field_type)

                if enhanced_result.relation is Relations.ONE_TO_MANY:
                    backrefs_uselist[field.name] = singularname
                    getattr(field_type, BACKREF)[singularname] = field.name

                elif enhanced_result.relation is Relations.MANY_TO_ONE:
                    backrefs[field.name] = pluralname
                    getattr(field_type, BACKREF_USELIST)[pluralname] = field.name

            if enhanced_result.relation is Relations.ONE_TO_MANY:
                field_type = List[field_type]
                default = dataclasses.field(default_factory=list)

            annotations[field.name] = field_type
            namespace[field.name] = default

        namespace[ENHANCED] = enhanced_results
        new_cls = dataclasses.make_dataclass(
            cls.__name__,
            annotations.items(),
            bases=bases,
            namespace=namespace,
        )

        # finally assign self-referencing fields, to the final type
        for enhanced_field in lazy_annotated_fields:
            enhanced_field.unwrapped_type = new_cls

        setattr(new_cls, DOC, namespace[DOC])

        # let users access the class from this model (ex. ApkModel.Provider)
        for model_class in model_classes:
            if issubclass(model_class, ModelBase):
                setattr(new_cls, model_class.__name__, model_class)

        setattr(new_cls, AUTO_SETATTR, chosen_backend.use_autosetattr())
        getattr(new_cls, BACKREF).update(backrefs)
        getattr(new_cls, BACKREF_USELIST).update(backrefs_uselist)
        repo = chosen_backend(new_cls).create_repository()
        new_cls.repository_cls = repo
        return new_cls

    return _wrap
