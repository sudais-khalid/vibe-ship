import os
from fastapi import FastAPI
app = FastAPI()
DB = os.environ["DATABASE_URL"]
SECRET = os.getenv("JWT_SECRET")
@app.get("/healthz")
def health(): return {"ok": True}
