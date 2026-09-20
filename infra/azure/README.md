# Azure pilot deployment

`foundation.bicep` creates the isolated shared resources for the Murmur pilot. `apps.bicep` deploys the immutable backend and frontend images after the required credential values have been written to the generated Key Vault.

Use `scripts/deploy_azure.py`; it validates git provenance, imports secrets without printing them, performs the builds, deploys both apps, and verifies the live result. Do not pass provider credentials as command-line arguments or Bicep parameters.

The pilot is intentionally limited to one backend process and one replica. SQLite is mounted from Azure Files with rollback journaling. Move to PostgreSQL plus migrations and distributed admission before raising the replica maximum.
