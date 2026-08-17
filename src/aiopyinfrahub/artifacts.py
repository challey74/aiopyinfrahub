"""Artifacts: generated files Infrahub keeps against its nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiopyinfrahub.graphql import segment

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api


class Artifacts:
    """ih.artifacts: read generated artifacts and ask for new ones.

    An artifact's content is whatever its transform produced (a config
    file, a rendered template, a JSON document), so fetch() hands back
    bytes and leaves the decoding to the caller. The artifact *nodes*
    themselves are an ordinary kind: `ih.CoreArtifact` reads their
    metadata, and this manager is only about the content.
    """

    def __init__(self, api: Api) -> None:
        self.api = api

    async def fetch(self, artifact_id: str) -> bytes:
        """One artifact's content, by the artifact node's id."""
        resp = await self.api._request_response(
            "GET", f"{self.api.base_url}/api/artifact/{segment(artifact_id)}"
        )
        return resp.content

    async def generate(
        self,
        definition_id: str,
        *,
        nodes: list[str] | None = None,
        branch: str | None = None,
    ) -> None:
        """Queue generation of an artifact definition's artifacts.

        Args:
            definition_id: The CoreArtifactDefinition's id, not an
                artifact id: generation is defined per definition and
                fans out over its targets.
            nodes: Target node ids to regenerate for. None or empty
                regenerates every target the definition selects.
            branch: Branch to generate on, overriding the client default.

        Returns:
            Nothing. The route queues the work and answers with no body
            worth parsing; watch `ih.tasks` for how it went.
        """
        params: dict[str, Any] = {}
        resolved = branch if branch is not None else self.api.branch
        if resolved:
            params["branch"] = resolved
        await self.api._request_response(
            "POST",
            f"{self.api.base_url}/api/artifact/generate/{segment(definition_id)}",
            params=params,
            json={"nodes": nodes or []},
            idempotent=False,
        )
