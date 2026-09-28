from dataclasses import dataclass


@dataclass
class Note:
    id: str
    title: str

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("title is required")
