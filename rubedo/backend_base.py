import abc
from typing import Tuple, Type

from .repository_base import ModelBase, RepositoryBase

_backend = None


class BackendBase(abc.ABC):
    """
    A base class for all the backends of rubedo - responsible for taking a model class,
    and generating a repository for it.
    """

    @abc.abstractmethod
    def __init__(self, model_cls: Type[ModelBase]):
        self._model_cls = model_cls

    @abc.abstractmethod
    def create_repository(self) -> Type[RepositoryBase]:
        """
        Creates a repository for this instance's model class.
        :return: The created repository
        """
        raise NotImplementedError()

    @classmethod
    def mixins(cls) -> Tuple:
        """
        Defines a tuple of mixin classes, that need to be "injected" into the model class.
        """
        return tuple()

    @classmethod
    @abc.abstractmethod
    def initialize_backend(cls, context):
        raise NotImplementedError()

    @classmethod
    def use_autosetattr(cls) -> bool:
        return True


def set_backend(backend: Type[BackendBase]):
    global _backend
    if _backend is not None:
        raise RuntimeError(
            f"tried to set backend to {backend} after it had already been set to {_backend}",
        )
    if not issubclass(backend, BackendBase):
        raise TypeError(
            f"tried to set backend to {backend}, which doesn't inherit from {BackendBase}",
        )
    _backend = backend


def get_backend() -> Type[BackendBase]:
    global _backend
    if _backend is None:
        from .sqlsorcery import SqlSorceryBackend

        _backend = SqlSorceryBackend
    return _backend
