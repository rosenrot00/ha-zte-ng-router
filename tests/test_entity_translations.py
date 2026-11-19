"""Translated names and registry compatibility, without router requests."""

import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ha_entities import Entity, EntityDescription
from test_router_features import buttons
from test_sensor_defaults import sensors
from test_switch_states import switches

ROOT = Path(__file__).resolve().parents[1] / "custom_components/zte_ng_router"


def load_texts():
    package = ModuleType("zte_text_name_test")
    package.__path__ = [str(ROOT)]

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    symbols = {
        "homeassistant.components.text": {"TextEntity": Entity, "TextEntityDescription": EntityDescription},
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.core": {"HomeAssistant": object},
        "homeassistant.helpers.entity": {
            "DeviceInfo": dict, "EntityCategory": SimpleNamespace(CONFIG="config"),
        },
        "homeassistant.helpers.update_coordinator": {"CoordinatorEntity": CoordinatorEntity},
        "zte_text_name_test.zte_api": {"ZteRouterApi": object},
    }
    modules = {package.__name__: package}
    for name, attributes in symbols.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        modules[name] = module
    spec = importlib.util.spec_from_file_location("zte_text_name_test.text", ROOT / "text.py")
    module = importlib.util.module_from_spec(spec)
    modules[spec.name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


texts = load_texts()


def entities(sensor_module, switch_module, button_module, text_module, hass, entry):
    coordinator = SimpleNamespace(data={}, hass=hass, last_update_success=True)
    api = object()
    result = [("sensor", sensor_module.ZteNgRouterSensor(
        coordinator, entry.entry_id, "Router", key, device_class, unit, state_class,
    )) for key, device_class, unit, state_class in sensor_module.SENSOR_DEFS]
    result.extend(("switch", switch_module.ZteActionSwitch(coordinator, api, entry, "Router", definition))
                  for definition in switch_module.SWITCH_DEFS)
    result.append(("switch", switch_module.ZtePausePollingSwitch(hass, entry, "Router")))
    result.extend(("switch", switch_module.ZteCellLockSwitch(hass, coordinator, api, entry, "Router", kind=kind))
                  for kind in ("4g", "5g"))
    result.extend(("button", button_module.ZteActionButton(coordinator, api, entry, "Router", definition))
                  for definition in button_module.BUTTON_DEFS)
    result.extend(("text", text_module.ZteTextEntity(hass, coordinator, api, entry, "Router", description))
                  for description in text_module.TEXTS)
    return result


def translations(language):
    return json.loads((ROOT / "translations" / f"{language}.json").read_text())["entity"]


def translation_table(document):
    return {f"component.zte_ng_router.entity.{domain}.{key}.name": value["name"]
            for domain, entries in document.items() for key, value in entries.items()}


class EntityTranslationTests(unittest.TestCase):
    def test_all_static_entities_have_translations_and_keep_their_unique_ids(self):
        english = translations("en")
        german = translations("de")
        self.assertEqual(json.loads((ROOT / "strings.json").read_text())["entity"], english)
        instances = entities(sensors, switches, buttons, texts, object(), SimpleNamespace(entry_id="entry"))
        seen = {domain: set() for domain in english}
        for domain, entity in instances:
            with self.subTest(domain=domain, key=entity.translation_key):
                key = entity.translation_key
                seen[domain].add(key)
                self.assertTrue(entity.has_entity_name)
                self.assertEqual(entity.unique_id, f"entry_{key}")
                self.assertFalse(hasattr(entity, "_attr_name"))
                self.assertEqual(entity.name, english[domain][key]["name"])
                self.assertTrue(german[domain][key]["name"].strip())
        self.assertEqual(len(instances), 92)
        for domain in english:
            self.assertEqual(seen[domain], set(english[domain]))
            self.assertEqual(seen[domain], set(german[domain]))

    def test_technical_abbreviations_are_unchanged(self):
        english, german = translations("en"), translations("de")
        for key in ("lte_rsrp", "lte_rsrq", "lte_sinr", "lte_pci", "nr_rsrp", "nr_sinr", "nr_arfcn"):
            self.assertEqual(german["sensor"][key], english["sensor"][key])

    def test_entity_definitions_do_not_override_translated_names(self):
        for definition in switches.SWITCH_DEFS + buttons.BUTTON_DEFS:
            self.assertFalse(hasattr(definition, "name"))
        for description in texts.TEXTS:
            self.assertIsNone(description.name)
        self.assertTrue(all(len(definition) == 4 for definition in sensors.SENSOR_DEFS))


class HomeAssistantNamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            from homeassistant.core import HomeAssistant
            from homeassistant.helpers import device_registry as dr, entity_registry as er
            from homeassistant.helpers.entity_platform import _async_derive_object_ids
        except ImportError:
            self.skipTest("Install Home Assistant to run real entity/registry compatibility tests")
        self.dr, self.er = dr, er
        self.derive_ids = _async_derive_object_ids
        self.files = tempfile.TemporaryDirectory(prefix="zte-entity-names-")
        self.addCleanup(self.files.cleanup)
        self.hass = HomeAssistant(self.files.name)
        self.entry = SimpleNamespace(
            entry_id="entry", domain="zte_ng_router", title="Router", subentries={},
            disabled_by=None, pref_disable_new_entities=False,
        )
        self.hass.config_entries = SimpleNamespace(
            async_get_entry=Mock(return_value=self.entry), async_wait_initialized=AsyncMock(),
        )
        self.device_registry = dr.DeviceRegistry(self.hass)
        await self.device_registry.async_load()
        self.hass.data[dr.DATA_REGISTRY] = self.device_registry
        self.registry = er.EntityRegistry(self.hass)
        await self.registry.async_load()
        self.hass.data[er.DATA_REGISTRY] = self.registry
        self.patches = [
            patch.object(self.device_registry, "async_schedule_save"),
            patch.object(self.registry, "async_schedule_save"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.device = self.device_registry.async_get_or_create(
            config_entry_id="entry", identifiers={("zte_ng_router", "entry")},
            name="Router", manufacturer="ZTE",
        )
        self.modules = [importlib.import_module(f"custom_components.zte_ng_router.{name}")
                        for name in ("sensor", "switch", "button", "text")]

    def instances(self, language):
        table = translation_table(translations(language))
        # Match EntityPlatform's language choice for new IDs.
        from homeassistant.generated import languages
        id_table = table if language in languages.NATIVE_ENTITY_IDS else translation_table(translations("en"))
        result = entities(*self.modules, self.hass, self.entry)
        for domain, entity in result:
            entity.hass = self.hass
            entity.platform_data = SimpleNamespace(
                domain=domain, platform_name="zte_ng_router", component_translations={},
                object_id_component_translations={}, platform_translations=table,
                object_id_platform_translations=id_table,
            )
            entity.internal_integration_suggested_object_id = None
        return result

    def register(self, domain, entity):
        suggested, base = self.derive_ids(entity, SimpleNamespace(entity_namespace=None))
        return self.registry.async_get_or_create(
            domain, "zte_ng_router", entity.unique_id,
            config_entry=self.entry, device_id=self.device.id,
            has_entity_name=entity.has_entity_name,
            original_name=entity.name, translation_key=entity.translation_key,
            suggested_object_id=suggested, object_id_base=base,
        )

    async def test_ha_resolves_english_and_german_names(self):
        for language in ("en", "de"):
            expected = translations(language)
            for domain, entity in self.instances(language):
                with self.subTest(language=language, domain=domain, key=entity.translation_key):
                    self.assertEqual(entity.name, expected[domain][entity.translation_key]["name"])

    async def test_existing_ids_custom_names_and_disabled_choices_survive_upgrade_and_reload(self):
        expected = {}
        custom_friendly_names = {}
        english = translations("en")
        for index, (domain, entity) in enumerate(self.instances("de")):
            name = english[domain][entity.translation_key]["name"]
            legacy = self.registry.async_get_or_create(
                domain, "zte_ng_router", entity.unique_id, config_entry=self.entry,
                device_id=self.device.id, has_entity_name=domain != "sensor",
                original_name=f"Router {name}" if domain == "sensor" else name,
                object_id_base=f"Router {name}" if domain == "sensor" else name,
            )
            changes = {}
            if index % 3 == 0:
                changes.update(name=f"My custom name {index}", new_entity_id=f"{domain}.custom_router_{index}")
            if index % 5 == 0:
                changes["disabled_by"] = self.er.RegistryEntryDisabler.USER
            if changes:
                legacy = self.registry.async_update_entity(legacy.entity_id, **changes)
            expected[(domain, entity.unique_id)] = (legacy.id, legacy.entity_id, legacy.name, legacy.disabled_by)
            if legacy.name is not None:
                custom_friendly_names[(domain, entity.unique_id)] = self.er.async_get_legacy_friendly_name(self.hass, legacy)
            upgraded = self.register(domain, entity)
            self.assertEqual((upgraded.id, upgraded.entity_id, upgraded.name, upgraded.disabled_by),
                             expected[(domain, entity.unique_id)])
            self.assertEqual(upgraded.unique_id, entity.unique_id)
            self.assertEqual(upgraded.original_name, entity.name)
            if upgraded.name is None:
                self.assertEqual(self.er.async_get_full_entity_name(self.hass, upgraded), f"Router {entity.name}")
            else:
                self.assertEqual(self.er.async_get_legacy_friendly_name(self.hass, upgraded),
                                 custom_friendly_names[(domain, entity.unique_id)])
        for domain, entity in self.instances("en"):
            reloaded = self.register(domain, entity)
            self.assertEqual((reloaded.id, reloaded.entity_id, reloaded.name, reloaded.disabled_by),
                             expected[(domain, entity.unique_id)])
            if reloaded.name is not None:
                self.assertEqual(self.er.async_get_legacy_friendly_name(self.hass, reloaded),
                                 custom_friendly_names[(domain, entity.unique_id)])
        self.assertEqual(len(self.registry.entities), 92)

    async def test_fresh_installation_uses_ha_id_generation_and_single_device_prefix(self):
        from homeassistant.util import slugify
        for domain, entity in self.instances("de"):
            registered = self.register(domain, entity)
            self.assertEqual(registered.entity_id, f"{domain}.{slugify('Router ' + entity.suggested_object_id)}")
            self.assertEqual(self.er.async_get_full_entity_name(self.hass, registered), f"Router {entity.name}")


if __name__ == "__main__":
    unittest.main()
