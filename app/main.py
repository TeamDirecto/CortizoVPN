from __future__ import annotations

from fastapi import FastAPI

from app.config import load_config

app = FastAPI(
    title="CortizoVPN",
    version="0.1.0",
    description="Administración controlada de usuarios, grupos y extensiones VICIdial.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/nodes")
def nodes() -> dict:
    config = load_config()
    return {
        "dialers": config.raw.get("dialers", {}),
        "database": config.raw.get("database", {}),
    }
