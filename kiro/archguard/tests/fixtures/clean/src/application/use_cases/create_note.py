import uuid

from ..ports.note_repository import NoteRepository
from domain.note import Note


class CreateNoteUseCase:
    def __init__(self, repository: NoteRepository) -> None:
        self._repository = repository

    async def execute(self, title: str) -> Note:
        note = Note(id=str(uuid.uuid4()), title=title)
        await self._repository.create(note)
        return note
