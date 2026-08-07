# parent of all our business errors - python exceptions. 
class DomainError(Exception):
    """base for application-level errors - subclasses set status-code. 
    a service raise one of these; global handler translates to HTTP."""
    status_code = 400

# each subclass changes only the status_code; the name says what went wrong
class NotFound(DomainError):
    status_code = 404

# exists / clashes
class Conflict(DomainError):
    status_code = 409 

# unauthorized - unknown 
class Unauthorized(DomainError):
    status_code = 401

# authentication error
class Forbidden(DomainError):
    status_code = 403

# against the /schemas.
class BadRequest(DomainError):
    status_code = 400

# an external service or queue we depend on is unavailable
class ServiceUnavailable(DomainError):
    status_code = 503

