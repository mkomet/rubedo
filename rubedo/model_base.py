from __future__ import annotations
import abc
import dataclasses
import json
import datetime
import hashlib
from enum import Enum
from typing import Dict


PLURAL_NAME = "__membersname__"
SINGULAR_NAME = "__membername__"
UNIQUE_NAME = "__uniquename__"
BACKREF = "__backrefmodels__"
BACKREF_USELIST = "__backrefmodels_uselist__"
AUTO_SETATTR = "__automaticsetattr__"


@dataclasses.dataclass
class ModelBase(abc.ABC):
    """
    A base class for all models
    Each model class should have the following properties:
        `repository_cls`: `Type[RepositoryBase]` - pointing to the repository class of the model.
        `__membername__`: `str` - specifying the singular name of instances of this model.
        `__membersname__`: `str` - specifying the plural name of instances of this model.
        `__uniquename__`: `str` - specifying a unique name for this model across all others models.
        `__enhancedfields__`: `Dict[str, EnhancedFieldResult]` - a mapping between field names,
                               and their parsed EnhancedFields
        `__supermodels__`: `List[Type[ModelBase]]` - a list of supermodels classes of this model.
    Moreover, if another model is used for one of the fields (file: FileModel for example), the referred model class
    must be accessible using ModelCls.<referred_model_name> (ApkModel.FileModel for example).
    Finally models must also have a `pk` field.
    """

    # TODO: how to define abstract class properties???
    def __getattr__(self, item):
        # if a submodel was not found the usual means,
        # return empty value for it instead of raising AttributeError
        # on the next lookup the attribute will already be set,
        # so this function should not be called (but see XXX below)

        # XXX: for some reason, __getattr__ is called even after we added
        # the attribute to the instances __dict__ (using super().__setattr__)
        if item in self.__dict__:
            return self.__dict__[item]

        if item in getattr(type(self), BACKREF):
            # call the super setattr so we don"t enter an infinite loop
            super().__setattr__(item, None)
            return None
        if item in getattr(type(self), BACKREF_USELIST):
            value = list()
            super().__setattr__(item, value)
            return value
        raise AttributeError(f"{type(self)} object has no attribute '{item}'")

    def __setattr__(self, key, value):
        if getattr(type(self), AUTO_SETATTR) and value is not None:
            backref = getattr(type(self), BACKREF)
            backref_uselist = getattr(type(self), BACKREF_USELIST)
            if key in backref:
                # add this model to the list of models
                backref_value = getattr(value, backref[key])
                try:
                    if self not in backref_value:
                        backref_value.append(self)
                except AttributeError:
                    # since dataclasses __eq__ is done by value, if `self` is still being
                    # built (__init__ hasn"t finished), the check could fail with an attribute
                    # error. In that case, we can be sure `self` is not in the other list since
                    # it wasn"t created yet
                    backref_value.append(self)
            elif key in backref_uselist:
                my_name_at_model = backref_uselist[key]
                for model in value:
                    if getattr(model, my_name_at_model) is not self:
                        setattr(model, my_name_at_model, self)

        super().__setattr__(key, value)

    def __init_subclass__(cls, **kwargs):
        """
        Initialize model classes.
        creating the sets of backrefed fields used for automatic setattr"ing
        a special field __automaticsetattr__ is created for the class, which when set to false, disables this
        functionality allowing backends to provides it instead.
        """
        super().__init_subclass__(**kwargs)

        # fields with a MANY TO ONE relationship
        # mapping between field name, and the name of this model in the *related* model
        setattr(cls, BACKREF, dict())

        # fields with a ONE TO MANY relationship
        # mapping between field name, and the name of this model in the *related* model
        setattr(cls, BACKREF_USELIST, dict())

    def asdict(
        self,
        show_hidden: bool = False,
        show_super: bool = False,
        **kwargs,
    ) -> Dict:
        result = dataclasses.asdict(self)
        # TODO: do this before passing the object to dataclasses.asdict
        keys = list(result.keys())
        if not show_hidden:
            for key in keys:
                if key[0] == "_" or key == "pk":
                    del result[key]
        if not show_super:
            enhanced_results = self.__enhancedfields__
            for field_name, enhanced_result in enhanced_results.items():
                if enhanced_result.unwrapped_type in self.__supermodels__:
                    del result[field_name]

        return result

    def json_dump(self, fp) -> None:
        """
        Dump this model as json to a .write supporting object fp
        datetime objects will be saved using .isoformat()
        bytes objects will be sha1summed and only the hash will be saved
        enum object will be saved using the name
        """
        fp.write(self.json_dumps())

    def json_dumps(self) -> str:
        """
        Dumps this model as json strings
        datetime objects will be saved using .isoformat()
        bytes objects will be sha1summed and only the hash will be saved
        enum object will be saved using the name
        """
        return ModelJsonEncoder(indent=2, sort_keys=True).encode(self.asdict())


class ModelJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            iso = obj.isoformat()
            if obj.tzinfo is None:
                iso += "+00:00"
            return iso
        if isinstance(obj, bytes):
            sha1er = hashlib.sha1()
            sha1er.update(obj)
            return sha1er.hexdigest()
        if isinstance(obj, Enum):
            return obj.name

        try:
            return list(obj)
        except TypeError:
            pass

        return super().default(obj)
