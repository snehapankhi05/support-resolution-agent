"""Tool results. Errors stay structured so the model never sees a traceback."""

from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    success: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    retryable: bool = False
    message: str = ""


def ok(data: dict[str, Any]) -> ToolResult:
    return ToolResult(success=True, data=data)


def fail(code: str, message: str, *, retryable: bool = False) -> ToolResult:
    return ToolResult(success=False, error_code=code, retryable=retryable, message=message)


NOT_FOUND = "not found"
