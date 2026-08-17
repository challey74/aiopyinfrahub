"""Checks over the generated kind hints.

kinds_generated.py and hints_generated.pyi come from
scripts/generate_kinds.py. These tests make sure the two stay consistent
with each other and with the runtime classes, so a bad regeneration fails
CI instead of silently degrading autocomplete. Nothing here touches the
network: the checked-in files are the fixture.
"""

import ast
import re
from pathlib import Path

from aiopyinfrahub.api import Api
from aiopyinfrahub.kinds import KindEndpoint
from aiopyinfrahub.kinds_generated import KindHints

SRC = Path(__file__).resolve().parent.parent / "src" / "aiopyinfrahub"
MODULE = SRC / "kinds_generated.py"
STUB = SRC / "hints_generated.pyi"

# Kinds every Infrahub instance defines, so the sample survives a
# regeneration against a different demo dataset.
SAMPLE = ("BuiltinTag", "CoreRepository", "InfraDevice")


def stub_tree() -> ast.Module:
    """The stub parsed rather than imported: it is a .pyi, so nothing
    can import it at runtime."""
    return ast.parse(STUB.read_text(encoding="utf-8"))


def stub_classes() -> dict[str, list[str]]:
    """Class name -> base class names, for every class in the stub."""
    return {
        node.name: [b.id for b in node.bases if isinstance(b, ast.Name)]
        for node in stub_tree().body
        if isinstance(node, ast.ClassDef)
    }


def test_generated_files_record_their_source():
    for path in (MODULE, STUB):
        header = path.read_text(encoding="utf-8")[:1200]
        assert "do not edit by hand" in header, path.name
        assert re.search(r"Source: https?://\S+", header), path.name


def test_annotations_create_no_runtime_attributes():
    """The hints are static-only; Api.__getattr__ is the real mechanism."""
    for kind in SAMPLE:
        assert kind in KindHints.__annotations__
        assert kind not in KindHints.__dict__
        assert kind not in Api.__dict__


async def test_annotated_kinds_resolve_through_getattr(ih):
    assert isinstance(ih.InfraDevice, KindEndpoint)
    assert ih.InfraDevice.name == "InfraDevice"


async def test_unlisted_kinds_still_work(ih):
    """A kind missing from the demo schema must not be blocked at runtime."""
    assert ih.NotARealKind.name == "NotARealKind"


def test_every_annotation_resolves_to_a_stub_class():
    classes = stub_classes()
    missing = []
    for kind, annotation in KindHints.__annotations__.items():
        # Annotations are strings: "hints.InfraDeviceEndpoint".
        target = str(annotation).split(".")[-1]
        if target not in classes:
            missing.append(f"{kind} -> {target}")
    assert not missing


def test_every_kind_has_filters_fields_and_endpoint_classes():
    classes = stub_classes()
    assert KindHints.__annotations__
    for kind in KindHints.__annotations__:
        assert f"{kind}Filters" in classes
        assert f"{kind}Fields" in classes
        assert classes[f"{kind}Endpoint"] == ["KindEndpoint"]


def test_typed_dicts_are_total_false():
    """Every filter and field key must be optional."""
    source = STUB.read_text(encoding="utf-8")
    for match in re.finditer(r"^class (\w+)\(TypedDict(.*?)\):", source, re.MULTILINE):
        assert "total=False" in match.group(2), match.group(1)


def test_stub_is_not_importable_at_runtime():
    """It is a .pyi; nothing may import it, which is why pyright's
    reportMissingModuleSource is disabled in pyproject.toml."""
    assert not (SRC / "hints_generated.py").exists()
