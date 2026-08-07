from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager

async def exists(db: AsyncSession, model, where) -> bool:
    """Does any row in 'model' match 'where'? returns true/false."""
    # LIMIT 1 - we only need to know IF one exists, not how many.
    q = await db.execute(select(model).where(where).limit(1))
    return q.scalar_one_or_none() is not None

async def get_or_404(db: AsyncSession, model, where, *, label: str = "row"):
    """fetch one row or raise NotFound. saves the repeating two-line check."""
    # import inside the function to avoid a circular import (errors.py
    # and db_helpers.py would otherwise import each other)
    from app.core.errors import NotFound
    q = await db.execute(select(model).where(where))
    row = q.scalar_one_or_none()
    if row is None:
        raise NotFound(f"{label} not found")
    return row

@asynccontextmanager
async def locked_row(db, model, **where):
    """Lock a single row FOR UPDATE for the duration of the with-block."""
    # with_for_update() adds SQL's "FOR UPDATE" — it locks the row so no
    # other request can change it until this transaction finishes
    q = await db.execute(
        select(model).filter_by(**where)
        .with_for_update()
        # refresh the identity-map copy from the locked row (see credits.py)
        .execution_options(populate_existing=True)
    )
    row = q.scalar_one_or_none()
    if row is None:
        from app.core.errors import NotFound
        raise NotFound(f"{model.__name__} not found")
    yield row
