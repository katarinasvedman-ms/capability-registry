# Change proposal: publish current .NET instructions as a new immutable pin

**State: proposed, not published or promoted.** Do not overwrite or repoint the
existing package, Toolbox versions 1/2, default version 1, or factory bindings.

The full change is in `dotnet-pinned-v1-to-current.diff`; exact source/pinned file
hashes and provenance are in `dotnet-proposal.json`. The comparison downloaded
the complete pinned archive, not just the instruction file. Both `SKILL.md` and
`references/validation.md` changed. Current source adds explicit valid/invalid
input and expected-outcome evidence to .NET input-validation proposals, retaining
NOT RUN when no authorized executor ran them.

The reviewed comparison is against source HEAD
`42583dff777676bb008f1d6fc581fe99459e56be` plus the captured work-in-progress bytes.
Current source semantic version is **1.0.3** and its package bytes match the
frozen snapshot. Historical Toolbox version 1 uses member service version **1**.
Its frontmatter contains no semantic version; service version 1 must not be
mislabelled semantic version 1.0.0.

## Normal publication proposal

Use the existing publisher's delta/hash-verification pattern in
`baselines\factory-skills\tools\publish_foundry.py`, not an in-place package edit:

1. Review the diff, exact file hashes and source version; freeze the approved
   source bytes. The local staged package is `.artifacts\dotnet-proposal\source`.
2. Upload only the changed .NET package through the normal
   `POST /skills/dotnet-development/versions?api-version=v1` multipart path.
   Record the service-assigned numeric version; do not preallocate or assume 2.
3. Download that exact new version's content and verify every file hash against
   the approved staging manifest, as the existing publisher does.
4. Propose a new immutable `factory-skills` Toolbox composition through
   `POST /toolboxes/factory-skills/versions?api-version=v1`, branching from the
   reviewed original default version 1. Keep the other nine skill pins at 1;
   replace only the .NET reference with the newly assigned pin. Do not mix in
   the separate version-2 mock connection by following an unreviewed latest version.
5. Record the new server-assigned Toolbox version and verification results.
   Leave default version 1 and all deployed bindings untouched. Promotion is
   a separate user decision; no publish/promote-default command is authorized
   or run by this proposal.

The source role-release publisher requires a reviewed source commit and
monotonic semantic version relative to its actual release baseline. A historical
general Toolbox binding is not automatically a valid role-release baseline.
Do not force it into `publish_roles` or invoke the broad `publish` helper, which
would upload every skill, merely to make this a one-line command.

Regenerate the read-only comparison with:

```powershell
.\.venv\Scripts\python.exe -m tools.propose_dotnet_change --live
```
