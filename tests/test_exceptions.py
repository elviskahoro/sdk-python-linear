"""Serialization regression tests for the typed exception hierarchy.

``BaseException.__reduce__`` rebuilds an exception for ``pickle``/``copy`` by
calling ``cls(*self.args)`` *before* restoring ``__dict__``, so every override of
``__init__`` must remain callable with only ``message`` — otherwise unpickling
raises ``TypeError`` and a process pool surfaces ``BrokenProcessPool`` instead of
the original error. These tests pin that invariant so the regression cannot
silently return (no SDK call path crosses a process boundary, so the suite
previously never exercised serialization).
"""

from __future__ import annotations

import pickle

import pytest

from gtm_linear.exceptions import (
    LinearAPIError,
    LinearGraphQLError,
    LinearHTTPError,
    LinearResponseError,
)


def _http_error() -> LinearHTTPError:
    """A canonical ``LinearHTTPError`` exercising both extra attributes."""
    return LinearHTTPError("HTTP error: 503", status_code=503, body="boom")


def _assert_http_error_intact(exc: LinearHTTPError) -> None:
    """Assert every documented attribute of a round-tripped ``LinearHTTPError``.

    Also guards against the tempting-but-wrong fix of threading
    ``status_code``/``body`` into ``Exception.args``: that would change
    ``str(exc)`` from the message to a tuple repr and break logging output.
    """
    assert isinstance(exc, LinearHTTPError)  # noqa: S101
    assert isinstance(exc, LinearAPIError)  # noqa: S101
    assert exc.status_code == 503  # noqa: S101
    assert exc.body == "boom"  # noqa: S101
    assert exc.message == "HTTP error: 503"  # noqa: S101
    assert exc.errors == []  # noqa: S101
    assert str(exc) == "HTTP error: 503"  # noqa: S101
    assert exc.args == ("HTTP error: 503",)  # noqa: S101


def test_http_error_pickle_roundtrip_preserves_attributes() -> None:
    """``pickle.loads(pickle.dumps(exc))`` is the canonical reproduction.

    Before the fix this raised ``TypeError: missing required positional
    argument: 'status_code'`` because ``BaseException.__reduce__`` rebuilds via
    ``cls(message)`` before ``__dict__`` (which carries ``status_code``) is
    restored.
    """
    _assert_http_error_intact(pickle.loads(pickle.dumps(_http_error())))


def test_http_error_constructible_with_message_only() -> None:
    """``cls(message)`` is the form ``BaseException.__reduce__`` uses.

    Before the fix ``status_code`` was required-positional and this call raised
    ``TypeError: missing required positional argument: 'status_code'``.
    """
    exc = LinearHTTPError("something went wrong")
    assert exc.status_code == 0  # noqa: S101
    assert exc.body == ""  # noqa: S101
    assert exc.message == "something went wrong"  # noqa: S101
    assert exc.errors == []  # noqa: S101


# Module-level so it is picklable under spawn start methods (Windows/CI), not
# just fork. ``ProcessPoolExecutor`` pickles the callable to the worker.
def _raise_http_error_in_worker() -> None:
    raise LinearHTTPError("HTTP error: 503", status_code=503, body="boom")


def test_http_error_roundtrips_through_process_pool() -> None:
    """The real-world impact: a worker's ``LinearHTTPError`` must reach the
    parent with ``status_code`` intact.

    Before the fix the executor pickled the exception to re-raise it, the
    unpickle raised ``TypeError``, and the parent saw ``BrokenProcessPool``
    instead of the actual transport error.
    """
    import concurrent.futures

    with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_raise_http_error_in_worker)
        with pytest.raises(LinearHTTPError) as caught:
            future.result(timeout=30)

    _assert_http_error_intact(caught.value)


def test_all_api_error_subclasses_constructible_with_message_only() -> None:
    """Every ``LinearAPIError`` subclass's ``__init__`` must accept ``message``
    alone — the exact contract ``BaseException.__reduce__`` relies on, so a
    future subclass cannot re-introduce the same defect.
    """
    for cls in (
        LinearAPIError,
        LinearGraphQLError,
        LinearHTTPError,
        LinearResponseError,
    ):
        instance = cls("only a message")  # type: ignore[call-arg]
        assert isinstance(instance, cls)  # noqa: S101
        assert isinstance(instance, LinearAPIError)  # noqa: S101
        assert instance.message == "only a message"  # noqa: S101
        # Reconstructing through the default ``__reduce__`` tuple must not raise.
        rebuilt = type(instance)(*instance.args)
        assert isinstance(rebuilt, cls)  # noqa: S101
