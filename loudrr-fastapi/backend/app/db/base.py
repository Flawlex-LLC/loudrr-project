from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# run this and verify tables: python -c "import app.models; from app.db.base import Base; print(list(Base.metadata.tables))"
