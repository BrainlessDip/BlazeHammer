"""File attachments for multipart uploads (backed by blaze_hammer.ext.attachments)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol


class _HasRead(Protocol):
    def read(self, n: int = ...) -> bytes: ...


@dataclass
class OpenedAttachment:
    field_name: str
    handle: IO[bytes]
    _owns: bool

    def close(self) -> None:
        if self._owns:
            self.handle.close()


class AttachmentSet:
    """Validated, opened attachments for one run.

    Paths are validated at construction (fail early) but opened here so
    the run owns the handles and closes them deterministically — no more
    module-level open files leaking between invocations.
    """

    def __init__(self, spec: dict[str, object]) -> None:
        missing = [
            name
            for name, value in spec.items()
            if isinstance(value, (str, Path)) and not Path(str(value)).is_file()
        ]
        if missing:
            from blaze_hammer.errors import ConfigurationError

            raise ConfigurationError(
                "File payload references missing file(s)",
                reason=", ".join(sorted(missing)),
                hint="fix paths in blaze_hammer/ext/attachments.py",
            )
        self._spec = spec
        self._opened: list[OpenedAttachment] | None = None

    @property
    def empty(self) -> bool:
        return not self._spec

    def open(self) -> None:
        from blaze_hammer.errors import ConfigurationError

        if self._opened is not None:
            return
        opened: list[OpenedAttachment] = []
        for name, value in self._spec.items():
            if isinstance(value, (str, Path)):
                opened.append(
                    OpenedAttachment(
                        str(name),
                        open(value, "rb"),  # noqa: SIM115 - closed via close()
                        True,
                    )
                )
            elif hasattr(value, "read"):
                opened.append(OpenedAttachment(str(name), value, False))  # type: ignore[arg-type]
            else:
                raise ConfigurationError(
                    f"Attachment '{name}' must be a path or an open binary file",
                    found=type(value).__name__,
                )
        self._opened = opened

    def as_httpx_files(self) -> dict[str, IO[bytes]] | None:
        if not self._opened:
            return None
        return {item.field_name: item.handle for item in self._opened}

    def close(self) -> None:
        for item in self._opened or []:
            item.close()
        self._opened = None

    def __enter__(self) -> AttachmentSet:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def load_attachment_spec() -> dict[str, object]:
    """Read the user-editable attachment mapping (validated by caller)."""
    from blaze_hammer.ext import attachments as user_attachments

    return dict(user_attachments.attachments)
