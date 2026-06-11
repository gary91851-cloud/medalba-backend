from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import get_settings
from .routers.api import router

app = FastAPI(title="MedAlba API")

s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[s.frontend_url, "https://medalba.com", "https://www.medalba.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "medalba-api"}
