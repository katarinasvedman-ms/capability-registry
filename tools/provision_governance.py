from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import uuid
from pathlib import Path

from tools.capture_sources import ROOT, write_json

DIRECTORY = ROOT / ".artifacts" / "governance"
STATE = DIRECTORY / "deployment.json"
RG = "factory-skills"
PREFIX = "cr-gov-f3e5ad02"
SHORT = "crgovf3e5ad02"
TAGS = ["purpose=capability-registry-testbed", "owner=katarina.svedman", "run=f3e5ad02"]
SUB = "8aebca5c-b21c-4fca-bbc9-dd77b898e99a"
ARM = f"/subscriptions/{SUB}/resourceGroups/{RG}"
AZ = shutil.which("az")
CLI_PYTHON = Path(AZ).parent.parent / "python.exe" if AZ else None
AZ_COMMAND = (
    [str(CLI_PYTHON), "-IBm", "azure.cli"]
    if AZ and AZ.lower().endswith(".cmd") and CLI_PYTHON.is_file()
    else [AZ]
)


def az(*args):
    result = subprocess.run(
        [*AZ_COMMAND, *args, "--output", "json", "--only-show-errors"],
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode:
        raise RuntimeError(f"Azure operation {args[:3]} failed: {result.stderr[:2000]}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


class Deployment:
    def __init__(self):
        self.state = (
            json.loads(STATE.read_text())
            if STATE.exists()
            else {
                "run": "f3e5ad02",
                "created": [],
                "intents": [],
                "resources": {},
            }
        )

    def save(self):
        write_json(STATE, self.state)

    def resource(self, key, kind, expected, command):
        if key in self.state["resources"]:
            return self.state["resources"][key]
        # Never adopt an unrelated resource with a guessed deterministic name.
        exists = subprocess.run(
            [
                *AZ_COMMAND,
                "resource",
                "show",
                "--ids",
                expected,
                "-o",
                "none",
                "--only-show-errors",
            ],
            capture_output=True,
            text=True,
        )
        if exists.returncode == 0:
            raise RuntimeError(f"Unmanifested resource already exists: {key}")
        self.state["intents"].append({"kind": kind, "id": expected})
        self.save()
        value = az(*command)
        if value.get("id", "").casefold() != expected.casefold():
            raise RuntimeError(f"Unexpected returned resource ID: {key}")
        self.state["resources"][key] = value
        self.state["created"].append({"kind": kind, "id": expected})
        self.save()
        return value

    def grant(self, principal, role, scope, principal_type="ServicePrincipal"):
        name = str(uuid.uuid5(uuid.NAMESPACE_URL, f"crgov:{principal}:{role}:{scope}"))
        key = "assignment:" + name
        if key in self.state["resources"]:
            return
        self.state["intents"].append(
            {
                "kind": "roleAssignment",
                "id": scope + "/providers/Microsoft.Authorization/roleAssignments/" + name,
            }
        )
        self.save()
        value = az(
            "role",
            "assignment",
            "create",
            "--name",
            name,
            "--assignee-object-id",
            principal,
            "--assignee-principal-type",
            principal_type,
            "--role",
            role,
            "--scope",
            scope,
            "--description",
            ";".join(TAGS),
        )
        self.state["resources"][key] = value
        self.state["created"].append(
            {"kind": "roleAssignment", "id": value["id"], "principalId": principal}
        )
        self.save()

    def base(self):
        location = "swedencentral"
        for service in ("consumer", "gateway", "mock"):
            self.resource(
                service + "_identity",
                "identity",
                f"{ARM}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{PREFIX}-{service}",
                [
                    "identity",
                    "create",
                    "-g",
                    RG,
                    "-n",
                    PREFIX + "-" + service,
                    "-l",
                    location,
                    "--tags",
                    *TAGS,
                ],
            )
        workspace = self.resource(
            "workspace",
            "workspace",
            f"{ARM}/providers/Microsoft.OperationalInsights/workspaces/{PREFIX}-logs",
            [
                "monitor",
                "log-analytics",
                "workspace",
                "create",
                "-g",
                RG,
                "-n",
                PREFIX + "-logs",
                "-l",
                location,
                "--retention-time",
                "30",
                "--tags",
                *TAGS,
            ],
        )
        self.resource(
            "appinsights",
            "appinsights",
            f"{ARM}/providers/Microsoft.Insights/components/{PREFIX}-insights",
            [
                "resource",
                "create",
                "--id",
                f"{ARM}/providers/Microsoft.Insights/components/{PREFIX}-insights",
                "--api-version",
                "2020-02-02",
                "--is-full-object",
                "--properties",
                json.dumps(
                    {
                        "location": location,
                        "kind": "web",
                        "tags": dict(value.split("=", 1) for value in TAGS),
                        "properties": {
                            "Application_Type": "web",
                            "WorkspaceResourceId": workspace["id"],
                            "IngestionMode": "LogAnalytics",
                        },
                    }
                ),
            ],
        )
        self.resource(
            "registry",
            "registry",
            f"{ARM}/providers/Microsoft.ContainerRegistry/registries/{SHORT}",
            [
                "acr",
                "create",
                "-g",
                RG,
                "-n",
                SHORT,
                "-l",
                location,
                "--sku",
                "Basic",
                "--admin-enabled",
                "false",
                "--tags",
                *TAGS,
            ],
        )
        storage = self.resource(
            "storage",
            "storage",
            f"{ARM}/providers/Microsoft.Storage/storageAccounts/{SHORT}",
            [
                "storage",
                "account",
                "create",
                "-g",
                RG,
                "-n",
                SHORT,
                "-l",
                location,
                "--sku",
                "Standard_LRS",
                "--kind",
                "StorageV2",
                "--https-only",
                "true",
                "--min-tls-version",
                "TLS1_2",
                "--allow-blob-public-access",
                "false",
                "--allow-shared-key-access",
                "false",
                "--tags",
                *TAGS,
            ],
        )
        self.resource(
            "vault",
            "vault",
            f"{ARM}/providers/Microsoft.KeyVault/vaults/{SHORT}",
            [
                "keyvault",
                "create",
                "-g",
                RG,
                "-n",
                SHORT,
                "-l",
                location,
                "--enable-rbac-authorization",
                "true",
                "--retention-days",
                "7",
                "--tags",
                *TAGS,
            ],
        )
        operator = az("ad", "signed-in-user", "show")["id"]
        self.grant(
            operator, "Key Vault Crypto Officer", self.state["resources"]["vault"]["id"], "User"
        )
        self.grant(operator, "Storage Table Data Contributor", storage["id"], "User")
        workspace_key = az(
            "monitor",
            "log-analytics",
            "workspace",
            "get-shared-keys",
            "-g",
            RG,
            "-n",
            PREFIX + "-logs",
        )["primarySharedKey"]
        # Workspace key is passed directly to Azure, never persisted in the manifest.
        self.resource(
            "environment",
            "environment",
            f"{ARM}/providers/Microsoft.App/managedEnvironments/{PREFIX}-env",
            [
                "containerapp",
                "env",
                "create",
                "-g",
                RG,
                "-n",
                PREFIX + "-env",
                "-l",
                location,
                "--logs-workspace-id",
                workspace["customerId"],
                "--logs-workspace-key",
                workspace_key,
                "--tags",
                *TAGS,
            ],
        )
        for service in ("consumer", "gateway", "mock"):
            identity = self.state["resources"][service + "_identity"]
            self.grant(
                identity["principalId"], "AcrPull", self.state["resources"]["registry"]["id"]
            )
            if service in ("gateway", "mock"):
                self.grant(
                    identity["principalId"],
                    "Key Vault Reader",
                    self.state["resources"]["vault"]["id"],
                )
                self.grant(identity["principalId"], "Storage Table Data Contributor", storage["id"])
        print("Owned base resources and new-only assignments recorded")

    def key(self):
        if "key_id" not in self.state:
            value = az(
                "keyvault",
                "key",
                "create",
                "--vault-name",
                SHORT,
                "-n",
                "capability-lease-v1",
                "--kty",
                "RSA",
                "--size",
                "2048",
                "--ops",
                "sign",
                "verify",
                "--tags",
                *TAGS,
            )
            self.state["key_id"] = value["key"]["kid"]
            self.save()
        scope = self.state["resources"]["vault"]["id"] + "/keys/capability-lease-v1"
        self.grant(
            self.state["resources"]["consumer_identity"]["principalId"],
            "Key Vault Crypto User",
            scope,
        )
        az(
            "storage",
            "table",
            "create",
            "--account-name",
            SHORT,
            "--name",
            "LeaseUse",
            "--auth-mode",
            "login",
        )
        print("Managed-identity signing key and atomic lease-use table ready")

    def config(self):
        entries = json.loads((ROOT / "catalog" / "catalog.json").read_text())
        permissions = sorted({p for row in entries for p in (row["permissions"] or [])})
        resumed = json.loads((ROOT / ".artifacts" / "h4" / "resumed.json").read_text())
        callers = {
            resumed["release"]["principal_id"]: {
                "permissions": permissions,
                "risk_tiers": ["general", "privileged"],
            },
            resumed["coder"]["principal_id"]: {
                "permissions": permissions,
                "risk_tiers": ["general"],
            },
        }
        domain = self.state["resources"]["environment"]["properties"]["defaultDomain"]
        return {
            "tenant": "199ab2fd-dc94-4a42-8438-1144eab8c1b4",
            "gateway_oid": self.state["resources"]["gateway_identity"]["principalId"],
            "key_id": self.state["key_id"],
            "table_url": f"https://{SHORT}.table.core.windows.net/LeaseUse",
            "gateway_url": f"https://{PREFIX}-gateway.{domain}",
            "mock_url": f"https://{PREFIX}-mock.{domain}",
            "consumer_url": f"https://{PREFIX}-consumer.{domain}",
            "insights_connection": (
                self.state["resources"]["appinsights"].get("connectionString")
                or self.state["resources"]["appinsights"]["properties"]["ConnectionString"]
            ),
            "callers": callers,
            "benchmark_principal": resumed["release"]["principal_id"],
        }

    def apps(self):
        config = self.config()
        tag = self.state["image_tag"]
        digest = self.state.get("images", {}).get(tag, {}).get("digest")
        image = f"{SHORT}.azurecr.io/governance" + ("@" + digest if digest else ":" + tag)
        for service in ("mock", "gateway", "consumer"):
            identity = self.state["resources"][service + "_identity"]
            env = [
                "SERVICE=" + service,
                "AZURE_CLIENT_ID=" + identity["clientId"],
                "GOV_CONFIG=" + json.dumps(config, separators=(",", ":")),
            ]
            key = service + "_app"
            if key in self.state["resources"]:
                value = az(
                    "containerapp",
                    "update",
                    "-g",
                    RG,
                    "-n",
                    PREFIX + "-" + service,
                    "--image",
                    image,
                    "--set-env-vars",
                    *env,
                )
                self.state["resources"][key] = value
                self.save()
            else:
                self.resource(
                    key,
                    "containerapp",
                    f"{ARM}/providers/Microsoft.App/containerApps/{PREFIX}-{service}",
                    [
                        "containerapp",
                        "create",
                        "-g",
                        RG,
                        "-n",
                        PREFIX + "-" + service,
                        "--environment",
                        PREFIX + "-env",
                        "--image",
                        image,
                        "--registry-server",
                        SHORT + ".azurecr.io",
                        "--registry-identity",
                        identity["id"],
                        "--user-assigned",
                        identity["id"],
                        "--ingress",
                        "external",
                        "--target-port",
                        "8088",
                        "--transport",
                        "http",
                        "--min-replicas",
                        "1",
                        "--max-replicas",
                        "1",
                        "--cpu",
                        "0.5",
                        "--memory",
                        "1Gi",
                        "--env-vars",
                        *env,
                        "--tags",
                        *TAGS,
                    ],
                )
        self.state["config"] = config
        self.save()
        print("Separate tagged ACA apps deployed; readiness still requires verification")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["base", "key", "apps"])
    args = parser.parse_args()
    deployment = Deployment()
    getattr(deployment, args.phase)()
