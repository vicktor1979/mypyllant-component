"""Check custom-integration translations without Home Assistant or API calls.

Run from the repository root:
    uv run --no-project python scripts/check_translations.py

This checks the actual config-flow schema via AST and the runtime locale files.
It is not an end-to-end Home Assistant frontend test or a Hassfest replacement.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "mypyllant"


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate translation key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )


def flatten(value: dict, prefix: str = "") -> dict[str, str]:
    result = {}
    for key, child in value.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            result.update(flatten(child, full_key))
        else:
            result[full_key] = child
    return result


def literal_constants() -> dict:
    result = {}
    tree = ast.parse((COMPONENT / "const.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result[target.id] = node.value.value
    return result


def schema_fields(schema_name: str) -> list[str]:
    """Read the real vol.Required/Optional keys, never importing the integration."""
    constants = literal_constants()
    tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == schema_name for t in node.targets):
            continue
        schema_dict = node.value.args[0]
        result = []
        for key in schema_dict.keys:
            value = key.args[0]
            result.append(
                value.value if isinstance(value, ast.Constant) else constants[value.id]
            )
        return result
    raise AssertionError(f"Schema not found: {schema_name}")


class TranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.locales = {
            lang: read_json(COMPONENT / "translations" / f"{lang}.json")
            for lang in ("en", "hu")
        }

    def test_locales_have_matching_leaf_keys(self):
        self.assertEqual(set(flatten(self.locales["en"])), set(flatten(self.locales["hu"])))

    def test_all_options_have_nonempty_human_readable_labels(self):
        fields = schema_fields("OPTIONS_SCHEMA")
        self.assertEqual(len(fields), 18)
        for lang, data in self.locales.items():
            labels = data["options"]["step"]["init"]["data"]
            for field in fields:
                with self.subTest(language=lang, field=field):
                    self.assertIn(field, labels)
                    self.assertTrue(labels[field].strip())
                    self.assertNotEqual(labels[field], field)

    def test_all_hungarian_option_labels_are_translated(self):
        en = self.locales["en"]["options"]["step"]["init"]["data"]
        hu = self.locales["hu"]["options"]["step"]["init"]["data"]
        for key in schema_fields("OPTIONS_SCHEMA"):
            with self.subTest(key=key):
                self.assertNotEqual(hu[key], en[key])

    def test_energy_history_label_and_description(self):
        option = self.locales["hu"]["options"]["step"]["init"]
        self.assertEqual(
            option["data"]["fetch_energy_history"],
            "Energiaelőzmények lekérése (induláskor és újratöltéskor is)",
        )
        self.assertIn("Alapértelmezetten kikapcsolva", option["data_description"]["fetch_energy_history"])
        # The actual option and default are unchanged; labels must not become keys.
        constants = literal_constants()
        self.assertEqual(constants["OPTION_FETCH_ENERGY_HISTORY"], "fetch_energy_history")
        self.assertIs(constants["DEFAULT_FETCH_ENERGY_HISTORY"], False)

    def test_all_login_forms_have_labels(self):
        for lang, data in self.locales.items():
            for step in ("user", "reauth_confirm", "reconfigure"):
                form = data["config"]["step"][step]
                self.assertTrue(form["title"])
                self.assertTrue(form["description"])
                for field in schema_fields("DATA_SCHEMA"):
                    with self.subTest(language=lang, step=step, field=field):
                        self.assertTrue(form["data"][field])

    def test_all_explicit_config_flow_messages_have_translations(self):
        tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
        codes = {"error": set(), "abort": set()}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == "errors":
                        codes["error"].add(node.value.value)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"async_abort", "_abort_if_unique_id_mismatch"}:
                    for kw in node.keywords:
                        if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                            codes["abort"].add(kw.value.value)
        for lang, data in self.locales.items():
            for category, reasons in codes.items():
                for reason in reasons:
                    with self.subTest(language=lang, category=category, reason=reason):
                        self.assertTrue(data["config"][category][reason])

    def test_runtime_files_have_resolved_text_not_core_placeholders(self):
        for lang, data in self.locales.items():
            for key, value in flatten(data).items():
                with self.subTest(language=lang, key=key):
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip())
                    self.assertNotIn("[%key:", value)
                    self.assertNotIn("\ufffd", value)

    def test_descriptions_only_reference_existing_fields(self):
        for data in self.locales.values():
            for category in ("config", "options"):
                for step in data[category]["step"].values():
                    self.assertLessEqual(set(step.get("data_description", {})), set(step["data"]))

    def test_placeholders_match_between_languages(self):
        en = flatten(self.locales["en"])
        hu = flatten(self.locales["hu"])
        for key in en:
            with self.subTest(key=key):
                self.assertEqual(re.findall(r"\{[^{}]+\}", en[key]), re.findall(r"\{[^{}]+\}", hu[key]))

    def test_catalogue_matches_runtime_english(self):
        # Existing upstream strings.json is kept as a catalogue only.
        self.assertEqual(read_json(COMPONENT / "strings.json"), self.locales["en"])

    def test_json_duplicate_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            json.loads('{"fetch_energy_history": "a", "fetch_energy_history": "b"}', object_pairs_hook=reject_duplicate_keys)


if __name__ == "__main__":
    unittest.main(verbosity=2)
