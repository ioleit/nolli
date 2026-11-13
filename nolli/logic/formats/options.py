"""Options of the formatters that take some (nolli/logic/formats/formatters.py).

Each model holds flat fields with defaults. A server reads them from a request: its request
type inherits every options model, so all options share one namespace and no two models may
name the same field."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class MermaidFormatOptions(BaseModel):
    """Options of the ``mermaid`` formatter."""

    fade: bool = True  # in a change's scene, draw what did not change faint


class TableFormatOptions(BaseModel):
    """Options of the ``table`` formatter."""

    overlays: list[str] = []  # the columns, in this order
    required_overlay: str | None = None  # only the elements this overlay has a value for; it need not be a column

    @field_validator("overlays", mode="before")
    @classmethod
    def split_names(cls, v: object) -> object:
        """Accept the names comma-separated (``change,label``) as well as one per value."""
        if isinstance(v, str):
            v = [v]
        if isinstance(v, (list, tuple)):
            return [n for s in v for n in str(s).split(",") if n]
        return v
