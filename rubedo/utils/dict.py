from typing import Any


class RubedoDict(dict):
    def __getattr__(self, item: str) -> Any:
        return self.__getitem__(item)

    def __setattr__(self, key: str, value: Any) -> None:
        self.__setitem__(key, value)
