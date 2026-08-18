import hashlib
import json
import re

import httpx2
import pytest

import aiopyinfrahub

BASE = "http://infrahub.test"

# Infrahub uses UUID primary keys; these are fixed so tests can reference
# them, and sorted so the fake's stable ordering is sw-1 through sw-5.
DEVICE_IDS = [
    "11111111-1111-4111-8111-111111111101",
    "11111111-1111-4111-8111-111111111102",
    "11111111-1111-4111-8111-111111111103",
    "11111111-1111-4111-8111-111111111104",
    "11111111-1111-4111-8111-111111111105",
]
SITE_ID = "22222222-2222-4222-8222-222222222201"
TAG_IDS = [
    "33333333-3333-4333-8333-333333333301",
    "33333333-3333-4333-8333-333333333302",
]
INTERFACE_ID = "44444444-4444-4444-8444-444444444401"
NEW_ID_PREFIX = "99999999-9999-4999-8999-9999999999"
TASK_ID_PREFIX = "77777777-7777-4777-8777-7777777777"
IP_POOL_ID = "88888888-8888-4888-8888-888888888801"
PREFIX_POOL_ID = "88888888-8888-4888-8888-888888888802"
IP_RESOURCE_ID = "88888888-8888-4888-8888-888888888811"
PREFIX_RESOURCE_ID = "88888888-8888-4888-8888-888888888812"
ARTIFACT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01"
ARTIFACT_DEFINITION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa02"
FILE_NODE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb01"
STORAGE_PREFIX = "cccccccc-cccc-4ccc-8ccc-cccccccccc"

# Fixed metadata the fake hangs off attributes and relationships whenever a
# query selects it, so tests can assert exact values.
ATTR_META = {
    "is_protected": True,
    "is_default": False,
    "updated_at": "2026-08-17T00:00:00Z",
}
REL_META = {"is_protected": False, "updated_at": "2026-08-17T00:00:00Z"}
LINEAGE = {
    "source": {"id": "66666666-6666-4666-8666-666666666601", "display_label": "netbox"},
    "owner": {"id": "66666666-6666-4666-8666-666666666602", "display_label": "otto"},
}


def _node(namespace, name, attributes, relationships, **extra):
    schema = {
        "id": f"schema-{namespace}{name}".lower(),
        "name": name,
        "namespace": namespace,
        "label": name,
        "branch": "aware",
        "attributes": [
            {
                "name": n,
                "kind": k,
                "optional": True,
                "unique": False,
                "read_only": False,
            }
            for n, k in attributes
        ],
        "relationships": [
            {
                "name": n,
                "peer": peer,
                "kind": kind,
                "cardinality": cardinality,
                "identifier": f"{name.lower()}__{n}",
                "optional": True,
                "read_only": False,
            }
            for n, peer, kind, cardinality in relationships
        ],
    }
    schema.update(extra)
    return schema


# The fixed schema the fake serves. TestingInterface deliberately declares
# no default_filter, so get() with a positional non-UUID has a kind to fail
# on; the `interfaces` relationship is Component, so it stays out of the
# default selection until include= asks for it.
SCHEMA = {
    "main": "0000000000000000000000000000face",
    "nodes": [
        _node(
            "Testing",
            "Device",
            [
                ("name", "Text"),
                ("serial", "Text"),
                ("port_count", "Number"),
                ("config", "JSON"),
            ],
            [
                ("site", "TestingSite", "Attribute", "one"),
                ("tags", "BuiltinTag", "Attribute", "many"),
                ("interfaces", "TestingInterface", "Component", "many"),
            ],
            default_filter="name__value",
            human_friendly_id=["name__value"],
            display_labels=["name__value"],
            uniqueness_constraints=[["name__value"]],
        ),
        _node(
            "Testing",
            "Site",
            [("name", "Text")],
            [],
            default_filter="name__value",
            human_friendly_id=["name__value"],
        ),
        _node("Testing", "Interface", [("name", "Text")], []),
        _node(
            "Builtin",
            "Tag",
            [("name", "Text")],
            [],
            default_filter="name__value",
            human_friendly_id=["name__value"],
        ),
    ],
    "generics": [_node("Core", "Node", [], [])],
    "profiles": [_node("Profile", "TestingDevice", [], [])],
    "templates": [_node("Template", "TestingDevice", [], [])],
    "namespaces": [{"name": "Testing"}, {"name": "Builtin"}],
}

SCHEMA_BY_KIND = {
    "{}{}".format(n["namespace"], n["name"]): n
    for section in ("nodes", "generics", "profiles", "templates")
    for n in SCHEMA[section]
}

FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
OPERATION = re.compile(r"\s*(query|mutation)\s*\{\s*([A-Za-z_][A-Za-z0-9_]*)")
TOKEN = re.compile(
    r"""\s*(?:
          (?P<string>"(?:[^"\\]|\\.)*")
        | (?P<number>-?\d+(?:\.\d+)?)
        | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
        | (?P<punct>[{}\[\]:,])
        )""",
    re.VERBOSE,
)


def _body(text, start, open_ch, close_ch):
    """The text between the delimiters that open at `start`."""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            # Skip string literals so delimiters inside them do not count.
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
        i += 1
    raise AssertionError(f"unbalanced {open_ch}{close_ch} in query: {text}")


def parse_fields(body):
    """Split a selection block body into {field: nested body or None}."""
    fields = {}
    i = 0
    while True:
        match = FIELD.search(body, i)
        if not match:
            return fields
        rest = body[match.end() :]
        if rest.lstrip().startswith("{"):
            brace = match.end() + len(rest) - len(rest.lstrip())
            nested = _body(body, brace, "{", "}")
            fields[match.group()] = nested
            i = brace + len(nested) + 2
        else:
            fields[match.group()] = None
            i = match.end()


class _Args:
    """Parses the argument text the client renders.

    The grammar is JSON with bare identifier keys and the GraphQL literals
    true/false/null, which is exactly what graphql.render_value emits.
    """

    def __init__(self, text):
        self.tokens = []
        pos = 0
        while pos < len(text):
            match = TOKEN.match(text, pos)
            if not match:
                raise AssertionError(f"unparseable arguments: {text}")
            pos = match.end()
            self.tokens.append((match.lastgroup, match.group(match.lastgroup)))
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def take(self):
        token = self.peek()
        self.pos += 1
        return token

    def value(self):
        kind, text = self.take()
        if kind in ("string", "number"):
            return json.loads(text)
        if kind == "name":
            # Anything that is not a GraphQL literal is an enum token, which
            # the client renders bare (InfrahubTask's `state:`).
            return {"true": True, "false": False, "null": None}.get(text, text)
        if text == "[":
            items = []
            while self.peek()[1] != "]":
                items.append(self.value())
                if self.peek()[1] == ",":
                    self.take()
            self.take()
            return items
        if text == "{":
            return self.fields("}")
        raise AssertionError(f"unexpected token {text!r}")

    def fields(self, end=None):
        out = {}
        while True:
            kind, text = self.peek()
            if kind is None or text == end:
                if end is not None:
                    self.take()
                return out
            self.take()  # the key
            self.take()  # the colon
            out[text] = self.value()
            if self.peek()[1] == ",":
                self.take()


def parse_query(text):
    """Split a rendered operation into (op, name, args, selection)."""
    match = OPERATION.match(text)
    if not match:
        raise AssertionError(f"unparseable operation: {text}")
    rest = text[match.end() :]
    args = {}
    stripped = rest.lstrip()
    if stripped.startswith("("):
        start = len(rest) - len(stripped)
        args_text = _body(rest, start, "(", ")")
        rest = rest[start + len(args_text) + 2 :]
        args = _Args(args_text).fields()
    selection = parse_fields(_body(rest, rest.index("{"), "{", "}"))
    return match.group(1), match.group(2), args, selection


def project(value, nested):
    """Pick just the selected fields out of a canned payload.

    Lists project elementwise, so an `edges { node { id } }` selection
    over canned data answers with exactly the fields it asked for and no
    others, the way a real GraphQL server would.
    """
    if nested is None:
        return value
    if isinstance(value, list):
        return [project(item, nested) for item in value]
    if value is None:
        return None
    return {k: project(value.get(k), sub) for k, sub in parse_fields(nested).items()}


def project_fields(value, selection):
    """project() over an already-parsed selection block."""
    return {k: project(value.get(k), sub) for k, sub in selection.items()}


def parse_multipart(request):
    """The named parts of a multipart/form-data body, as bytes."""
    boundary = request.headers["Content-Type"].split("boundary=")[1].encode()
    parts = {}
    for chunk in request.content.split(b"--" + boundary):
        head, _, body = chunk.partition(b"\r\n\r\n")
        match = re.search(rb'name="([^"]+)"', head)
        if match:
            # Each part's body is terminated by the CRLF before the next
            # boundary line, which is not part of the content.
            parts[match.group(1).decode()] = (
                body[:-2] if body.endswith(b"\r\n") else body
            )
    return parts


def make_pool(pool_id, resource_id, kind, resource_kind, label, available):
    return {
        "id": pool_id,
        "kind": kind,
        "display_label": label,
        "resource": {"id": resource_id, "kind": resource_kind, "display_label": label},
        "resource_kind": resource_kind,
        # The free list, which allocation really consumes.
        "available": list(available),
        "allocated": [],
    }


# The fake has no diff engine, so DiffTree and DiffTreeSummary answer
# from this one canned payload projected through whatever the query
# selected: the shape is real, the content is fixed. It carries every
# field of both types, and projection drops the ones the caller did not
# ask for (`num_unchanged` is summary-only, `name`/`nodes` tree-only).
DIFF = {
    "base_branch": "main",
    "diff_branch": "feature-x",
    "from_time": "2026-08-17T00:00:00Z",
    "to_time": "2026-08-17T01:00:00Z",
    "name": "diff-feature-x",
    "num_added": 1,
    "num_removed": 0,
    "num_updated": 1,
    "num_unchanged": 3,
    "num_conflicts": 0,
    "num_untracked_base_changes": 0,
    "num_untracked_diff_changes": 0,
    "nodes": [
        {
            "uuid": DEVICE_IDS[0],
            "kind": "TestingDevice",
            "label": "sw-1",
            "status": "UPDATED",
            "path_identifier": f"data/{DEVICE_IDS[0]}",
            "contains_conflict": False,
            "num_added": 0,
            "num_removed": 0,
            "num_updated": 1,
            "num_conflicts": 0,
            "parent": {
                "uuid": SITE_ID,
                "kind": "TestingSite",
                "relationship_name": "site",
            },
            "attributes": [
                {"name": "serial", "status": "UPDATED", "contains_conflict": False}
            ],
        },
        {
            "uuid": DEVICE_IDS[1],
            "kind": "TestingDevice",
            "label": "sw-2",
            "status": "ADDED",
            "path_identifier": f"data/{DEVICE_IDS[1]}",
            "contains_conflict": False,
            "num_added": 1,
            "num_removed": 0,
            "num_updated": 0,
            "num_conflicts": 0,
            "parent": None,
            "attributes": [
                {"name": "name", "status": "ADDED", "contains_conflict": False}
            ],
        },
    ],
}

# What /api/diff/files and /api/diff/artifacts answer with.
DIFF_FILES = {"main": [{"branch": "feature-x", "location": "topology.j2"}]}
DIFF_ARTIFACTS = {ARTIFACT_ID: {"action": "updated", "item_new": {"checksum": "b"}}}

# The `diff` member of both schema-write payloads.
SCHEMA_DIFF = {"added": {"TestingRack": {}}, "changed": {}, "removed": {}}


def make_device(
    pk, name, serial="", port_count=48, config=None, site=None, tags=(), interfaces=()
):
    return {
        "id": pk,
        "name": name,
        "serial": serial,
        "port_count": port_count,
        # A JSON-kind attribute, whose value is itself a dict.
        "config": config,
        "site": site,
        "tags": list(tags),
        "interfaces": list(interfaces),
    }


def make_task(task_id, title="branch create", state="PENDING", **extra):
    task = {
        "id": task_id,
        "title": title,
        "state": state,
        "conclusion": "unknown",
        "progress": None,
        "workflow": "branch_create",
        "branch": "main",
        "created_at": "2026-08-17T00:00:00Z",
        "updated_at": "2026-08-17T00:00:00Z",
        "start_time": "2026-08-17T00:00:00Z",
        "tags": [],
    }
    task.update(extra)
    return task


def bearer(request):
    """The token from an `Authorization: Bearer` header, if there is one."""
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else None


def make_branch(name, description=None, sync_with_git=False, is_default=False):
    return {
        "id": f"55555555-5555-4555-8555-{abs(hash(name)) % 10**12:012d}",
        "name": name,
        "description": description,
        "origin_branch": None if is_default else "main",
        "branched_from": "2026-08-17T00:00:00Z",
        "status": "OPEN",
        "sync_with_git": sync_with_git,
        "is_default": is_default,
        "created_at": "2026-08-17T00:00:00Z",
        "graph_version": 1,
        "schema_differs_from_default_branch": False,
    }


class FakeInfrahub:
    """Minimal in-memory Infrahub served through httpx2.MockTransport.

    The GraphQL route parses the queries this client generates and answers
    them for real: filtering, offset/limit pagination with a true count,
    and mutations that change the store. Anything it does not model fails
    loudly rather than returning a plausible-looking response.
    """

    def __init__(self, devices=None, page_size=50):
        if devices is None:
            devices = [
                make_device(
                    DEVICE_IDS[0],
                    "sw-1",
                    serial="ABC123",
                    config={"ntp": ["10.0.0.1"]},
                    site=SITE_ID,
                    tags=TAG_IDS,
                    interfaces=[INTERFACE_ID],
                ),
                make_device(DEVICE_IDS[1], "sw-2"),
                make_device(DEVICE_IDS[2], "sw-3"),
                make_device(DEVICE_IDS[3], "sw-4"),
                make_device(DEVICE_IDS[4], "sw-5"),
            ]
        self.objects = {
            "TestingDevice": {d["id"]: d for d in devices},
            "TestingSite": {SITE_ID: {"id": SITE_ID, "name": "atl1"}},
            "BuiltinTag": {
                TAG_IDS[0]: {"id": TAG_IDS[0], "name": "prod"},
                TAG_IDS[1]: {"id": TAG_IDS[1], "name": "edge"},
            },
            "TestingInterface": {
                INTERFACE_ID: {"id": INTERFACE_ID, "name": "Ethernet1"}
            },
        }
        self.branches = {
            "main": make_branch("main", is_default=True),
            "feature-x": make_branch("feature-x", description="wip"),
        }
        self.page_size = page_size
        # When set, `limit` is capped like a server enforcing a maximum
        # page size, which the fan-out's offset arithmetic must survive.
        self.max_limit = None
        self.requests = []
        self.created = 0
        # Failure injection for retry tests: each entry is consumed by one
        # request before normal routing. An int is an HTTP status to return,
        # "transport" raises httpx2.ConnectError, and None lets that one
        # request through, which is how a test targets the second of two.
        self.fail_next = []
        # Canned bodies for hand-written queries, keyed by a substring of
        # the query text, so raw-GraphQL tests need no parser support.
        self.canned = {}
        # JWT auth: one account, and opaque tokens the fake issues and then
        # honors. expire_access_token()/expire_refresh_token() are how a
        # test forces the client's refresh and re-login paths.
        self.user = ("otto", "infrahub")
        self.access_tokens = set()
        self.refresh_tokens = set()
        self.logins = 0
        self.refreshes = 0
        # Tasks, plus the state sequence each one walks: every read pops
        # the next entry and the last repeats, so wait() sees progress.
        self.tasks = {}
        self.task_states = {}
        # Resource pools. Allocation is real: the free list shrinks, so a
        # second caller sees the first one's allocation and an exhausted
        # pool errors the way the server's does.
        self.pools = {
            IP_POOL_ID: make_pool(
                IP_POOL_ID,
                IP_RESOURCE_ID,
                "CoreIPAddressPool",
                "IpamIPAddress",
                "management addresses",
                ["10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24"],
            ),
            PREFIX_POOL_ID: make_pool(
                PREFIX_POOL_ID,
                PREFIX_RESOURCE_ID,
                "CoreIPPrefixPool",
                "IpamIPPrefix",
                "site supernet",
                ["10.1.0.0/24", "10.1.1.0/24"],
            ),
        }
        # The object store: identifier -> bytes, plus the three ways a
        # CoreFileObject's content is addressed.
        self.storage_objects = {}
        self.files = {}
        self.file_hfids = {}
        self.file_storage_ids = set()
        self.uploads = []
        self.artifacts = {ARTIFACT_ID: b"interface Ethernet1\n  no shutdown\n"}
        self.artifact_definitions = {ARTIFACT_DEFINITION_ID}
        self.generated = []
        self.transforms = {"device-report"}
        # Stored queries hold real query text, run through the same
        # executor /graphql uses; the fake substitutes no variables, so
        # stored_calls is what a test asserts the passthrough against.
        self.stored_queries = {"device-names": "query { TestingDevice { count } }"}
        self.stored_calls = []
        # Schema management. A load changes the hash and restarts
        # propagation; InfrahubStatus reports the hash as synced only
        # from the `sync_after_polls`-th read onwards.
        self.schema_hash = SCHEMA["main"]
        self.loaded_schemas = []
        self.status_polls = 0
        self.sync_after_polls = 0

    def add_storage_object(self, content, identifier=None):
        """Store bytes; returns the {identifier, checksum} the upload
        routes answer with. Infrahub checksums objects with MD5."""
        if identifier is None:
            identifier = f"{STORAGE_PREFIX}{len(self.storage_objects) + 1:02d}"
        self.storage_objects[identifier] = content
        return {"identifier": identifier, "checksum": hashlib.md5(content).hexdigest()}

    def add_file(self, node_id, kind, hfid, content):
        """Register a CoreFileObject: one node id, one storage id, one
        hfid, all pointing at the same stored bytes."""
        identifier = self.add_storage_object(content)["identifier"]
        self.files[node_id] = identifier
        self.file_hfids[(kind, tuple(hfid))] = identifier
        # Owned by a file object, so /api/storage/object/ answers 403.
        self.file_storage_ids.add(identifier)
        return identifier

    def add_task(self, task_id, states=("COMPLETED",), **extra):
        """Register a task that reports `states` one per read."""
        self.tasks[task_id] = make_task(task_id, state=states[0], **extra)
        self.task_states[task_id] = list(states)
        return self.tasks[task_id]

    def expire_access_token(self):
        """Invalidate every access token, forcing the client to refresh."""
        self.access_tokens.clear()

    def expire_refresh_token(self):
        """Invalidate every refresh token, forcing the client to log in again."""
        self.refresh_tokens.clear()

    def handler(self, request):
        self.requests.append(request)
        if self.fail_next:
            failure = self.fail_next.pop(0)
            if failure == "transport":
                raise httpx2.ConnectError("injected failure")
            if failure is not None:
                return httpx2.Response(
                    failure,
                    json={"data": None, "errors": [{"message": "injected"}]},
                    headers={"Retry-After": "0"},
                )
        path = request.url.path
        if path.startswith("/api/auth/"):
            return self._auth(request, path)
        token = bearer(request)
        if token is not None and token not in self.access_tokens:
            # An expired or revoked JWT: the 401 the client recovers from.
            return httpx2.Response(
                401, json={"data": None, "errors": [{"message": "Expired token"}]}
            )
        if path == "/api/info":
            return httpx2.Response(
                200, json={"deployment_id": "fake-deployment", "version": "1.10.8"}
            )
        if path == "/api/schema":
            # The schema is branch-aware on a real server; the fake serves
            # the same one for every branch and records the query param.
            # Only the hash moves, so a load is observable.
            return httpx2.Response(200, json={**SCHEMA, "main": self.schema_hash})
        if path in ("/api/schema/load", "/api/schema/check"):
            return self._schema_write(request, path)
        if path.startswith("/api/storage/"):
            return self._storage(request, path)
        if path.startswith("/api/artifact/"):
            return self._artifact(request, path)
        if path.startswith("/api/transform/"):
            return self._transform(request, path)
        if path.startswith("/api/query/"):
            return self._stored_query(request, path)
        if path.startswith("/api/diff/"):
            return self._diff_rest(path)
        if path == "/graphql" or path.startswith("/graphql/"):
            return self._graphql(request)
        return httpx2.Response(500, json={"error": f"unhandled path {path}"})

    @staticmethod
    def _not_found(message):
        return httpx2.Response(
            404, json={"data": None, "errors": [{"message": message}]}
        )

    def _schema_write(self, request, path):
        body = json.loads(request.content)
        if "schemas" not in body:
            return httpx2.Response(
                422, json={"data": None, "errors": [{"message": "schemas is required"}]}
            )
        if path.endswith("/check"):
            # The check route answers 202 with the diff it would apply.
            return httpx2.Response(202, json={"diff": SCHEMA_DIFF, "warnings": []})
        previous = self.schema_hash
        self.loaded_schemas.extend(body["schemas"])
        self.schema_hash = f"{len(self.loaded_schemas):032x}"
        # A load restarts propagation: the workers have to pick the new
        # hash up before InfrahubStatus calls it synced again.
        self.status_polls = 0
        return httpx2.Response(
            200,
            json={
                "hash": self.schema_hash,
                "previous_hash": previous,
                "diff": SCHEMA_DIFF,
                "warnings": [],
                "schema_updated": True,
            },
        )

    def _object(self, identifier):
        content = self.storage_objects.get(identifier)
        if content is None:
            return self._not_found(f"Object {identifier} was not found.")
        return httpx2.Response(
            200, content=content, headers={"Content-Type": "application/octet-stream"}
        )

    def _storage(self, request, path):
        # httpx2 hands back a decoded path, so the identifiers the client
        # percent-encoded are already readable here.
        rest = path.removeprefix("/api/storage/")
        if rest == "upload/content":
            content = json.loads(request.content).get("content") or ""
            return httpx2.Response(200, json=self.add_storage_object(content.encode()))
        if rest == "upload/file":
            parts = parse_multipart(request)
            if "file" not in parts:
                return httpx2.Response(
                    422,
                    json={"data": None, "errors": [{"message": "file is required"}]},
                )
            self.uploads.append(parts)
            return httpx2.Response(200, json=self.add_storage_object(parts["file"]))
        if rest.startswith("object/"):
            identifier = rest.removeprefix("object/")
            if identifier in self.file_storage_ids:
                # A CoreFileObject owns it, so this route refuses and the
                # by-storage-id route is the one that serves it.
                return httpx2.Response(
                    403,
                    json={
                        "data": None,
                        "errors": [{"message": "Use the file object routes."}],
                    },
                )
            return self._object(identifier)
        if rest.startswith("files/by-hfid/"):
            kind = rest.removeprefix("files/by-hfid/")
            # `hfid` is repeatable, one parameter per part of the id.
            hfid = tuple(request.url.params.get_list("hfid"))
            identifier = self.file_hfids.get((kind, hfid))
            if identifier is None:
                return self._not_found(f"No {kind} with hfid {list(hfid)}.")
            return self._object(identifier)
        if rest.startswith("files/by-storage-id/"):
            identifier = rest.removeprefix("files/by-storage-id/")
            if identifier not in self.file_storage_ids:
                return self._not_found(f"No file object owns {identifier}.")
            return self._object(identifier)
        if rest.startswith("files/"):
            node_id = rest.removeprefix("files/")
            identifier = self.files.get(node_id)
            if identifier is None:
                return self._not_found(f"No file object {node_id}.")
            return self._object(identifier)
        return httpx2.Response(500, json={"error": f"unhandled path {path}"})

    def _artifact(self, request, path):
        rest = path.removeprefix("/api/artifact/")
        if rest.startswith("generate/"):
            definition = rest.removeprefix("generate/")
            if definition not in self.artifact_definitions:
                return self._not_found(f"No artifact definition {definition}.")
            self.generated.append(
                {
                    "definition": definition,
                    "nodes": json.loads(request.content).get("nodes"),
                    "branch": request.url.params.get("branch"),
                }
            )
            return httpx2.Response(200, json=None)
        content = self.artifacts.get(rest)
        if content is None:
            return self._not_found(f"No artifact {rest}.")
        return httpx2.Response(
            200, content=content, headers={"Content-Type": "text/plain"}
        )

    def _transform(self, request, path):
        language, _, transform_id = path.removeprefix("/api/transform/").partition("/")
        if transform_id not in self.transforms:
            return self._not_found(f"No transform {transform_id}.")
        # Everything but branch and at rides along as a GraphQL variable.
        params = {
            k: v for k, v in request.url.params.items() if k not in ("branch", "at")
        }
        if language == "python":
            return httpx2.Response(
                200, json={"transform": transform_id, "params": params}
            )
        if language == "jinja2":
            # PlainTextResponse on the real server, so the render is the
            # whole body rather than a JSON document wrapping it.
            rendered = "\n".join(f"{k}: {v}" for k, v in sorted(params.items()))
            return httpx2.Response(200, text=f"# {transform_id}\n{rendered}")
        return self._not_found(f"No {language} transforms.")

    def _stored_query(self, request, path):
        query_id = path.removeprefix("/api/query/")
        query = self.stored_queries.get(query_id)
        if query is None:
            return self._not_found(f"No stored query {query_id}.")
        self.stored_calls.append(
            {
                "id": query_id,
                "variables": json.loads(request.content).get("variables"),
                "branch": request.url.params.get("branch"),
                "at": request.url.params.get("at"),
                "update_group": request.url.params.get("update_group"),
                "subscribers": request.url.params.get_list("subscribers"),
            }
        )
        # The saved query runs through the same executor as /graphql; the
        # fake substitutes no variables, which is what stored_calls is for.
        return self._execute(query)

    def _diff_rest(self, path):
        if path == "/api/diff/files":
            return httpx2.Response(200, json=DIFF_FILES)
        if path == "/api/diff/artifacts":
            return httpx2.Response(200, json=DIFF_ARTIFACTS)
        return httpx2.Response(500, json={"error": f"unhandled path {path}"})

    def _auth(self, request, path):
        if path == "/api/auth/login":
            body = json.loads(request.content)
            if (body.get("username"), body.get("password")) != self.user:
                return self._unauthorized("Incorrect credentials")
            self.logins += 1
            access, refresh = f"access-{self.logins}", f"refresh-{self.logins}"
            self.access_tokens.add(access)
            self.refresh_tokens.add(refresh)
            return httpx2.Response(
                200, json={"access_token": access, "refresh_token": refresh}
            )
        if path == "/api/auth/refresh":
            # The refresh token arrives in the header; the route takes no body.
            if bearer(request) not in self.refresh_tokens:
                return self._unauthorized("Expired refresh token")
            self.refreshes += 1
            access = f"access-r{self.refreshes}"
            self.access_tokens.add(access)
            return httpx2.Response(200, json={"access_token": access})
        if path == "/api/auth/logout":
            # X-INFRAHUB-KEY is not accepted here, only the access token.
            if bearer(request) not in self.access_tokens:
                return self._unauthorized("Not authenticated")
            self.access_tokens.discard(bearer(request))
            return httpx2.Response(200, json=None)
        return httpx2.Response(500, json={"error": f"unhandled path {path}"})

    @staticmethod
    def _unauthorized(message):
        return httpx2.Response(
            401, json={"data": None, "errors": [{"message": message}]}
        )

    def _graphql(self, request):
        return self._execute(json.loads(request.content)["query"])

    def _execute(self, text):
        """Run one rendered operation, whether it arrived on /graphql or
        was pulled out of the stored-query store."""
        for needle, payload in self.canned.items():
            if needle in text:
                return httpx2.Response(200, json=payload)
        op, name, args, selection = parse_query(text)
        if op == "mutation":
            return self._mutation(name, args, selection)
        if name == "Branch":
            return self._branch_query(args, selection)
        if name == "InfrahubTask":
            return self._task_query(args, selection)
        if name == "InfrahubSearchAnywhere":
            return self._search_query(args, selection)
        if name == "InfrahubStatus":
            return self._status_query(selection)
        if name in ("DiffTree", "DiffTreeSummary"):
            return self._diff_query(name, args, selection)
        if name == "InfrahubResourcePoolUtilization":
            return self._pool_utilization(args, selection)
        if name == "InfrahubResourcePoolAllocated":
            return self._pool_allocated(args, selection)
        if name == "InfrahubPathTraversal":
            return self._path_traversal(args, selection)
        if name == "InfrahubReachableNodes":
            return self._reachable_nodes(args, selection)
        if name not in SCHEMA_BY_KIND:
            return self._errors(f'Cannot query field "{name}" on type "Query".')
        return self._node_query(name, args, selection)

    def _status_query(self, selection):
        """InfrahubStatus reports the schema hash as synced only from the
        sync_after_polls-th read on, which is what drives the
        convergence poll."""
        payload = {
            "summary": {
                "schema_hash_synced": self.status_polls >= self.sync_after_polls
            }
        }
        self.status_polls += 1
        return httpx2.Response(
            200, json={"data": {"InfrahubStatus": project_fields(payload, selection)}}
        )

    def _diff_query(self, name, args, selection):
        """DiffTree and DiffTreeSummary answer from the canned DIFF
        payload projected through the selection: the fake has no diff
        engine, so the shape is honest and the content is fixed."""
        if args.get("branch") != DIFF["diff_branch"]:
            # No diff held for that branch, which the server reports as a
            # null payload rather than as an error.
            return httpx2.Response(200, json={"data": {name: None}})
        return httpx2.Response(
            200, json={"data": {name: project_fields(DIFF, selection)}}
        )

    def _pool_get_resource(self, name, data, selection):
        """The GetResource mutations allocate for real: the pool's free
        list shrinks and an exhausted pool errors."""
        pool = self.pools.get(data.get("id"))
        if pool is None:
            return self._errors(f"Pool {data.get('id')} was not found.")
        if not pool["available"]:
            return self._errors(f"No available resource in pool {pool['id']}.")
        self.created += 1
        node = {
            "id": f"{NEW_ID_PREFIX}{self.created:02d}",
            "kind": pool["resource_kind"],
            "identifier": data.get("identifier"),
            "display_label": pool["available"].pop(0),
            "branch": "main",
        }
        pool["allocated"].append(node)
        payload = {"ok": True, "node": node}
        return httpx2.Response(
            200, json={"data": {name: project_fields(payload, selection)}}
        )

    def _pool_utilization(self, args, selection):
        pool = self.pools.get(args.get("pool_id"))
        if pool is None:
            return self._errors(f"Pool {args.get('pool_id')} was not found.")
        total = len(pool["available"]) + len(pool["allocated"])
        used = 100.0 * len(pool["allocated"]) / total if total else 0.0
        payload = {
            "count": 1,
            "utilization": used,
            "utilization_branches": 0.0,
            "utilization_default_branch": used,
            "edges": [
                {
                    "node": {
                        **pool["resource"],
                        "utilization": used,
                        "utilization_branches": 0.0,
                        "utilization_default_branch": used,
                        "weight": total,
                    }
                }
            ],
        }
        return httpx2.Response(
            200,
            json={
                "data": {
                    "InfrahubResourcePoolUtilization": project_fields(
                        payload, selection
                    )
                }
            },
        )

    def _pool_allocated(self, args, selection):
        pool = self.pools.get(args.get("pool_id"))
        if pool is None:
            return self._errors(f"Pool {args.get('pool_id')} was not found.")
        if args.get("resource_id") != pool["resource"]["id"]:
            return self._errors(
                f"Resource {args.get('resource_id')} is not in pool {pool['id']}."
            )
        nodes = pool["allocated"]
        offset = args.get("offset", 0)
        limit = args.get("limit", self.page_size)
        payload = {
            "count": len(nodes),
            "edges": [{"node": node} for node in nodes[offset : offset + limit]],
        }
        return httpx2.Response(
            200,
            json={
                "data": {
                    "InfrahubResourcePoolAllocated": project_fields(payload, selection)
                }
            },
        )

    def _adjacency(self):
        """Undirected adjacency over every relationship in the store,
        which is the graph the traversal queries walk."""
        edges = {}
        for kind, store in self.objects.items():
            for obj in store.values():
                for rel in SCHEMA_BY_KIND[kind]["relationships"]:
                    peers = obj.get(rel["name"]) or []
                    if isinstance(peers, str):
                        peers = [peers]
                    for peer in peers:
                        edges.setdefault(obj["id"], []).append((peer, rel))
                        edges.setdefault(peer, []).append((obj["id"], rel))
        return edges

    def _path_node(self, node_id):
        """One node in PathNodeType's shape, or None if it is unknown."""
        for kind, store in self.objects.items():
            obj = store.get(node_id)
            if obj is not None:
                return {
                    "id": node_id,
                    "kind": kind,
                    "label": kind,
                    "display_label": obj.get("name"),
                    "hfid": self._hfid(kind, obj),
                }
        return None

    def _shortest_paths(self, source_id, max_depth):
        """Breadth-first shortest path from the source to every node
        within max_depth, each as a list of (node id, relationship)."""
        edges = self._adjacency()
        paths = {source_id: []}
        frontier = [source_id]
        for _ in range(max_depth):
            following = []
            for node_id in frontier:
                for peer, rel in edges.get(node_id, []):
                    if peer in paths:
                        continue
                    paths[peer] = paths[node_id] + [(peer, rel)]
                    following.append(peer)
            frontier = following
        return paths

    def _render_path(self, source_id, hops):
        rendered = [{"node": self._path_node(source_id), "relationship": None}]
        for node_id, rel in hops:
            rendered.append(
                {
                    "node": self._path_node(node_id),
                    # The fake knows one name per relationship, so both
                    # sides of a hop report the same one.
                    "relationship": {
                        "kind": rel["kind"],
                        "from_rel": rel["name"],
                        "to_rel": rel["name"],
                        "from_label": rel["name"],
                        "to_label": rel["name"],
                    },
                }
            )
        return {"depth": len(hops), "hops": rendered}

    def _path_traversal(self, args, selection):
        data = args.get("data") or {}
        source = self._path_node(data.get("source_id"))
        destination = self._path_node(data.get("destination_id"))
        if source is None or destination is None:
            return self._errors("Both the source and the destination must exist.")
        paths = self._shortest_paths(data["source_id"], data.get("max_depth", 5))
        hops = paths.get(data["destination_id"])
        found = [] if hops is None else [self._render_path(data["source_id"], hops)]
        payload = {
            "count": len(found),
            "truncated_at_depth": None,
            "excluded_kinds": [],
            "source": source,
            "destination": destination,
            "paths": found[: data.get("max_paths", 10)],
        }
        return httpx2.Response(
            200,
            json={
                "data": {"InfrahubPathTraversal": project_fields(payload, selection)}
            },
        )

    def _reachable_nodes(self, args, selection):
        data = args.get("data") or {}
        source_id = data.get("source_id")
        source = self._path_node(source_id)
        if source is None:
            return self._errors(f"Node {source_id} was not found.")
        kinds = set(data.get("target_kinds") or [])
        dependencies = []
        for node_id, hops in self._shortest_paths(
            source_id, data.get("max_depth", 5)
        ).items():
            node = self._path_node(node_id)
            if node_id == source_id or node["kind"] not in kinds:
                continue
            dependencies.append(
                {
                    "depth": len(hops),
                    "node": node,
                    "path": self._render_path(source_id, hops),
                }
            )
        dependencies.sort(key=lambda dep: (dep["depth"], dep["node"]["id"]))
        # `count` is the number of entries returned, so it is taken after
        # max_results has cut the list down.
        dependencies = dependencies[: data.get("max_results", 50)]
        payload = {
            "count": len(dependencies),
            "source": source,
            "dependencies": dependencies,
        }
        return httpx2.Response(
            200,
            json={
                "data": {"InfrahubReachableNodes": project_fields(payload, selection)}
            },
        )

    @staticmethod
    def _errors(message):
        return httpx2.Response(
            200, json={"data": None, "errors": [{"message": message}]}
        )

    def _hfid(self, kind, obj):
        parts = SCHEMA_BY_KIND[kind].get("human_friendly_id") or []
        return [obj.get(part.removesuffix("__value")) for part in parts]

    def _matches(self, kind, obj, args):
        for key, value in args.items():
            if key in ("offset", "limit", "order", "partial_match"):
                continue
            if key == "ids":
                if obj["id"] not in value:
                    return False
            elif key == "hfid":
                if self._hfid(kind, obj) != list(value):
                    return False
            elif key.endswith("__value"):
                if obj.get(key.removesuffix("__value")) != value:
                    return False
            else:
                raise AssertionError(f"unsupported filter {key!r} on {kind}")
        return True

    def _node_query(self, kind, args, selection):
        matches = [
            obj
            for obj in self.objects.get(kind, {}).values()
            if self._matches(kind, obj, args)
        ]
        # A stable ordering, which is what makes offset/limit meaningful.
        matches.sort(key=lambda obj: obj["id"])
        offset = args.get("offset", 0)
        limit = args.get("limit", self.page_size)
        if self.max_limit is not None:
            limit = min(limit, self.max_limit)
        payload = {}
        if "count" in selection:
            payload["count"] = len(matches)
        if "edges" in selection:
            node_selection = parse_fields(parse_fields(selection["edges"])["node"])
            payload["edges"] = [
                {"node": self._render(kind, obj, node_selection)}
                for obj in matches[offset : offset + limit]
            ]
        return httpx2.Response(200, json={"data": {kind: payload}})

    def _render(self, kind, obj, selection):
        schema = SCHEMA_BY_KIND[kind]
        attributes = {a["name"] for a in schema["attributes"]}
        relationships = {r["name"]: r for r in schema["relationships"]}
        node = {}
        for field, nested in selection.items():
            if field == "id":
                node["id"] = obj["id"]
            elif field == "hfid":
                node["hfid"] = self._hfid(kind, obj)
            elif field == "display_label":
                node["display_label"] = obj.get("name")
            elif field == "__typename":
                node["__typename"] = kind
            elif field in attributes:
                selected = parse_fields(nested or "")
                node[field] = {
                    "value": obj.get(field),
                    **self._metadata(
                        ATTR_META, {k: v for k, v in selected.items() if k != "value"}
                    ),
                }
            elif field in relationships:
                node[field] = self._render_rel(
                    relationships[field], obj, parse_fields(nested or "")
                )
            else:
                raise AssertionError(f"unknown field {field!r} on {kind}")
        return node

    @staticmethod
    def _metadata(values, selection):
        """Render the metadata fields a query selected; empty when none."""
        rendered = {}
        for field, nested in selection.items():
            if field in LINEAGE:
                peer = LINEAGE[field]
                rendered[field] = {k: peer[k] for k in parse_fields(nested or "")}
            else:
                rendered[field] = values[field]
        return rendered

    def _render_rel(self, rel, obj, selection):
        peer_kind = rel["peer"]
        peers = self.objects[peer_kind]
        if rel["cardinality"] == "many":
            # Cardinality-many hangs `properties` off each edge, beside node.
            edges = parse_fields(selection["edges"])
            edge_selection = parse_fields(edges["node"])
            related = [peers[i] for i in obj.get(rel["name"]) or []]
            return {
                "count": len(related),
                "edges": [
                    {
                        "node": self._render(peer_kind, peer, edge_selection),
                        **self._rel_properties(edges),
                    }
                    for peer in related
                ],
            }
        peer_id = obj.get(rel["name"])
        peer = peers.get(peer_id) if peer_id else None
        node_selection = parse_fields(selection["node"])
        return {
            "node": self._render(peer_kind, peer, node_selection) if peer else None,
            **self._rel_properties(selection),
        }

    def _rel_properties(self, selection):
        if "properties" not in selection:
            return {}
        return {
            "properties": self._metadata(
                REL_META, parse_fields(selection["properties"])
            )
        }

    def _unwrap(self, kind, data):
        """Strip the input wrappers back off a mutation's `data` block."""
        schema = SCHEMA_BY_KIND[kind]
        attributes = {a["name"] for a in schema["attributes"]}
        relationships = {r["name"]: r for r in schema["relationships"]}
        values = {}
        for key, value in data.items():
            if key in ("id", "hfid"):
                values[key] = value
            elif key in attributes:
                values[key] = value.get("value")
            elif relationships.get(key, {}).get("cardinality") == "many":
                values[key] = [peer["id"] for peer in value]
            elif key in relationships:
                values[key] = value["id"] if value else None
            else:
                raise AssertionError(f"unknown input field {key!r} on {kind}")
        return values

    def _find(self, kind, values):
        store = self.objects.setdefault(kind, {})
        if values.get("id"):
            return store.get(values["id"])
        keys = [
            part.removesuffix("__value")
            for part in SCHEMA_BY_KIND[kind].get("human_friendly_id") or []
        ]
        if not keys:
            return None
        wanted = values.get("hfid") or [values.get(k) for k in keys]
        for obj in store.values():
            if [obj.get(k) for k in keys] == list(wanted):
                return obj
        return None

    def _new(self, kind, values):
        self.created += 1
        obj = {"id": values.get("id") or f"{NEW_ID_PREFIX}{self.created:02d}"}
        for rel in SCHEMA_BY_KIND[kind]["relationships"]:
            if rel["cardinality"] == "many":
                obj[rel["name"]] = []
        obj.update({k: v for k, v in values.items() if k not in ("id", "hfid")})
        self.objects.setdefault(kind, {})[obj["id"]] = obj
        return obj

    def _mutation(self, name, args, selection):
        if name.startswith("Branch"):
            return self._branch_mutation(name, args, selection)
        if name in ("RelationshipAdd", "RelationshipRemove"):
            return self._relationship_mutation(name, args.get("data") or {})
        if name == "ConvertObjectType":
            return self._convert(args.get("data") or {})
        if name in (
            "InfrahubIPAddressPoolGetResource",
            "InfrahubIPPrefixPoolGetResource",
        ):
            return self._pool_get_resource(name, args.get("data") or {}, selection)
        match = re.fullmatch(r"(.+?)(Create|Upsert|Update|Delete)", name)
        if not match or match.group(1) not in SCHEMA_BY_KIND:
            return self._errors(f'Unknown mutation "{name}".')
        kind, action = match.group(1), match.group(2)
        values = self._unwrap(kind, args.get("data") or {})
        target = self._find(kind, values)
        if action == "Delete":
            if target is None:
                return self._errors(f"{kind} to delete was not found.")
            del self.objects[kind][target["id"]]
            return httpx2.Response(200, json={"data": {name: {"ok": True}}})
        if action == "Create" or (action == "Upsert" and target is None):
            target = self._new(kind, values)
        elif target is None:
            return self._errors(f"{kind} to update was not found.")
        else:
            target.update({k: v for k, v in values.items() if k not in ("id", "hfid")})
        payload = {"ok": True}
        if "object" in selection:
            payload["object"] = self._render(
                kind, target, parse_fields(selection["object"])
            )
        return httpx2.Response(200, json={"data": {name: payload}})

    def _relationship_mutation(self, name, data):
        """RelationshipAdd/Remove edit one relationship's peer list in place."""
        for kind, store in self.objects.items():
            obj = store.get(data.get("id"))
            if obj is None:
                continue
            schema = {r["name"]: r for r in SCHEMA_BY_KIND[kind]["relationships"]}
            rel = data.get("name")
            if rel not in schema:
                return self._errors(f"{kind} has no relationship {rel}.")
            # RelatedNodeInput addresses a peer by id or by hfid.
            found = [self._find(schema[rel]["peer"], node) for node in data["nodes"]]
            if None in found:
                return self._errors(f"A peer in {data['nodes']} was not found.")
            peers = [peer["id"] for peer in found]
            current = list(obj.get(rel) or [])
            if name == "RelationshipAdd":
                current += [p for p in peers if p not in current]
            else:
                current = [p for p in current if p not in peers]
            obj[rel] = current
            return httpx2.Response(200, json={"data": {name: {"ok": True}}})
        return self._errors(f"Node {data.get('id')} was not found.")

    def _convert(self, data):
        """ConvertObjectType rehomes a node under another kind.

        Its `node` is a GenericScalar on the server, so the payload is
        plain JSON rather than a selection the client chose.
        """
        target = data.get("target_kind")
        if target not in SCHEMA_BY_KIND:
            return self._errors(f"Unknown kind {target}.")
        source = None
        for store in self.objects.values():
            if data.get("node_id") in store:
                source = store.pop(data["node_id"])
                break
        if source is None:
            return self._errors(f"Node {data.get('node_id')} was not found.")
        values = {"id": source["id"]}
        for field, mapping in (data.get("fields_mapping") or {}).items():
            values[field] = source.get(mapping.get("source_field", field))
        for rel in SCHEMA_BY_KIND[target]["relationships"]:
            if rel["cardinality"] == "many":
                values.setdefault(rel["name"], [])
        self.objects.setdefault(target, {})[values["id"]] = values
        node = {
            "id": values["id"],
            "__typename": target,
            "display_label": values.get("name"),
        }
        return httpx2.Response(
            200, json={"data": {"ConvertObjectType": {"ok": True, "node": node}}}
        )

    def _task_query(self, args, selection):
        tasks = list(self.tasks.values())
        if "ids" in args:
            tasks = [t for t in tasks if t["id"] in args["ids"]]
        if "state" in args:
            tasks = [t for t in tasks if t["state"] in args["state"]]
        tasks.sort(key=lambda task: task["id"])
        offset = args.get("offset", 0)
        limit = args.get("limit", self.page_size)
        payload = {}
        if "count" in selection:
            payload["count"] = len(tasks)
        if "edges" in selection:
            node_selection = parse_fields(parse_fields(selection["edges"])["node"])
            page = tasks[offset : offset + limit]
            payload["edges"] = [
                {"node": {k: task.get(k) for k in node_selection}} for task in page
            ]
            # Advanced after rendering, so the first read reports states[0].
            for task in page:
                self._advance(task)
        return httpx2.Response(200, json={"data": {"InfrahubTask": payload}})

    def _advance(self, task):
        """Step a task to its next state; the last one repeats forever."""
        states = self.task_states.get(task["id"]) or []
        if len(states) > 1:
            states.pop(0)
            task["state"] = states[0]

    def _search_query(self, args, selection):
        """InfrahubSearchAnywhere answers with `id` and `kind` per hit."""
        q = str(args.get("q", "")).lower()
        hits = [
            {"id": obj["id"], "kind": kind}
            for kind, store in sorted(self.objects.items())
            for obj in store.values()
            if q in str(obj.get("name") or "").lower()
        ]
        if args.get("limit") is not None:
            hits = hits[: args["limit"]]
        node_selection = parse_fields(parse_fields(selection["edges"])["node"])
        return httpx2.Response(
            200,
            json={
                "data": {
                    "InfrahubSearchAnywhere": {
                        "count": len(hits),
                        "edges": [
                            {"node": {k: hit[k] for k in node_selection}}
                            for hit in hits
                        ],
                    }
                }
            },
        )

    def _branch_query(self, args, selection):
        # The Branch query answers with a flat list, not count/edges/node.
        branches = list(self.branches.values())
        if "name" in args:
            branches = [b for b in branches if b["name"] == args["name"]]
        if "ids" in args:
            branches = [b for b in branches if b["id"] in args["ids"]]
        return httpx2.Response(
            200,
            json={
                "data": {"Branch": [{k: b.get(k) for k in selection} for b in branches]}
            },
        )

    def _branch_mutation(self, name, args, selection):
        data = args.get("data") or {}
        branch_name = data.get("name")
        if name == "BranchCreate":
            if branch_name in self.branches:
                return self._errors(f"Branch {branch_name} already exists.")
            branch = make_branch(
                branch_name,
                description=data.get("description"),
                sync_with_git=data.get("sync_with_git", False),
            )
            self.branches[branch_name] = branch
        elif branch_name not in self.branches:
            return self._errors(f"Branch {branch_name} was not found.")
        elif name == "BranchDelete":
            # BranchDelete answers with ok and a task, never an object.
            branch = self.branches.pop(branch_name)
        elif name == "BranchUpdate":
            self.branches[branch_name]["description"] = data.get("description")
            # BranchUpdate answers with ok only.
            return httpx2.Response(200, json={"data": {name: {"ok": True}}})
        elif name in ("BranchRebase", "BranchMerge", "BranchValidate"):
            branch = self.branches[branch_name]
            if name == "BranchMerge":
                branch["status"] = "MERGED"
        else:
            return self._errors(f'Unknown mutation "{name}".')
        payload = {"ok": True}
        if "object" in selection:
            payload["object"] = {
                k: branch.get(k) for k in parse_fields(selection["object"])
            }
        if "task" in selection:
            # wait_until_completion: false answers with the queued task's id.
            task = self.add_task(
                f"{TASK_ID_PREFIX}{len(self.tasks) + 1:02d}",
                states=("PENDING", "RUNNING", "COMPLETED"),
                title=name,
            )
            payload["task"] = {"id": task["id"]}
        return httpx2.Response(200, json={"data": {name: payload}})


@pytest.fixture
def fake():
    return FakeInfrahub()


def make_api(fake, token="abc123", **kwargs):
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handler))
    return aiopyinfrahub.api(BASE, token=token, client=client, **kwargs)


@pytest.fixture
async def ih(fake):
    async with make_api(fake) as ih:
        yield ih
