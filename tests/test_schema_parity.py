"""The models must agree with the schema about every leaf, not just the blank sheet.

``tests/test_blank_and_schema.py`` proves an *empty* sheet round-trips. An empty sheet is
almost entirely nulls, so it says nothing about types: when the schema was tightened to
make weapon range an integer, the blank sheet went on passing while every filled one
failed with ``'60' is not of type 'integer'``.

So this module builds a document in which *every* leaf carries a value invented from the
schema's own declaration, and pushes it through the models and back. A field the models
type differently from the schema fails one of the two ends: pydantic rejects the generated
value, or the serialised value no longer validates.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from scribe40k.models import CharacterSheet
from scribe40k.paths import CHARACTER_SCHEMA


@pytest.fixture(scope="session")
def schema() -> dict:
    return json.loads(CHARACTER_SCHEMA.read_text(encoding="utf-8"))


def _resolve(node: dict, schema: dict) -> dict:
    """Follow a local ``$ref``. The schema uses no remote ones.

    Properties sitting beside a ``$ref`` narrow the target rather than replacing it --
    that is how each skill pins its own ``characteristic`` to a const -- so they are
    merged property by property.
    """
    ref = node.get("$ref")
    if not ref:
        return node
    assert ref.startswith("#/"), f"only local refs are supported, got {ref}"
    target: object = schema
    for token in ref[2:].split("/"):
        target = target[token.replace("~1", "/").replace("~0", "~")]  # type: ignore[index]

    merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}  # type: ignore[dict-item]
    if "properties" in target and "properties" in node:  # type: ignore[operator]
        merged["properties"] = {
            name: {**target["properties"].get(name, {}), **node["properties"].get(name, {})}  # type: ignore[index]
            for name in {*target["properties"], *node["properties"]}  # type: ignore[index]
        }
    return merged


def _types(node: dict) -> list[str]:
    declared = node.get("type")
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, list):
        return list(declared)
    return []


def sample(node: dict, schema: dict) -> object:
    """A value this subschema accepts, preferring a non-null one.

    Deliberately naive: it understands exactly the constructs this schema uses. Anything
    it cannot invent a value for raises, so a new construct is a failing test rather than
    a silently skipped field.
    """
    node = _resolve(node, schema)

    # const before enum: a skill pins its characteristic with a const beside the $ref that
    # carries the full enum, and the narrower of the two is the one that has to hold.
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return next((v for v in node["enum"] if v is not None), node["enum"][0])
    if "anyOf" in node:
        for option in node["anyOf"]:
            resolved = _resolve(option, schema)
            if _types(resolved) != ["null"]:
                return sample(resolved, schema)
        return None

    types = [t for t in _types(node) if t != "null"]
    if not types:
        raise AssertionError(f"no way to invent a value for {node}")
    kind = types[0]

    if kind == "object":
        properties = node.get("properties", {})
        return {name: sample(child, schema) for name, child in properties.items()}
    if kind == "array":
        items = node.get("items")
        return [sample(items, schema)] if items else []
    if kind == "integer":
        low = node.get("minimum", 0)
        return max(low, 1) if node.get("maximum", 100) >= max(low, 1) else low
    if kind == "number":
        return float(node.get("minimum", 1))
    if kind == "boolean":
        return True
    if kind == "string":
        return "x"

    raise AssertionError(f"unhandled schema type {kind!r}")


@pytest.fixture(scope="session")
def fully_populated(schema: dict) -> dict:
    return sample(schema, schema)


def test_the_generated_document_is_actually_valid(schema: dict, fully_populated: dict) -> None:
    """Guards the generator itself: if this fails, the test below proves nothing."""
    errors = sorted(Draft202012Validator(schema).iter_errors(fully_populated), key=str)
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:10])


def test_every_leaf_survives_a_round_trip(schema: dict, fully_populated: dict) -> None:
    """The check that would have caught the weapon-range drift."""
    reparsed = CharacterSheet.model_validate(fully_populated).to_json_dict()

    errors = sorted(Draft202012Validator(schema).iter_errors(reparsed), key=str)
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:10])


def test_no_leaf_changes_value_on_the_way_through(schema: dict, fully_populated: dict) -> None:
    """Types agreeing is not enough; a coerced value is drift too.

    ``"60"`` arriving as ``60`` would satisfy both validators while quietly meaning the
    models and the schema disagree about what the field holds.
    """
    reparsed = CharacterSheet.model_validate(fully_populated).to_json_dict()

    differences = list(_differences(fully_populated, reparsed))
    assert not differences, "\n".join(differences[:10])


def _differences(expected: object, actual: object, path: str = ""):
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in {*expected, *actual}:
            if key not in expected:
                yield f"{path}/{key}: added by the models ({actual[key]!r})"
            elif key not in actual:
                yield f"{path}/{key}: dropped by the models ({expected[key]!r})"
            else:
                yield from _differences(expected[key], actual[key], f"{path}/{key}")
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            yield f"{path}: {len(expected)} item(s) in, {len(actual)} out"
        else:
            for index, (a, b) in enumerate(zip(expected, actual, strict=True)):
                yield from _differences(a, b, f"{path}/{index}")
    elif expected != actual:
        yield f"{path}: {expected!r} in, {actual!r} out"


def test_the_root_keys_match_exactly(schema: dict) -> None:
    """A property added to one side and not the other."""
    assert set(schema["properties"]) == set(CharacterSheet.model_fields)
