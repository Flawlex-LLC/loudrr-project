from decimal import Decimal
from typing import Annotated
from pydantic import Field

NonEmptyStr = Annotated[str, Field(min_length=1, max_length=500)]
ShortText = Annotated[str, Field(min_length=1, max_length=100)]
OptCode = Annotated[str | None, Field(max_length=16)]     
OptShort = Annotated[str | None, Field(max_length=50)]
Decimal4 = Annotated[Decimal, Field(max_digits=12, decimal_places=4)]