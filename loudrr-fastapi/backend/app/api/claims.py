from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse

from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.db.session import get_session
from app.models.user import User
from app.services import claims as svc
from app.tasks.enqueue import enqueue

router = APIRouter(tags=["claims"])


# ---- endpoint 12 ----
@router.post("/session/queue-claim/")
# spawns verification work → cap claim spam per IP
@limiter.limit("30/hour")
async def queue_claim(
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    async def schedule(batch_id):
        # prefer the arq queue (Ch16); fall back to in-process BackgroundTasks
        if not await enqueue("process_verification_batch", str(batch_id)):
            background.add_task(svc.process_batch_in_new_session, batch_id)

    body, status_code = await svc.queue_claim(db, user=user, schedule=schedule)
    return JSONResponse(content=body, status_code=status_code)


# ---- endpoint 13 ----
@router.get("/claims/history/")
async def claim_history(
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    return await svc.claim_history(db, user=user)
