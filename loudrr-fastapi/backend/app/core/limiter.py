from slowapi import Limiter
from slowapi.util import get_remote_address

# One shared rate limiter, in its own module so both main.py and the route
# files can import it without a circular import (main imports the routers,
# the routers import the limiter — they must not import each other).
limiter = Limiter(key_func=get_remote_address)
