from fastapi import FastAPI

from app.services.order_service import place_order
from app.models.order import Order

app = FastAPI()


@app.post("/orders")
def create(data: dict):
    place_order(Order(**data))
    return data
