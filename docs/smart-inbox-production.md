# Smart Inbox — Production Operations

## Guarantees

- Every mutating request is represented as a durable proposal with ordered steps.
- Source idempotency prevents duplicate proposals for the same Telegram update/message.
- Proposal versions and compare-and-swap claims allow only one executor.
- Completed steps are never repeated by the normal retry path.
- Unknown mutations fail closed and require explicit approval.
- External calls with an uncertain outcome move to `needs_review`; they are never retried automatically.

## Lifecycle

`pending → executing → completed`

Recoverable local failures move to `failed` and may be retried. External uncertainty or a stale dispatched step moves to `needs_review`. Users may also move a pending proposal to `editing`, `cancelled`, or let it become `expired`.

## Operational checks

- `GET /health/ready` includes Smart Inbox counters and stale-execution recovery results.
- `GET /api/inbox/health` is restricted to configured Telegram administrators.
- Investigate any non-zero `needs_review` count before manually replaying an external action.
- A growing `executing` count indicates a stuck worker or database contention.
- A growing `failed` count indicates deterministic tool errors that are safe to inspect and retry after correction.

## Recovery policy

At startup, stale proposals are reconciled:

- No step was dispatched: proposal becomes `failed` and remains retryable.
- A local step failed before success: proposal remains retryable and completed steps are skipped.
- An external step was already dispatched: proposal becomes `needs_review` to prevent duplicate calendar events, reminders, documents, or publications.

## Incident procedure

1. Open the Inbox item and inspect its ordered steps and audit trail.
2. Verify the external system directly when the status is `needs_review`.
3. Do not use Retry when an external action may already exist.
4. Resolve duplicates in the external system first, then create a corrected request if needed.
5. Preserve the proposal and audit entries for post-incident analysis.

## Rollout checklist

- Full Python test suite passes.
- Mini App TypeScript/Vite production build passes.
- SQLite volume is mounted persistently in Railway.
- Telegram creator/admin permissions are verified with two separate users.
- Smoke-test one immediate low-risk action, one approved multi-step action, one cancellation, and one simulated external timeout.
- Confirm Inbox badges and counts update in Telegram and the Mini App.
