"""One error type for everything that stops a query, with a hint the model can act on."""

from __future__ import annotations


class QueryRejected(Exception):
    """A query was refused by the guard or failed in the database.

    `code` is stable and machine-readable (used by tests and evals); `message` says what
    happened; `hint` tells the model how to fix it.
    """

    def __init__(self, code: str, message: str, hint: str | None = None, layer: str = "guard"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.layer = layer  # "guard" (static check) or "database" (runtime)

    def to_dict(self) -> dict:
        out = {"error": self.code, "layer": self.layer, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        return out

    def __repr__(self) -> str:
        return f"QueryRejected({self.code!r}, {self.message!r})"
