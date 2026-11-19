"""Minimal entity names and descriptions for tests without Home Assistant."""

from dataclasses import dataclass
import json
from pathlib import Path

ENGLISH = json.loads((Path(__file__).resolve().parents[1] /
                     "custom_components/zte_ng_router/translations/en.json").read_text())["entity"]


@dataclass(frozen=True, kw_only=True)
class EntityDescription:
    key: str
    name: str | None = None
    translation_key: str | None = None
    icon: str | None = None
    entity_category: object = None


class Entity:
    @property
    def name(self):
        if hasattr(self, "_attr_name"):
            return self._attr_name
        domain = self.__class__.__module__.rsplit(".", 1)[-1]
        if self.translation_key in ENGLISH.get(domain, {}):
            return ENGLISH[domain][self.translation_key]["name"]
        return self.entity_description.name if hasattr(self, "entity_description") else None

    @property
    def translation_key(self):
        if hasattr(self, "_attr_translation_key"):
            return self._attr_translation_key
        return self.entity_description.translation_key if hasattr(self, "entity_description") else None

    @property
    def has_entity_name(self):
        return self._attr_has_entity_name

    @property
    def unique_id(self):
        return self._attr_unique_id
