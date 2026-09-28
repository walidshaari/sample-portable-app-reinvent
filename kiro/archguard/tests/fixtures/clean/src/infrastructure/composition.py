import os

from infrastructure.repositories.in_memory_note_repository import InMemoryNoteRepository

REGISTRY = {"memory": InMemoryNoteRepository}


def build_repository():
    backend = os.environ.get("NOTE_BACKEND", "memory")
    return REGISTRY[backend]()


def build():
    return InMemoryNoteRepository()
