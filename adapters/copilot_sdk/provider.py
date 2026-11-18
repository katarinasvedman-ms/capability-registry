from __future__ import annotations

import asyncio

from adapters.core import RegistryError


class RegistrySkillClient:
    def __init__(self, transport, binding):
        self.transport, self.binding = transport, binding
        self.versions = None

    async def catalog(self):
        records = await self.transport.list_skills(self.binding)
        self.versions = {name: record["foundry_version"] for name, record in records.items()}
        return records

    async def read(self, uri):
        return await self.transport.read_resource(self.binding, uri)

    def metrics(self):
        calls = self.transport.evidence
        return {
            "calls": calls,
            "bytes": sum(row["bytes"] for row in calls),
            "cache_hits": 0,
            "cache_bytes": 0,
        }

    async def aclose(self):
        await self.transport.aclose()


def integrate(provider_type, registry, *, identity, rationale):
    """Specialize the frozen provider; retain its safe final materialization implementation."""
    from copilot import SkillProviderDescriptor

    class CoreSkillProvider(provider_type):
        def __init__(self, manifest, role, **kwargs):
            if "client_factory" in kwargs:
                raise RegistryError("The integrated provider uses the shared registry transport")
            super().__init__(
                manifest,
                role,
                client_factory=lambda binding: RegistrySkillClient(registry.transport, binding),
                **kwargs,
            )

        async def list_skills(self):
            try:
                async with asyncio.timeout(self.deadline), self.lock:
                    self.raise_if_failed()
                    records = await self.client.catalog()
                    if self.client.versions != self.distribution["skill_versions"]:
                        raise RegistryError("Pinned Toolbox references changed")
                    descriptors = []
                    for name in sorted(self.names):
                        expected = self.manifest["skills"][name]
                        if records.get(name) != {key: expected[key] for key in records[name]}:
                            raise RegistryError("Pinned discovery metadata changed")

                        async def describe(entry, binding, name=name):
                            if entry["description"] != records[name]["description"]:
                                raise RegistryError("Contract and pinned discovery disagree")
                            return SkillProviderDescriptor(
                                name=name,
                                description=entry["description"],
                                directory=str(self.root / name),
                            )

                        descriptors.append(
                            await registry.execute(
                                sorted(self.names), name, identity, rationale, describe
                            )
                        )
                    return descriptors
            except BaseException as error:
                self.fail("list", reason=type(error).__name__)
                raise

        async def read_skill(self, name):
            async def materialize(entry, binding):
                return await super(CoreSkillProvider, self).read_skill(entry["id"])

            try:
                return await registry.execute(
                    sorted(self.names), name, identity, rationale, materialize
                )
            except BaseException as error:
                self.fail("read", name, type(error).__name__)
                raise

    return CoreSkillProvider
