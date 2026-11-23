from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.api.auth import router as auth_router
from app.api.customer import router as customer_router
from app.api.proposals import router as proposal_router
from app.api.runs import router as run_router
from app.auth.tokens import assert_jwt_secret_configured
from app.config import get_settings
from app.db.session import engine

assert_jwt_secret_configured(get_settings().jwt_secret)

app = FastAPI(title="Support Resolution Agent")
app.include_router(auth_router)
app.include_router(customer_router)
app.include_router(proposal_router)
app.include_router(run_router)


@app.get("/health")
def health() -> dict:
    """Liveness plus a real database round-trip."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}
