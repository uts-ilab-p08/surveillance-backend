from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Unauthenticated liveness check — not in the original diagram but needed
    by any deployment platform / uptime check."""
    return {"status": "ok"}
