from decimal import Decimal
from typing import Annotated
from pydantic import Field

NonEmptyStr = Annotated[str, Field(min_length=1, max_length=500)]
ShortText = Annotated[str, Field(min_length=1, max_length=100)]
OptCode = Annotated[str | None, Field(max_length=16)]
OptShort = Annotated[str | None, Field(max_length=50)]
Decimal4 = Annotated[Decimal, Field(max_digits=12, decimal_places=4)]

# itsdangerous URLSafeTimedSerializer token for OAuth-verified handle proofs
# (see services/waitlist_x_oauth.py). A real proof lands around 150-250 chars
# today; the wider ceiling gives headroom for adding fields to the payload
# without shipping a mystifying "String should have at most 500 characters"
# validation error before verify_x_proof even runs.
OAuthProof = Annotated[str, Field(min_length=32, max_length=2048)]