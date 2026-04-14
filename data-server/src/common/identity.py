from __future__ import annotations


def identity_fields(*fields):
    """Class decorator: generates __eq__ and __hash__ from the named fields.

    Ensures eq/hash consistency by construction — both methods use the same
    tuple of field values, so they can never diverge.

    Usage:
        @identity_fields("id", "name")
        class MyModel(BaseModel):
            id: str
            name: str
            items: List[Item] = []  # not part of identity
    """

    def decorator(cls):
        def __eq__(self, other):
            if self is other:
                return True
            if not isinstance(other, cls):
                return False
            return tuple(getattr(self, f) for f in fields) == tuple(getattr(other, f) for f in fields)

        def __hash__(self):
            return hash(tuple(getattr(self, f) for f in fields))

        cls.__eq__ = __eq__
        cls.__hash__ = __hash__
        return cls

    return decorator
