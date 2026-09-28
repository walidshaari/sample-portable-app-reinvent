from application.ports.note_repository import NoteRepository


class InMemoryNoteRepository(NoteRepository):
    def __init__(self) -> None:
        self._items = {}

    async def create(self, note) -> None:
        self._items[note.id] = note

    async def find_by_id(self, id):
        return self._items.get(id)
