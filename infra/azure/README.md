# Azure pilot deployment

`foundation.bicep` creates the isolated shared resources for the Murmur pilot. `apps.bicep` deploys the immutable backend and frontend images after the required credential values have been written to the generated Key Vault.

The apps use separate managed identities. The backend identity can pull its image and read the two Key Vault secrets. The frontend identity is pull-only and cannot read backend credentials.

Use `scripts/deploy_azure.py`; it validates git provenance, imports secrets without printing them, performs the builds, deploys both apps, and verifies the live result. Set `MURMUR_AZURE_SUBSCRIPTION_ID` and `MURMUR_AZURE_TENANT_ID` to the exact GUIDs for the intended target (or pass their equivalent CLI flags); the driver refuses a different active Azure account. Do not pass provider credentials as command-line arguments or Bicep parameters.

The deployer grants itself Key Vault secret-write access only when needed and removes that exact temporary role assignment after the writes. Image builds use unique, unguessable tags before resolving immutable digests, so concurrent releases cannot race on a shared source-SHA tag.

`FIREBASE_RUNTIME_SERVICE_ACCOUNT_PATH` must point to a dedicated Firebase Authentication Viewer credential. This read-only principal is the only Google credential imported into Azure. `FIREBASE_DOMAIN_ADMIN_SERVICE_ACCOUNT_PATH` is optional, must identify a different principal and key, and is used only by the local deploy process when the Container Apps hostname is not already authorized.

The low-cost visual pilot is intentionally limited to one backend process and one replica. Its SQLite database is local and ephemeral because SQLite locking is not safe on the SMB-backed Azure Files mount. Stored agents and history can reset on scale-down or deployment; move to PostgreSQL plus migrations before treating data as durable or raising the replica maximum.
