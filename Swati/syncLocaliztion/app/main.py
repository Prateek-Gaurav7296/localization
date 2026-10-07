import logging

from fastapi import FastAPI

from app.routes.sync import router as sync_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs every request URL at INFO, which includes the signed output URL.
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="Sync.so lipsync service")
app.include_router(sync_router)
