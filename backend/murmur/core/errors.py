"""Transport-independent failures that are safe to expose to API clients."""


class MurmurError(Exception):
    """Base class for expected application failures."""


class ResourceNotFoundError(MurmurError):
    """The requested application resource does not exist."""


class PermissionDeniedError(MurmurError):
    """The authenticated identity does not own the requested resource."""


class InvalidRequestError(MurmurError):
    """The request conflicts with trusted application state."""


class ServiceInitializationError(MurmurError):
    """A runtime service could not be initialized safely."""
