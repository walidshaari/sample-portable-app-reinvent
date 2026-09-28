import json

from application.use_cases.create_note import CreateNoteUseCase


def handle(use_case: CreateNoteUseCase, body: str) -> str:
    return json.dumps({"ok": True, "body": body})
