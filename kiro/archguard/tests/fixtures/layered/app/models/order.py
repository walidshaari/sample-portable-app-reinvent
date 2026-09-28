from dataclasses import dataclass


@dataclass
class Order:
    id: str
    total: float
