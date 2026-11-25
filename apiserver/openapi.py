"""OpenAPI 3.0.3 description for the Nemeq v1alpha1 API."""

from __future__ import annotations


from typing import Any

RESOURCES: tuple[str, ...] = ("zis", "sus", "epishus", "sessions", "grants", "quotas", "models")
STATUS_RESOURCES: tuple[str, ...] = ("epishus", "sessions", "quotas")
PREFIX: str = "/apis/nemeq.io/v1alpha1"
PROBLEM_REF: dict[str, Any] = {"$ref": "#/components/responses/Problem"}
ERROR_RESPONSES: dict[str, dict[str, Any]] = {
    code: {"description": "Error", "content": {"application/problem+json": {"schema": PROBLEM_REF}}}
    for code in ("400", "403", "404", "409", "410", "413", "422", "429", "507")
}



SCHEMA_REF_LIST: dict[str, Any] = {"$ref": "#/components/schemas/List"}
SCHEMA_REF_WATCH: dict[str, Any] = {"$ref": "#/components/schemas/WatchPage"}
SCHEMA_REF_ENVELOPE: dict[str, Any] = {"$ref": "#/components/schemas/Envelope"}


def _method_get_collection(kind: str) -> dict[str, Any]:
    return {
        "summary": f"List {kind}", "operationId": f"list{kind}",
        "parameters": [
            {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1}},
            {"name": "continue", "in": "query", "schema": {"type": "string"}},
            {"name": "watch", "in": "query", "schema": {"type": "boolean"}},
            {"name": "fromRev", "in": "query", "schema": {"type": "integer", "minimum": 0}},
            {"name": "from_rev", "in": "query", "schema": {"type": "integer", "minimum": 0},
             "description": "snake_case alias of fromRev"},
        ],
        "responses": {
            "200": {"description": "OK", "content": {
                "application/json": {"schema": {"oneOf": [SCHEMA_REF_LIST, SCHEMA_REF_WATCH]}}
            }},
            **ERROR_RESPONSES,
        },
    }


def _method_create(kind: str) -> dict[str, Any]:
    return {
        "summary": f"Create {kind}", "operationId": f"create{kind}",
        "requestBody": {"content": {"application/json": {"schema": SCHEMA_REF_ENVELOPE}}},
        "responses": {
            "201": {"description": "Created", "content": {
                "application/json": {"schema": SCHEMA_REF_ENVELOPE}
            }},
            **ERROR_RESPONSES,
        },
    }


def _method_item(kind: str, verb: str, operation_id: str, summary: str) -> dict[str, Any]:
    responses: dict[str, Any] = {"200": {"description": "OK", "content": {
        "application/json": {"schema": SCHEMA_REF_ENVELOPE}
    }}} if verb != "delete" else {"200": {"description": "OK"}}
    return {
        "summary": summary, "operationId": operation_id,
        "parameters": [{"name": "name", "in": "path", "required": True,
                        "schema": {"type": "string"}}],
        "responses": {**responses, **ERROR_RESPONSES},
    }



def _components() -> dict[str, Any]:
    return {
        "responses": {
            "Problem": {"description": "RFC 7807 problem",
                        "content": {"application/problem+json": {
                            "schema": {"$ref": "#/components/schemas/Problem"}}}},
        },
        "schemas": {
            "Problem": {"type": "object", "required": ["type", "title", "status", "detail", "reason"],
                        "properties": {k: {"type": "string"} for k in ("type", "title", "status", "detail", "reason")}},
            "ObjectMeta": {"type": "object", "required": ["name", "uid", "resourceVersion", "generation"],
                           "properties": {"name": {"type": "string"}, "uid": {"type": "string"},
                                          "resourceVersion": {"type": "string"}, "generation": {"type": "integer"}}},
            "Envelope": {"type": "object", "required": ["apiVersion", "kind", "metadata", "spec", "status"],
                         "properties": {"apiVersion": {"type": "string"}, "kind": {"type": "string"},
                                        "metadata": {"$ref": "#/components/schemas/ObjectMeta"},
                                        "spec": {"type": "object"}, "status": {"type": "object"}}},
            "List": {"type": "object", "required": ["apiVersion", "kind", "metadata", "items"],
                     "properties": {"apiVersion": {"type": "string"}, "kind": {"type": "string"},
                                    "metadata": {"type": "object", "properties": {"continue": {"type": "string"}}},
                                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Envelope"}}}},
            "WatchPage": {
                "type": "object",
                "required": ["items", "metadata"],
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["type", "rev", "object"],
                            "properties": {
                                "type": {"type": "string",
                                         "enum": ["ADDED", "MODIFIED", "DELETED"]},
                                "rev": {"type": "integer"},
                                "object": {"nullable": True,
                                           "allOf": [{"$ref": "#/components/schemas/Envelope"}]},
                            },
                        },
                    },
                    "metadata": {
                        "type": "object",
                        "required": ["nextRev", "fromRev", "limit"],
                        "properties": {
                            "nextRev": {"type": "integer",
                                        "description": "global rev of the last inspected event"},
                            "fromRev": {"type": "integer"},
                            "limit": {"type": "integer"},
                        },
                    },
                },
            },
        },
    }



def build_openapi() -> dict[str, Any]:
    kind_map = {"zis": "Zi", "sus": "Su", "epishus": "Epishu", "sessions": "Session",
                "grants": "Grant", "quotas": "Quota", "models": "Model"}
    paths: dict[str, dict[str, Any]] = {}
    for plural in RESOURCES:
        kind = kind_map[plural]
        col = f"{PREFIX}/{plural}"
        elem = f"{col}/{{name}}"
        paths[col] = {"get": _method_get_collection(kind), "post": _method_create(kind)}
        paths[elem] = {
            "get": _method_item(kind, "get", f"get{kind}", f"Get {kind}"),
            "put": _method_item(kind, "update", f"update{kind}", f"Update {kind}"),
            "delete": _method_item(kind, "delete", f"delete{kind}", f"Delete {kind}"),
        }
        if plural in STATUS_RESOURCES:
            paths[f"{elem}/status"] = {
                "put": _method_item(kind, "status", f"replaceStatus{kind}",
                                    f"Replace status of {kind}")
            }
    return {"openapi": "3.0.3", "info": {"title": "Nemeq API", "version": "v1alpha1"},
            "paths": paths, "components": _components()}
