"""OpenAPI contract for the versioned facade, served without exposing UI routes."""

S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
OBJ = {"type": "object", "additionalProperties": True}


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required)}


def response(name, description="Success"):
    return {"description": description, "content": {"application/json": {"schema": ref(name)}}}


def parameter(name, schema=S, where="query", description=""):
    return {"name": name, "in": where, "required": where == "path", "schema": schema, "description": description}


def document(base_url="", auth_required=True):
    paging = [parameter("limit", {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}),
              parameter("offset", {"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0})]
    pid = parameter("id", where="path", description="Stable EasyRead document ID")
    paths = {}
    def get(path, operation, schema, params=(), description=""):
        paths[path] = {"get": {"operationId": operation, "description": description,
                              "parameters": list(params), "responses": {"200": response(schema),
                              "default": response("Error", "Error; see error.code and HTTP status")}}}
    get("/info", "getInfo", "Info")
    get("/documents", "listDocuments", "DocumentPage", [parameter("q"), parameter("category", description="Full path; includes descendants"), parameter("status"), *paging])
    get("/documents/{id}", "getDocument", "Document", [pid])
    get("/documents/{id}/notes", "getNotes", "NotePage", [pid, *paging], "Paper-wide note plus paginated paragraph notes/questions. Deleted notes excluded.")
    get("/documents/{id}/highlights", "getHighlights", "HighlightPage", [pid, *paging])
    get("/documents/{id}/content", "getContent", "ContentPage", [pid, *paging,
        parameter("language", {"type": "string", "enum": ["original", "translation", "both"], "default": "both"}),
        parameter("view", {"type": "string", "enum": ["effective", "raw"], "default": "effective"})],
        "Ordered blocks, retaining EasyRead's block structure. en/zh mean original/target, not necessarily English/Chinese. effective applies saved user edits; raw returns translator output. Shared math/table structure is retained in either language.")
    get("/documents/{id}/links", "getDocumentLinks", "Links", [pid, parameter("block")])
    get("/categories", "listCategories", "CategoryPage", paging, "Ordered tree flattened into full paths/parent links, including empty categories.")
    get("/tasks/{task_id}", "getTask", "Task", [parameter("task_id", where="path")],
        "state=done means the import completed, not translation. processing reports the document's CURRENT preparation/translation job, including confirm/partial/error/cancelled. Later UI jobs may replace processing. Import records survive restart; interrupted downloads are not automatically repeated.")
    get("/openapi.json", "getOpenAPI", "OpenAPI")
    import_response = {"202": response("ImportAccepted", "Accepted for background import"),
                       "default": response("Error", "Invalid/unsafe input, unauthorized, unavailable, oversized or queue full")}
    paths["/imports/file"] = {"post": {"operationId": "importFile", "description": "Raw PDF upload, maximum 50 MiB. No local server paths. Duplicate documents retain existing data. Translation defaults to false.",
        "parameters": [parameter("filename", {"type": "string", "default": "paper.pdf"}), parameter("translate", {"type": "boolean", "default": False}), parameter("target")],
        "requestBody": {"required": True, "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}, "responses": import_response}}
    paths["/imports/url"] = {"post": {"operationId": "importURL", "description": "Public HTTP(S) PDF/article/arXiv URL, ports 80/443 only. DNS and redirects are checked; private addresses, credentials and JavaScript rendering are forbidden. No inherited proxy. translate=true may invoke configured paid models.",
        "requestBody": {"required": True, "content": {"application/json": {"schema": ref("URLImport")}}}, "responses": import_response}}
    paths["/imports/html"] = {"post": {"operationId": "importHTML", "description": "Private HTML snapshot, max 8 MiB UTF-8 HTML / 12 MiB JSON body. No network or JavaScript. Remote assets, styles and active content are removed; embedded PNG/JPEG/GIF/WebP accepted. html + title + source_id identify a snapshot; changed content creates a new document. translate defaults to false.",
        "requestBody": {"required": True, "content": {"application/json": {"schema": ref("HTMLImport")}}}, "responses": import_response}}
    schemas = {
        "Error": obj({"error": obj({"code": S, "message": S}, ("code", "message"))}, ("error",)),
        "Links": obj({"web_url": S, "desktop_url": S}, ("web_url", "desktop_url")),
        "Info": obj({"api_version": S, "version": S, "authentication_required": B, "enabled": B,
                     "mcp_enabled": B, "public_url": S, "capabilities": {"type": "array", "items": S},
                     "max_upload_bytes": I, "max_html_bytes": I, "max_page_size": I}),
        "Document": obj({"id": S, "display_title": S, "title_en": S, "title_zh": S, "authors": S,
                         "tags": {"type": "array", "items": S}, "status": S, "source_type": S, "url": S,
                         "doi": S, "arxiv": S, "target": S, "progress": {"type": "number"}, "job": {"type": ["object", "null"]}, "links": ref("Links")}, ("id",)),
        "Note": obj({"id": S, "anchor": S, "quote": S, "kind": S, "color": S, "body": S,
                     "created": S, "updated": S, "links": ref("Links")}, ("id",)),
        "Block": obj({"id": S, "type": S, "page": I, "en": S, "zh": S, "caption_en": S, "caption_zh": S,
                      "items": {"type": "array", "items": OBJ}, "has_user_edits": B, "links": ref("Links")}, ("id",)),
        "Category": obj({"path": S, "name": S, "parent": {"type": ["string", "null"]}, "count": I}),
        "ImportAccepted": obj({"task_id": S, "state": {"const": "queued"}, "status_url": S}, ("task_id", "state", "status_url")),
        "Task": obj({"id": S, "state": {"type": "string", "enum": ["queued", "running", "done", "error", "interrupted"]},
                     "created": S, "updated": S, "document_id": {"type": ["string", "null"]}, "new": B, "queued": B,
                     "error": {"type": ["object", "null"]}, "processing": {"anyOf": [{"type": "null"}, obj({"type": S, "state": S, "done": I, "total": I, "error": S, "message": S, "updated": S})]}, "links": ref("Links")}),
        "URLImport": {**obj({"url": {"type": "string", "format": "uri", "maxLength": 4096},
                     "translate": {"type": "boolean", "default": False}, "target": S,
                     "kind": {"type": "string", "enum": ["auto", "pdf", "web"], "default": "auto"}}, ("url",)), "additionalProperties": False},
        "HTMLImport": {**obj({"html": {"type": "string", "minLength": 1},
                     "title": {"type": "string", "maxLength": 500}, "source_id": {"type": "string", "maxLength": 512},
                     "translate": {"type": "boolean", "default": False}, "target": S}, ("html",)), "additionalProperties": False},
        "OpenAPI": OBJ,
    }
    for name, item in (("DocumentPage", "Document"), ("NotePage", "Note"), ("HighlightPage", "Note"), ("ContentPage", "Block"), ("CategoryPage", "Category")):
        schemas[name] = obj({"items": {"type": "array", "items": ref(item)}, "total": I, "limit": I, "offset": I,
                             "next_offset": {"type": ["integer", "null"]}}, ("items", "total", "limit", "offset", "next_offset"))
    schemas["NotePage"]["properties"].update(paper_note=OBJ, document_id=S, version=S)
    schemas["HighlightPage"]["properties"].update(document_id=S, version=S)
    schemas["ContentPage"]["properties"].update(document_id=S, language=S, view=S, target=S, translation=OBJ, versions=OBJ)
    return {"openapi": "3.1.0", "info": {"title": "EasyRead Open API", "version": "1.0.0",
            "description": "Bearer uses the existing EasyRead access code. Protection enabled: all endpoints require it. Disabled: no authentication, including remote clients. Browser Cookie/X-Token flow is unchanged. No OAuth discovery or account system."},
            "servers": [{"url": base_url + "/api/v1"}], "paths": paths,
            "security": [{"AccessCode": []}] if auth_required else [],
            "components": {"securitySchemes": {"AccessCode": {"type": "http", "scheme": "bearer", "description": "Existing EasyRead access code (not an OAuth token)"}}, "schemas": schemas}}
