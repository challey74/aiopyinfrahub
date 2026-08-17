"""Exception types raised by aiopyinfrahub."""

from __future__ import annotations

from typing import Any

import httpx


class RequestError(Exception):
    """Infrahub returned a non-success HTTP response."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.status_code = response.status_code
        self.url = str(response.url)
        self.error = response.text
        if response.status_code == 404:
            self.message = f"The requested url: {response.url} could not be found."
        else:
            try:
                detail = response.json()
            except ValueError:
                detail = "(non-JSON response body)"
            self.message = (
                f"The request failed with code {response.status_code} "
                f"{response.reason_phrase}: {detail}"
            )
        super().__init__(self.message)


class ContentError(Exception):
    """A successful response contained non-JSON content."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.url = str(response.url)
        self.error = (
            "The server returned invalid (non-json) data. Maybe not an Infrahub server?"
        )
        super().__init__(self.error)


class TaskTimeoutError(Exception):
    """A server-side task was still running when its timeout elapsed.

    Attributes:
        task_id: The task that was being polled.
        timeout: The timeout, in seconds, that elapsed.
    """

    def __init__(self, task_id: str, timeout: float) -> None:
        self.task_id = task_id
        self.timeout = timeout
        super().__init__(f"Task {task_id} was still running after {timeout}s.")


class ConvergenceTimeoutError(Exception):
    """The schema was still propagating when its timeout elapsed.

    A schema load applies asynchronously: the API answers as soon as the
    new schema is stored, and every worker then picks it up on its own,
    so InfrahubStatus keeps reporting the hash as unsynced until the last
    one has. This is raised when that had not finished in time.

    Attributes:
        timeout: The timeout, in seconds, that elapsed.
    """

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        super().__init__(f"The schema was still converging after {timeout}s.")


class GraphQLError(Exception):
    """A GraphQL operation failed.

    Infrahub answers execution failures with HTTP 200 and an `errors`
    array rather than an error status, so this is raised from the response
    body as well as from the 400 an unparseable query gets.

    Attributes:
        errors: The `errors` array describing what went wrong.
    """

    def __init__(self, response: httpx.Response, errors: list[Any]) -> None:
        self.response = response
        self.status_code = response.status_code
        self.url = str(response.url)
        self.errors = errors
        super().__init__(str(errors))
