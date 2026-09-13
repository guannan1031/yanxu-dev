# Security

Yanxu processes repository metadata and, in AI mode, selected PR content. Do not put API keys, tokens, cookies, passwords, private keys, customer data, or unauthorized company code in this public repository.

Authentication stays in the GitHub and Codex CLIs. Local reports may contain private repository material and should remain private. Redaction is best effort and does not replace review before sharing.

`prepare-fix` never executes repository code. `test-fix` executes only the explicit command in a temporary commit archive; this is process and credential isolation, not a hardened security sandbox. Never run untrusted test commands.

Please report security issues privately through the repository owner's GitHub contact channel rather than a public issue.
