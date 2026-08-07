"""Versioned field semantics and lifetime ownership for HICAR input products."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any


class FieldLifetime(str, Enum):
    """How a field varies over the lifetime of a multi-year simulation."""

    INVARIANT = "invariant"
    EPOCH = "epoch"
    CLIMATOLOGY = "climatology"
    TIME_SERIES = "time_series"
    INITIAL_ONLY = "initial_only"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    lifetime: FieldLifetime
    support: str
    interpolation: str
    aliases: tuple[str, ...] = ()
    units: str | None = None

    @classmethod
    def from_mapping(cls, item: dict[str, Any]) -> "FieldSpec":
        return cls(
            name=str(item["name"]),
            lifetime=FieldLifetime(item["lifetime"]),
            support=str(item.get("support", "cell")),
            interpolation=str(item.get("interpolation", "none")),
            aliases=tuple(item.get("aliases", ())),
            units=item.get("units"),
        )


class FieldRegistry:
    """Strict registry preventing time-varying state from leaking into static files."""

    def __init__(self, specs: list[FieldSpec], *, version: str) -> None:
        self.version = version
        self._by_name: dict[str, FieldSpec] = {}
        for spec in specs:
            for name in (spec.name, *spec.aliases):
                if name in self._by_name:
                    raise ValueError(f"duplicate field registry name {name!r}")
                self._by_name[name] = spec

    @classmethod
    def from_json(cls, path: Path) -> "FieldRegistry":
        payload = json.loads(path.read_text())
        return cls(
            [FieldSpec.from_mapping(item) for item in payload["fields"]],
            version=str(payload["version"]),
        )

    @classmethod
    def default(cls) -> "FieldRegistry":
        return cls.from_json(Path(__file__).resolve().parents[1] / "field_registry.json")

    def get(self, name: str) -> FieldSpec:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(
                f"field {name!r} has no lifetime policy in registry {self.version}; "
                "classify it explicitly before publication"
            ) from exc

    def classify(self, name: str, attributes: dict[str, Any] | None = None) -> FieldSpec:
        attributes = attributes or {}
        declared = attributes.get("hicar_lifetime")
        spec = self.get(name)
        if declared is not None and FieldLifetime(str(declared)) is not spec.lifetime:
            raise ValueError(
                f"{name}: file declares lifetime {declared!r}, registry requires "
                f"{spec.lifetime.value!r}"
            )
        return spec

    def canonical_specs(self) -> list[FieldSpec]:
        unique = {spec.name: spec for spec in self._by_name.values()}
        return [unique[name] for name in sorted(unique)]
