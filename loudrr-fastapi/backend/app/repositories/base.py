from typing import Generic, TypeVar, Type, Sequence
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound

T = TypeVar("T")


class BaseRepository(Generic[T]):
    """One home for the row-shaped queries every service reuses."""

    model: Type[T]

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, **where) -> T | None:
        # filter_by(**where) turns get(id=5) into  WHERE id = 5
        q = await self.db.execute(select(self.model).filter_by(**where))
        # scalar_one_or_none(): the one row, or None if there isn't one
        return q.scalar_one_or_none()

    async def get_or_404(self, *, label: str = "row", **where) -> T:
        row = await self.get(**where)
        if row is None:
            raise NotFound(f"{label} not found")
        return row

    async def exists(self, **where) -> bool:
        return (await self.get(**where)) is not None

    # for conditions kwargs can't express, e.g. func.lower(col) == "value"
    async def exists_where(self, *expressions) -> bool:
        q = await self.db.execute(
            select(self.model).where(*expressions).limit(1)
        )
        return q.scalar_one_or_none() is not None

    async def list(self, *, limit: int = 100, **where) -> Sequence[T]:
        q = await self.db.execute(
            select(self.model).filter_by(**where).limit(limit)
        )
        return q.scalars().all()

    # THE one place in the app that catches IntegrityError and turns a
    # duplicate (unique-constraint violation) into a clean Conflict
    async def create(self, **values) -> T:
        row = self.model(**values)
        self.db.add(row)
        try:
            # flush() sends the INSERT now, so a duplicate fails HERE
            await self.db.flush()
        except IntegrityError as e:
            await self.db.rollback()
            raise Conflict("Row already exists") from e
        return row
