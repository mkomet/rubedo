from __future__ import annotations

import dataclasses
from typing import Dict, Optional

from .group import Group


@dataclasses.dataclass
class Result:
    matches: Dict[int, str]
    # Map match model_pk to matched string in the model's field
    group: Optional[Group]


@dataclasses.dataclass
class SearchResults:
    group: Group
    subresults: Dict[str, SearchResults] = dataclasses.field(default_factory=dict)
    results: Dict[str, Result] = dataclasses.field(default_factory=dict)
    # Mapping of the subgroup field name to SearchResults
