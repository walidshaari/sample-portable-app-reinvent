from os import getenv
from dotenv import load_dotenv
from infrastructure.repositories.memory import MemoryRepo

PORT = getenv("PORT")
