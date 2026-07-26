# Project Rules

## Standing Rules

1. Conversation and implementation notes must be kept in `CONVERSATION_LOG.md`.
2. Project direction, architecture decisions, risks, and verification results must be kept in `PROJECT_ANALYSIS.md`.
3. User-facing conversation should be in Chinese unless the user requests otherwise.
4. Before a new feature/change round starts, work must be isolated on a Git branch.
5. After major verified changes, automatically commit and push the corresponding repository updates to GitHub unless the user explicitly asks to keep the work local.

## Branch Promotion Rule

This project does not use long-lived feature branches as the final delivery state. When a new work branch is completed and verified:

1. Back up the current `main` branch to a separate archive/backup branch before promotion.
2. Promote the completed work branch to replace `main`.
3. Keep the completed work branch name available as the backup label for that main state when the next main replacement happens.
4. Push the backup branch, the promoted `main`, and the work branch to the remote repository.
5. If replacing `main` cannot be done as a fast-forward, stop and make the required force-push risk explicit before changing the remote.

Recommended backup branch name:

```text
archive/main-before-<content-summary>
```

Use a content summary such as `python311-uvr-vst3-runtime`, not a date-driven name.
Dates may be appended only when they disambiguate two backups with the same content summary.
