# Security

Yanxu processes repository metadata and, in AI mode, selected PR content. Do not put API keys, tokens, cookies, passwords, private keys, customer data, or unauthorized company code in this public repository.

Authentication stays in the GitHub and Codex CLIs. Local reports may contain private repository material and should remain private. Redaction is best effort and does not replace review before sharing.

The optional private service reads its database URL, bootstrap secret, and GitHub webhook secret from environment variables. PostgreSQL stores only SHA-256 API token and browser-session hashes and never stores the plaintext webhook secret. Browser login returns an eight-hour HttpOnly, SameSite=Strict cookie; revoking the underlying API token also invalidates that session. Snapshot and webhook ingestion keep fixed normalized field whitelists and drop source code, diffs, PR titles/bodies, logs, and unknown fields. Cost records contain a category, exact amount, currency and redacted evidence reference; do not paste invoice bodies, customer secrets, access tokens or personal data into that reference. Pilot exports inherit the sensitivity of those references and must remain inside the customer's approved storage. The Docker port binds to localhost by default; use HTTPS, set `YANXU_SECURE_COOKIES=true`, and apply network controls before exposing it beyond the host.

`ops backup` creates a full private-service PostgreSQL dump. The dump contains token hashes, cost references and delivery metadata even though it does not contain the environment webhook secret or plaintext API tokens. Store the dump and its manifest in customer-approved encrypted storage, restrict access, and never commit either file to a public repository. `ops restore` requires an exact SHA-256 manifest and explicit `--confirm-restore`, but authorization for that command remains an operator responsibility.

`pilot init` writes random private values to the Git-ignored `.env` and does not print them. It refuses to overwrite an existing file and applies mode `0600` on POSIX systems; Windows access still depends on the directory's ACL. `pilot doctor` reports only check identifiers and status messages. Keep `.env` in customer-approved secret storage and do not attach it to support tickets, screenshots, pilot bundles, or public issues.

Pilot acceptance and support records may reference customer tickets, meeting notes, invoices, or signed documents. Store only customer-approved references, not document contents, personal data, signatures, or credentials. `customer_confirmed` is an owner-recorded fact and is not an electronic signature or independent identity verification.

`prepare-fix` never executes repository code. `test-fix` executes only the explicit command in a temporary commit archive; this is process and credential isolation, not a hardened security sandbox. Never run untrusted test commands.

Please report security issues privately through the repository owner's GitHub contact channel rather than a public issue.
