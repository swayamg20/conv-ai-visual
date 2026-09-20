# Azure pilot deployment

`foundation.bicep` creates the isolated shared resources for the Murmur pilot. `apps.bicep` deploys the immutable backend and frontend images after the required credential values have been written to the generated Key Vault.

The apps use separate managed identities. The backend identity can pull its image and read the two Key Vault secrets. The frontend identity is pull-only and cannot read backend credentials.

Use `scripts/deploy_azure.py`; it validates git provenance, imports secrets without printing them, performs the builds, deploys both apps, and verifies the live result. Do not pass provider credentials as command-line arguments or Bicep parameters.

The low-cost visual pilot is intentionally limited to one backend process and one replica. Its SQLite database is local and ephemeral because SQLite locking is not safe on the SMB-backed Azure Files mount. Stored agents and history can reset on scale-down or deployment; move to PostgreSQL plus migrations before treating data as durable or raising the replica maximum.
