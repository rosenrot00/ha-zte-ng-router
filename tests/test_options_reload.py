"""Options changes must reload the config entry so new settings take effect.

Regression test for issue #16: options were only applied after a manual entry
reload or a Home Assistant restart. The integration registers an explicit
update listener; the options flow must not reload a second time.
"""

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "zte_ng_router"


def _module_source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class OptionsReloadTests(unittest.TestCase):
    def test_setup_registers_update_listener(self):
        """async_setup_entry registers the listener and unloads it with the entry."""
        tree = ast.parse(_module_source("__init__.py"))
        setup = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_entry"
        )
        calls = {
            ast.unparse(node.func)
            for node in ast.walk(setup)
            if isinstance(node, ast.Call)
        }
        self.assertIn("entry.async_on_unload", calls)
        self.assertIn("entry.add_update_listener", calls)

    def test_update_listener_reloads_entry(self):
        """The listener calls hass.config_entries.async_reload with the entry id."""
        tree = ast.parse(_module_source("__init__.py"))
        listener = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_update_listener"
        )
        calls = [
            ast.unparse(node.func)
            for node in ast.walk(listener)
            if isinstance(node, ast.Call)
        ]
        self.assertIn("hass.config_entries.async_reload", calls)
        args = [
            node.args[0]
            for node in ast.walk(listener)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "hass.config_entries.async_reload"
            and node.args
        ]
        self.assertTrue(args)
        self.assertEqual(ast.unparse(args[0]), "entry.entry_id")

    def test_options_flow_does_not_reload_twice(self):
        """The options flow must not use OptionsFlowWithReload in parallel."""
        source = _module_source("config_flow.py")
        self.assertNotIn("OptionsFlowWithReload", source)
        self.assertIn("class ZteNgRouterOptionsFlow(config_entries.OptionsFlow):", source)


if __name__ == "__main__":
    unittest.main()
