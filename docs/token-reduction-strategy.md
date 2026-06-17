# Token Reduction Strategy

Schema: `token-reduction-strategy.v1`

## 40-50% Savings Target

Reduce exposed loop tokens by at least 40-50% compared with replaying every full turn.

Target band: 40-50% reduction from raw full-history replay.

## Original Text Retention Policy

Persist complete user requests, OpenClaw drafts, Hermes review requests, Hermes reviews, final synthesis, and escalation messages in raw turn storage for audit and replay.

Raw storage remains the source of truth. Summaries are derived context, not replacements for stored meeting turns.

## Exposed Context Summary Separation

Expose only bounded visible summaries to loop prompts, meeting history, and user-facing thread output; raw full text is not replayed unless an explicit audit/debug path requests it.

The loop context boundary is `turns.visibleSummary`; raw `turns.content` stays behind the persistence/audit boundary.

## Compressed Context Approach

Each round carries request summary, latest draft summary, latest Hermes verdict, accepted feedback, rejected feedback, and escalation reasons instead of replaying full raw text.

The compressed loop context should carry request summary, latest OpenClaw draft summary, latest Hermes verdict, accepted feedback, rejected feedback, and escalation reasons.

## Baseline Measurement

Method: `deterministic-local-estimate-v1`

Representative turns: 7

Raw full-text tokens: 734

Exposed summary tokens: 260 (64.6% reduction)

Compressed context tokens: 67 (90.9% reduction)

Target thresholds:

- 40% reduction: expose at most 440 tokens, saving at least 294 tokens
- 50% reduction: expose at most 367 tokens, saving at least 367 tokens

## Validation Sections

- 40-50% Savings Target
- Original Text Retention Policy
- Exposed Context Summary Separation
- Compressed Context Approach
- Baseline Measurement
