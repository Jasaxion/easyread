"""Shared import completion; keep legacy uploads/URLs and background URLs equivalent."""
from ..library import citations
from ..translate import langs


def finish_import(app, ws, fresh, translate_after, scope=None, read=False, model="", target="", translation_extra="", citation=None, *, resume_processing=False):
    paper = ws.load("paper") or {}
    job = ws.load("job") or {}
    active = job.get("state") in ("queued", "running")
    reserve = resume_processing and active and job.get("type") == "prepare" and not job.get("translate")
    queued = reserve or ((fresh or resume_processing or not paper.get("meta", {}).get("pages")) and not active)
    if queued:
        if translate_after and not read:
            langs.remember(ws, target)
        if paper.get("meta", {}).get("source_type") == "web":
            scope = "all"
        app.jobs.enqueue(ws, translate_after=translate_after, scope=scope, read=read, model=model, translation_extra=translation_extra)
    result = {"id": ws.id, "new": fresh, "queued": queued}
    if citation:
        try:
            citations.link_reference(app.lib, citation[0], citation[1], citation[2], ws.id)
        except (ValueError, OSError) as error:
            result["citation_error"] = str(error)
    return result
