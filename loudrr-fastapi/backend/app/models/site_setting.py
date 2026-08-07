import uuid
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class SiteSetting(Base):
    __tablename__ = "site_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(255))
    data_type: Mapped[str] = mapped_column(
        String(20), default="int"
    )  # this might be confusing but it's just a hint for the app to know how to interpret the value; the database will store it as a string regardless
    description: Mapped[str] = mapped_column(String(255), default="")
