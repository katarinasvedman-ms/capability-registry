from __future__ import annotations

import base64
import re
from urllib.parse import urlsplit


class KeyVaultSigner:
    """Remote signing only: the managed identity never downloads private key material."""

    def __init__(self, key_id, credential, client):
        url = urlsplit(key_id)
        if (
            url.scheme != "https"
            or not re.fullmatch(r"[a-z0-9-]+\.vault\.azure\.net", url.hostname or "")
            or not re.fullmatch(r"/keys/[a-zA-Z0-9-]+/[a-f0-9]{32}", url.path)
            or url.query
            or url.fragment
            or url.username
            or url.password
            or url.port
        ):
            raise ValueError("Expected an immutable Azure Key Vault key-version URL")
        self.key_id, self.credential, self.client = key_id, credential, client

    async def sign(self, digest):
        if len(digest) != 32:
            raise ValueError("RS256 requires a SHA-256 digest")
        token = await self.credential.get_token("https://vault.azure.net/.default")
        response = await self.client.post(
            self.key_id + "/sign?api-version=7.5",
            headers={"Authorization": f"Bearer {token.token}"},
            json={"alg": "RS256", "value": base64.urlsafe_b64encode(digest).rstrip(b"=").decode()},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("kid") != self.key_id:
            raise ValueError("Key Vault returned a different key version")
        return base64.urlsafe_b64decode(data["value"] + "=" * (-len(data["value"]) % 4))
