# Context Storage Boundary

Schema: `context-storage-boundary.v1`

## Source Of Truth

- User request: `tasks.user_request`
- Raw meeting turn: `turns.content`

## Loop Visible Fields

- `turns.visibleSummary`
- `RunTaskResult.meetingHistory[].summary`
- `LoopVisibleContextRetrievalResult.meetingHistory[].summary`
- `LoopVisibleContextRetrievalResult.compressedLoopContext.content`
- `DiscordDelivery.postThread.content`

## Audit Only Fields

- `turns.content`
- `DiscordDelivery.postThread.fullContent`

## Invariants

- Every persisted meeting turn keeps the complete original text in turns.content.
- Loop prompts, returned meeting history, and normal thread output use turns.visibleSummary-derived text.
- Raw full text is available only through persistence or explicit audit/debug paths.

## Verification Checks

- full original turn content is non-empty and retained exactly
- raw turn content does not appear in loop-visible context
- visible summaries appear in loop-visible context
- loop-visible retrieval returns meeting history summaries and compressed context without content fields
- raw retained text and loop-visible summary are retrieved through separate observable paths
