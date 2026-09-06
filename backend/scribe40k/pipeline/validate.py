"""Stage 5: turn schema violations and consistency findings into flags.

Everything here produces a flag rather than an exception. A scanned sheet that does not
quite validate is the normal case, not an error condition -- the whole point of the review
step is that a human resolves what the machine could not. Refusing to produce a document
would leave them nothing to correct.

A document so malformed it cannot be loaded into the model at all is the one case that
short-circuits: it gets a flag on the root saying so, and a flag on each value that is
stopping it, since nothing further can be checked until those are fixed.
"""

from __future__ import annotations

import functools
import json

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ..derive import Finding, apply_derivations, check_consistency
from ..models import CharacterSheet
from ..paths import CHARACTER_SCHEMA
from ..pointer import escape
from .report import Flag


@functools.lru_cache(maxsize=1)
def character_validator() -> Draft202012Validator:
    schema = json.loads(CHARACTER_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _pointer_from_path(path) -> str:
    return "".join(f"/{escape(str(part))}" for part in path)


def schema_flags(document: dict) -> list[Flag]:
    """Flags for anything the JSON Schema rejects."""
    flags: list[Flag] = []
    for error in sorted(character_validator().iter_errors(document), key=lambda e: list(e.path)):
        flags.append(
            Flag(
                pointer=_pointer_from_path(error.absolute_path),
                severity="error",
                rule=f"schema.{error.validator}",
                message=f"Does not match the character schema: {error.message}",
                actual=error.instance if _is_jsonable(error.instance) else None,
            )
        )
    return flags


def _is_jsonable(value) -> bool:
    return isinstance(value, str | int | float | bool | type(None))


def model_error_flags(error: ValidationError) -> list[Flag]:
    """Flags for the values that stop a document being loaded at all.

    Deliberately *instead of* the schema's own report rather than alongside it. When the
    models cannot load a document, nothing is derived from it, and the schema then objects
    to every derived key that is consequently absent -- eight "'modifier' is a required
    property" errors about objects the user never touched, none of which they can act on
    and none of which go away when they fix the one field that actually broke. Pydantic
    knows which value it choked on; that is the one worth showing.
    """
    return [
        Flag(
            pointer=_pointer_from_path(item["loc"]),
            severity="error",
            rule="document.invalid_value",
            message=f"This value stops the sheet being loaded: {item.get('msg', 'invalid')}.",
            actual=item.get("input") if _is_jsonable(item.get("input")) else None,
        )
        for item in error.errors()
    ]


def finding_to_flag(finding: Finding) -> Flag:
    return Flag(
        pointer=finding.pointer,
        severity=finding.severity,
        rule=finding.rule,
        message=finding.message,
        expected=finding.expected,
        actual=finding.actual,
    )


def missing_value_flags(sheet: CharacterSheet) -> list[Flag]:
    """Flag the handful of fields whose absence makes a sheet unusable.

    Deliberately short. Flagging every null on a sparsely-filled sheet would bury the
    genuine problems under a hundred notices, which is the failure mode this whole review
    step exists to avoid.
    """
    flags: list[Flag] = []

    if not (sheet.bio.characterName or "").strip():
        flags.append(
            Flag(
                pointer="/bio/characterName",
                severity="warning",
                rule="bio.missing_name",
                message="No character name was read from the sheet.",
            )
        )

    if sheet.wounds.totalWounds is None:
        flags.append(
            Flag(
                pointer="/wounds/totalWounds",
                severity="warning",
                rule="wounds.missing_total",
                message="Total Wounds is blank.",
            )
        )

    return flags


def validate_document(document: dict) -> tuple[dict, list[Flag]]:
    """Derive, check and validate a merged document.

    Returns the finished document and every flag raised against it. The document is
    returned even when it does not validate: a sheet the user can correct beats an error
    they cannot act on.
    """
    flags: list[Flag] = []

    try:
        sheet = CharacterSheet.model_validate(document)
    except ValidationError as exc:
        # The merged document is too malformed to model. Report each real problem where it
        # is, and hand back what we have so the editor can still show the raw values.
        flags.append(
            Flag(
                pointer="",
                severity="error",
                rule="document.unparseable",
                message=(
                    f"{exc.error_count()} value(s) below stop the sheet being loaded. Until "
                    "they are fixed, nothing is derived from it and the other checks do not "
                    "run."
                ),
            )
        )
        flags.extend(model_error_flags(exc))
        return document, flags

    apply_derivations(sheet)
    flags.extend(finding_to_flag(f) for f in check_consistency(sheet))
    flags.extend(missing_value_flags(sheet))

    finished = sheet.to_json_dict()
    flags.extend(schema_flags(finished))

    return finished, flags


def dedupe_flags(flags: list[Flag]) -> list[Flag]:
    """Collapse flags that would highlight the same field for the same reason.

    Both the model and the consistency rules can object to one field. Showing the user two
    badges on one input, saying nearly the same thing, makes the review harder rather than
    more thorough.
    """
    seen: dict[tuple[str, str], Flag] = {}
    order: list[tuple[str, str]] = []

    for flag in flags:
        key = (flag.pointer, flag.rule)
        if key in seen:
            existing = seen[key]
            # Keep whichever carries more for the user to act on.
            if not existing.alternatives and flag.alternatives:
                seen[key] = flag
            continue
        seen[key] = flag
        order.append(key)

    severity_rank = {"error": 0, "warning": 1, "info": 2}
    return sorted(
        (seen[key] for key in order),
        key=lambda f: (severity_rank.get(f.severity, 3), f.pointer),
    )
