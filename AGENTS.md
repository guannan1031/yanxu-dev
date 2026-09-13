# Contributor scope

This is a read-only PR/CI evidence tool. Changes must preserve the following:

- AI-generated advice must never become approval, merge authorization, or a successful test result.
- Reports bind to head, base, checks and reviews; new evidence requires re-verification.
- Never commit credentials, private PR contents, or `runs/`.
- Use synthetic fixtures for tests. `sample/` is an intentionally small public demo.
- Test with `python -m unittest discover -s tests -v`.
- Keep the core dependency-free and explain implemented versus planned features.
