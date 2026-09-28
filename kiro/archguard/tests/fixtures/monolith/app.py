import boto3
from fastapi import FastAPI, HTTPException

dynamodb = boto3.resource("dynamodb")
app = FastAPI()


@app.post("/widgets")
def create_widget(data: dict):
    if not data.get("name"):
        raise HTTPException(status_code=422, detail="name is required")
    dynamodb.Table("Widgets").put_item(Item=data)
    return data


@app.get("/widgets/{widget_id}")
def get_widget(widget_id: str):
    item = dynamodb.Table("Widgets").get_item(Key={"id": widget_id}).get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return item
