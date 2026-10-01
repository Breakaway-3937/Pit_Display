"""
The contracts in `home/contracts/`, and a validator for the part of JSON
Schema they use.

The validator is small on purpose (no `jsonschema` in the bundle): `type`,
`enum`, `const`, `required`, `properties`, `additionalProperties: false`,
`items`, `minItems`/`maxItems`, `maxLength`, `pattern`, local `$ref`. A
keyword it doesn't know is a bug in this file, so `validate()` refuses one
rather than skipping it.

Two derived schemas go to Ollama as `format`, with `$ref`s inlined and the
keywords its grammar handles badly (`pattern`) removed; the full schema is
still checked afterwards:

* `insight_format()`: the analyst's findings.
* `designer_format()`: the board minus everything code fills in (`schema`,
  `session_uid`, `generated_at`, `models`, `evidence`, every `status`, each
  card's `shape`, each chart's `data`),
  so the designer cannot write a figure into a field nobody checks.
"""

from __future__ import annotations

import copy
import json
import re
from functools import cache
from typing import Any

from app import paths

_KNOWN = {"$schema", "$id", "title", "description", "type", "enum", "const", "required",
          "properties", "additionalProperties", "items", "minItems", "maxItems",
          "maxLength", "pattern", "$ref", "$defs"}


@cache
def load(name: str) -> dict:
    """`board` or `insight`, from home/contracts/<name>.schema.json."""
    path = paths.resource("home", "contracts", f"{name}.schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _type_ok(value: Any, t: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[t]


def validate(value: Any, schema: dict, root: dict | None = None, at: str = "$") -> list[str]:
    """Every way `value` breaks `schema`, as `path: problem` strings. Empty = valid."""
    root = root or schema
    unknown = set(schema) - _KNOWN
    if unknown:
        raise ValueError(f"schema keyword(s) not supported here: {sorted(unknown)} at {at}")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ValueError(f"only local $refs are supported: {ref}")
        return validate(value, root["$defs"][ref.removeprefix("#/$defs/")], root, at)

    errs: list[str] = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            return [f"{at}: expected {'/'.join(types)}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        errs.append(f"{at}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{at}: {value!r} not one of {schema['enum']}")
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{at}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{at}: doesn't match {schema['pattern']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{at}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{at}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errs += validate(item, schema["items"], root, f"{at}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errs.append(f"{at}: missing {key!r}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errs.append(f"{at}: unexpected {key!r}")
        for key, sub in props.items():
            if key in value:
                errs += validate(value[key], sub, root, f"{at}.{key}")
    return errs


def _inline(node: Any, defs: dict) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(copy.deepcopy(defs[node["$ref"].removeprefix("#/$defs/")]), defs)
        return {k: _inline(v, defs) for k, v in node.items()
                if k not in ("$defs", "$schema", "$id", "pattern")}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


@cache
def insight_format() -> dict:
    s = load("insight")
    return _inline(s, s.get("$defs", {}))


@cache
def designer_schema() -> dict:
    """The full board schema less the code-filled fields (still with $refs)."""
    s = copy.deepcopy(load("board"))
    for key in ("schema", "session_uid", "generated_at", "models", "evidence"):
        s["properties"].pop(key, None)
    s["required"] = [k for k in s["required"] if k in s["properties"]]
    # Status is the finding's, set by code: the designer names the finding.
    reading = s["$defs"]["reading"]
    reading["properties"].pop("shape", None)
    reading["properties"].pop("status", None)
    reading["required"] = ["label", "value", "finding"]
    reading["properties"]["finding"] = {"type": "string"}
    sub = s["properties"]["subsystems"]["items"]
    sub["properties"].pop("status", None)
    sub["required"] = ["name", "finding"]
    sub["properties"]["finding"] = {"type": "string"}
    head = s["properties"]["headline"]
    head["properties"].pop("status", None)
    head["required"] = [k for k in head["required"] if k != "status"]
    s["$defs"]["chart"]["properties"].pop("data", None)
    return s


@cache
def designer_format() -> dict:
    s = designer_schema()
    return _inline(s, s["$defs"])
