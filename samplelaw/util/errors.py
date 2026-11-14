"""Shared exception types and validation issues for samplelaw."""

from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class ValidationIssue:
    """One stable, machine-readable validation error."""
    code: str
    path: str
    message: str
    line: int | None = None
    def with_line(self, line: int | None) -> "ValidationIssue":
        return ValidationIssue(self.code, self.path, self.message, line)

class SamplelawError(Exception):
    """Base error carrying a stable code and an optional location."""
    code = "SAMPLELAW-000"
    def __init__(self, code: str, message: str, *, path: str = "", line: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.line = line
    def __str__(self) -> str:
        location = self.path or "<root>"
        suffix = f" (line {self.line})" if self.line else ""
        return f"[{self.code}] {location}: {self.message}{suffix}"
    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "line": self.line,
        }

class ConfigError(SamplelawError):
    """Plan or pipeline configuration could not be loaded."""
    def __init__(self, code: str, message: str, *, path: str = "", line: int | None = None):
        super().__init__(code, message, path=path, line=line)

class DataError(SamplelawError):
    """A local data source is unreadable or structurally invalid."""

class SamplingError(SamplelawError):
    """A plan cannot be executed against a data source."""

class LedgerError(SamplelawError):
    """The local SQLite ledger could not be used."""

class ReportError(SamplelawError):
    """A report cannot be produced."""
