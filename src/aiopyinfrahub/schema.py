"""Schema helpers: flattening /api/schema, kind lookup, field selection."""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

# The four sections of the /api/schema payload that carry node schemas.
# `main` is the schema hash and `namespaces` is a summary, neither of which
# describes a queryable kind.
SCHEMA_SECTIONS = ("nodes", "generics", "profiles", "templates")

# Relationship kinds pulled in by the default selection. Component and
# Generic many-relationships are left out: including them makes every list
# query fan out across the graph, which is how the official SDK tunes it.
DEFAULT_REL_KINDS = frozenset({"Attribute", "Parent"})

# Available on every node for free, and the whole of a nested peer's
# selection, which is what makes peers "brief" Records.
BRIEF_FIELDS: dict[str, Any] = {
    "id": None,
    "hfid": None,
    "display_label": None,
    "__typename": None,
}

# LineageSource and LineageOwner are interfaces carrying id, hfid and
# display_label; the pair is metadata about a field, not another object to
# hydrate, so only enough to name it is selected.
LINEAGE_FIELDS: dict[str, Any] = {"id": None, "display_label": None}

# What properties=True adds to every attribute wrapper. `is_visible` was
# removed from Infrahub; `is_protected` is the only flag property left.
ATTR_PROPERTIES: dict[str, Any] = {
    "is_protected": None,
    "is_default": None,
    "updated_at": None,
    "source": LINEAGE_FIELDS,
    "owner": LINEAGE_FIELDS,
}

# RelationshipProperty has exactly these four fields (verified against the
# 1.10 SDL): is_default is attribute-only.
REL_PROPERTIES: dict[str, Any] = {
    "is_protected": None,
    "updated_at": None,
    "source": LINEAGE_FIELDS,
    "owner": LINEAGE_FIELDS,
}


def kind_name(node_schema: dict[str, Any]) -> str:
    """The GraphQL type name for a node schema, namespace + name.

    The `Attribute` namespace is elided by the server, so its kinds are
    named by the bare name.
    """
    namespace = node_schema.get("namespace") or ""
    name = node_schema.get("name") or ""
    return name if namespace == "Attribute" else f"{namespace}{name}"


def flatten(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collapse a /api/schema response into one {kind: node_schema} map."""
    kinds: dict[str, dict[str, Any]] = {}
    for section in SCHEMA_SECTIONS:
        for node_schema in data.get(section) or []:
            kinds[kind_name(node_schema)] = node_schema
    return kinds


def lookup(kinds: dict[str, dict[str, Any]], kind: str) -> dict[str, Any]:
    """Find one kind's schema.

    Raises:
        ValueError: If the branch's schema has no such kind. Kinds are
            instance-specific, so the message lists near misses rather
            than leaving a typo to fail server-side.
    """
    try:
        return kinds[kind]
    except KeyError:
        close = get_close_matches(kind, kinds, n=3)
        hint = " Did you mean: {}?".format(", ".join(close)) if close else ""
        raise ValueError(
            f"{kind!r} is not a kind in this branch's schema.{hint}"
        ) from None


def rel_selection(rel: dict[str, Any], properties: bool = False) -> dict[str, Any]:
    """The selection for one relationship: a brief peer, plus metadata.

    Cardinality-one relationships carry `properties` beside `node`;
    cardinality-many ones carry it inside each edge.
    """
    edge: dict[str, Any] = {"node": dict(BRIEF_FIELDS)}
    if properties:
        edge["properties"] = dict(REL_PROPERTIES)
    if rel.get("cardinality") == "many":
        return {"count": None, "edges": edge}
    return edge


def build_selection(
    node_schema: dict[str, Any],
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    properties: bool = False,
) -> dict[str, Any]:
    """The default field selection for a kind, as a renderable dict.

    GraphQL has no `SELECT *`, so this stands in for one: every attribute
    as `name { value }` plus the relationships worth fetching eagerly.

    Args:
        node_schema: One kind's schema, from lookup().
        include: Relationship names to add even when their kind is not
            fetched by default.
        exclude: Attribute or relationship names to leave out.
        properties: Also select each attribute's and relationship's
            metadata (is_protected, source, owner, ...), which the
            Records hand back through `record.meta(name)`.
    """
    included = set(include or ())
    excluded = set(exclude or ())
    selection: dict[str, Any] = dict(BRIEF_FIELDS)
    attribute: dict[str, Any] = {"value": None}
    if properties:
        attribute.update(ATTR_PROPERTIES)
    for attr in node_schema.get("attributes") or []:
        name = attr["name"]
        if name not in excluded:
            selection[name] = dict(attribute)
    for rel in node_schema.get("relationships") or []:
        name = rel["name"]
        if name in excluded:
            continue
        if rel.get("kind") not in DEFAULT_REL_KINDS and name not in included:
            continue
        selection[name] = rel_selection(rel, properties)
    return selection


def build_input(node_schema: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """Wrap create/upsert/update values in Infrahub's mutation input shapes.

    Attribute inputs are `{value: ...}` and relationship inputs are
    RelatedNodeInput (`{id: ...}` or `{hfid: [...]}`), so bare scalars are
    wrapped by kind. A value that is already the wire shape passes through,
    which is how metadata (`is_protected`, `source`) and hfid references
    stay reachable.
    """
    attributes = {a["name"] for a in node_schema.get("attributes") or []}
    cardinality = {
        r["name"]: r.get("cardinality") for r in node_schema.get("relationships") or []
    }
    data: dict[str, Any] = {}
    for name, value in fields.items():
        if name in attributes:
            data[name] = value if isinstance(value, dict) else {"value": value}
        elif cardinality.get(name) == "many":
            data[name] = [
                peer if isinstance(peer, dict) else {"id": peer} for peer in value or []
            ]
        elif name in cardinality:
            # A bare string is a node id; None clears the relationship.
            if value is None or isinstance(value, dict):
                data[name] = value
            else:
                data[name] = {"id": value}
        else:
            data[name] = value
    return data
