# Instructions for coding agents

These instructions apply to the entire SenOS repository. Read the README before contributing and follow its collaboration workflow.

## Branches and collaboration

- Work on a focused task branch, not `main`. Use `feat/`, `fix/`, `docs/`, `test/`, `chore/`, or `refactor/` followed by a lowercase, hyphen-separated task name.
- Inspect the current branch and working tree before editing. If already on an appropriate task branch, continue there. If on `main` with a clean working tree, create the appropriate task branch before editing.
- Preserve existing user changes. Do not reset, discard, or move unrelated work. If the current branch or existing edits conflict with the requested task, ask for direction before changing branches.
- A local task branch may be created as part of an authorized edit. Do not commit, push, open a pull request, or merge unless the user has authorized that action in the session.
- When authorized to publish, target `main` through a pull request. Summarize the change, its purpose, validation, and limitations. Follow the README's teammate-review and squash-merge workflow; do not merge without the required review.
- Use commit subjects such as `feat: add workload registration` or `docs: explain Linux setup`.
- Do not push directly to `main` or force-push shared branches. Never rewrite shared history without explicit authorization.

## Implementation boundaries

- Build the CLI first. Keep presentation separate from application services so a future UI can reuse them.
- Keep changes focused and coordinate changes to shared contracts. Update documentation when behavior or setup changes.
- The current repository is a scaffold. Do not describe placeholder modules or planned commands as implemented features.
- Keep resource operations behind the restricted executor. Validate managed targets, allowlisted actions, bounds, expiration, and recovery behavior in deterministic code.
- Never execute arbitrary model-generated commands or silently change host resource policies as part of development. Use the agreed isolated Linux environment for policy experiments.
- Do not commit credentials, local environments, generated runtime data, or benchmark output.

## Verification and handoff

- Run checks appropriate to the change and `git diff --check`. Documentation-only changes need a focused content and link review, not new runtime tests.
- For resource-control changes, verify relevant behavior and recovery in Linux. Clearly distinguish unit checks from Linux integration results.
- Report what changed, what was checked, and any checks that could not run. Never claim tests passed if they were not run.
- In the final handoff, identify the working branch and whether changes remain uncommitted or unpublished.
