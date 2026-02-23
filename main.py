from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import asyncio
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from sources.vk_source import get_vk_info
from sources.tg_source import get_tg_info
from sources.maigret_source import run_maigret
import config

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class SearchRequest(BaseModel):
    query: str
    query_type: str  # username, email, phone
    sources: list[str]  # vk, telegram, maigret
    vk_token: Optional[str] = None
    tg_api_id: Optional[int] = None
    tg_api_hash: Optional[str] = None
    ai_provider: Optional[str] = None
    ai_key: Optional[str] = None

@app.post("/api/search")
async def search(req: SearchRequest):
    results = []
    vk_token = req.vk_token or config.VK_TOKEN
    tg_api_id = req.tg_api_id or config.TG_API_ID
    tg_api_hash = req.tg_api_hash or config.TG_API_HASH

    if "vk" in req.sources:
        vk_result = get_vk_info(vk_token, req.query)
        results.append(vk_result)

    if "telegram" in req.sources:
        tg_result = await get_tg_info(tg_api_id, tg_api_hash, req.query)
        results.append(tg_result)

    if "maigret" in req.sources:
        maigret_result = run_maigret(req.query)
        results.append(maigret_result)

    return {"results": results}

@app.get("/")
async def root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
