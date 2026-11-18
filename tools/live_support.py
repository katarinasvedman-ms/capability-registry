from __future__ import annotations

import asyncio

from azure.identity import AzureCliCredential


class AsyncCliCredential:
    def __init__(self):
        self.credential = AzureCliCredential(process_timeout=120)

    async def get_token(self, scope):
        return await asyncio.to_thread(self.credential.get_token, scope)

    async def close(self):
        await asyncio.to_thread(self.credential.close)
