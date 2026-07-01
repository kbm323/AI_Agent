# Final Report Content Specificity Fix Plan

## Goal
Fix the remaining final-report content issue with a bounded, small TDD patch:

1. `✅ 합의안` must not quote/restate the full `🎯 결론` sentence.
2. Agreement bullets must be concrete supporting decisions: who/what/how.
3. `🚀 다음 액션` must prefer specific role/subject actions over generic reused strings.
4. Placeholder/specialist failure actions must preserve the concrete role name, e.g. `legal-reviewer`.

This is not a full semantic summarizer redesign. It is a regression fix for the observed issues.

## Acceptance Criteria

- Agreement section does not contain `최종 합의는 \`` or the full conclusion sentence.
- Agreement section includes concrete details when source text contains `placeholder`, `worker_execution_failed`, or `legal-reviewer`.
- Action section includes `legal-reviewer placeholder` and `worker_execution_failed` when those are present in team/specialist summaries.
- Action section does not fall back to the previous generic action `evidence 분리와 specialist 고유 output을 회귀 테스트로 고정한다` for legal-reviewer placeholder cases.
- Discord smoke with a fresh, non-template test question verifies the final thread body.

## Implementation Steps

1. Add RED assertions to `tests/test_runtime_architecture_v2_phase14_multi_bot.py`.
2. Change `_derive_agreement_items()` to accept role/specialist summaries or a derived `source_text` and build concrete bullets from source signals instead of quoting `conclusion`.
3. Change `_derive_action_items()` to order specific patterns before generic patterns:
   - `legal-reviewer` + `placeholder` → legal-reviewer placeholder action.
   - `placeholder` + `worker_execution_failed` → placeholder failure action.
   - generic `회귀 테스트`/`evidence` rules only as fallback.
4. Update local and Discord report calls consistently.
5. Update README with the bounded content-specificity policy.
6. Run focused RED/GREEN, targeted suite, broad suite, Markdown verification, and live Discord smoke.
