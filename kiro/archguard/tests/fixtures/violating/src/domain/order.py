import importlib
import os
from infrastructure.repositories.memory import MemoryRepo

client = importlib.import_module("requests")
region = os.environ.get("AWS_REGION")
