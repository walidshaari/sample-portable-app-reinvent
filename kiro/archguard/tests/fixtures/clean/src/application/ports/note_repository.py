from abc import ABC, abstractmethod
from typing import Optional

from domain.note import Note


class NoteRepository(ABC):
    @abstractmethod
    async def create(self, note: Note) -> None: ...

    @abstractmethod
    async def find_by_id(self, id: str) -> Optional[Note]: ...
