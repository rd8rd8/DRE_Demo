from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Optional


@dataclass
class HierarchyNode:
    type: str
    label: str
    title: Optional[str] = None


@dataclass
class Article:
    id: str
    number: str
    label: str
    title: Optional[str]
    text: str
    paragraphs: list[str] = field(default_factory=list)
    hierarchy: list[HierarchyNode] = field(default_factory=list)
    source_anchor: Optional[str] = None


@dataclass
class Annex:
    id: str
    label: str
    title: Optional[str]
    reference_note: Optional[str] = None
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)


@dataclass
class Relationship:
    type: str
    target: str


@dataclass
class LegalDocument:
    schema_version: str
    source: dict[str, Any]
    document: dict[str, Any]
    preamble: list[str] = field(default_factory=list)
    recitals: list[dict[str, Any]] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    annexes: list[Annex] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
