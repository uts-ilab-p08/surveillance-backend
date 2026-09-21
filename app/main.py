from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import cameras, clips, health, queries, search
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Surveillance Video Query API",
    description="Natural-language querying over multi-camera CCTV footage.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# /health is deliberately unauthenticated and unprefixed so uptime checks
# and load balancers can hit it without credentials or versioning.
app.include_router(health.router)

api_router_prefix = settings.api_v1_prefix
app.include_router(search.router, prefix=api_router_prefix)
app.include_router(clips.router, prefix=api_router_prefix)
app.include_router(cameras.router, prefix=api_router_prefix)
app.include_router(queries.router, prefix=api_router_prefix)
