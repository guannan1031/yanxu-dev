# Controlled implementation

`implement` turns an existing Yanxu task contract into a proposed source patch, applies it to an immutable archive of the current `HEAD`, and runs one explicit test command. It produces a diff, test log, JSON manifest and standalone HTML report for human review.

## Run

Create and inspect the contract first:

```bash
python -m yanxu task "Make sample.value return 1" --repo . --output runs/tasks/value
```

Commit or remove unrelated working-tree changes, then name every source file the model may edit:

```bash
python -m yanxu implement runs/tasks/value/task.json \
  --checkout . \
  --allow-path sample/value.py \
  --output runs/implement \
  --command python -m unittest discover -s tests -v
```

The model receives the requirement, acceptance conditions, task-contract context and only the explicitly allowed source files. It cannot call tools in this step. Yanxu accepts only a standard diff that modifies 1–10 existing UTF-8/LF source files. Test files, hidden files, additions, deletions, renames and files outside the allowlist are rejected.

If the model's first response is empty or violates the patch boundary, Yanxu retries generation once. A second invalid result stops the run before any test or source modification.

`READY_FOR_HUMAN_REVIEW` means the generated patch applied cleanly to the recorded `HEAD` archive and the named test command returned zero. The original checkout and GitHub remain unchanged. Review `proposal.diff` and `test-output.log`; then apply the accepted diff in a normal feature branch and use `draft-pr` to publish it.

## Windows

The CLI uses Python 3.11+, Git and Codex CLI. The repository runs its regression suite on `windows-latest` with Python 3.11 in addition to Linux. The command allowlist recognizes the common Windows executables such as `python.exe`, `py.exe`, `npm.cmd`, `mvn.cmd` and `gradle.bat`.

This is process and credential isolation for a developer-authorized test command, not a hardened sandbox for untrusted repository code.
