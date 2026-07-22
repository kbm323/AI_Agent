# Discord Save SDD Progress

Baseline: c7d52c7; Ubuntu 87 passed; Windows 82 passed with 5 known environment-specific failures.
Task 1: complete (commits 71d58b7..d93059d, review clean)
Task 2: complete (commits d93059d..3f52906, review clean; 9 tests; Ruff clean)
Task 3: complete (commits 3f52906..f0692be, review clean; 21 tests; Ruff clean)
Task 4: complete (commits f0692be..500a5e1, review clean; 24 tests; Ruff clean)
Task 5: complete (commits 500a5e1..4aae3c2, review clean; 23 tests; Ruff clean)
Task 6: complete (commits 4aae3c2..7d67150, review clean; 48 tests; Ruff clean)
Task 7: complete (commits 7d67150..721584e, review clean; 67 tests; Ruff clean)
Task 8: complete (commits 721584e..27d1ba4, review clean; 100 tests; Ruff clean; official skill-command/plugin-tool path)
Task 9: complete (commits 27d1ba4..1f0e2d6, final review clean; 244 focused tests; whole-range Ruff, format, diff, and secret gates clean)

# Discord LLM Wiki/QMD SDD Progress

Plan: docs/superpowers/plans/2026-07-16-discord-llmwiki-qmd-commands.md
Baseline: 4ea2ed5
Task 1: complete (commits 4ea2ed5..4ae4073, review clean; 26 tests; Ruff clean)
Task 2: complete (commits 4ae4073..62b6bdd, review clean; 36 combined tests; Ruff clean; Linux live lock probe deferred to Task 7)
Task 3: complete (commits 62b6bdd..0096e48, review clean; 68 tests; Ruff clean; partial-write recovery verified)
Task 4: complete (commits 0096e48..d3f3475; unified abx-dl adapter committed on feature/llmwiki-commands; 84 combined tests and Ruff clean; Runtime v2 broad regression 731 passed, 1 skipped, with 2 unrelated pre-existing Phase 14 live-Discord failures; Ubuntu ARM64 live probe deferred to deployment)
Task 5: complete (commit 06bee97; transport-neutral ingest/note/find services; 13 focused tests; Task 1-5 integration 97 passed, 1 skipped; Ruff clean)
Task 6: complete (commit fb3a1f4; Hermes command registration, plugin 0.2.0, archive QMD hook; 137 integration tests; Ruff clean)
Task 7: complete (commits 649ac69..fe0bf7b; reconciliation CLI, ARM-safe QMD/abx-dl runbook, systemd and rollout guards; live Ubuntu ARM64 deployment complete)
Live deployment (2026-07-22): QMD 2.5.3 and abx-dl 1.11.235 installed; one `obsidian` collection indexes the Google Drive vault; the five-minute QMD reconciliation timer is active; plugin 0.2.0 was hash-verified and deployed to all seven Hermes profiles; Discord API verification reported 59 commands per bot with `/archive`, `/llmwiki-ingest`, `/llmwiki-find`, and `/llmwiki-note` present. Content-level smoke tests remain intentionally deferred to normal operation.
Final local verification: 240 focused passed, 1 skipped; changed-file Ruff clean; range secret scan clean; Runtime v2 broad 752 passed, 1 skipped, with the same 2 unrelated Phase 14 live-Discord failures. Node syntax 158 files passed; full repository Ruff remains blocked by existing backup/legacy debt and mypy was unavailable in the offline local cache.

# Discord Meeting Commands SDD Progress

Plan: docs/superpowers/plans/2026-07-22-discord-meeting-commands.md
Baseline: 6229f55
Task 1: complete (commit e3b559d; explicit command intent bypass, persisted Discord thread linkage, and profile-independent live projection tests; 68 focused tests passed)
Task 2: complete (commit d82596a; transport-neutral meeting start/report service; 18 tests; Ruff clean)
Task 3: complete (commit cd90e57; Hermes `/meeting-start` and `/meeting-report`, plugin 0.3.0; 49 combined tests; Ruff clean)
Task 4: complete (commit 850c9b7; existing design, runbook, operational guards, and progress record updated; 17 operational guard tests passed)
Task 5 verification: complete on Ubuntu ARM64 at commit 348c9d3 (5776 repository tests passed; range secret scan inspected 16 files; changed-file Ruff and `git diff --check` clean). Windows UTF-8 run passed 5770 tests with 1 skip and retained 5 known platform-specific failures (path separators, missing Bash, permission semantics, and timer precision).
Task 5 rollout: complete (merged as 23b3c99; plugin 0.3.0 installed from the official GitHub subdirectory on all seven profiles; source and installed `plugin.yaml`/`__init__.py` content matched; all seven Gateways running; Discord API reported 61 commands per bot with `/archive`, both `/meeting-*`, and all three `/llmwiki-*` commands present). Content-level meeting and command-response smoke remain intentionally deferred to supervised normal operation.
Task 6: complete (commit 17b7ccc; canonical meeting session/outcome schemas and atomic store paths)
Task 7: complete (commit 29ca21b; round-by-round durable transcript and generation evidence)
Task 8: complete (commit 8810628; content-derived outcome validation and evidence thresholds)
Task 9: complete (commit a1959ab; canonical evidence reports, atomic `reports/` artifacts, section-safe Discord delivery; 127 compatibility tests passed)
Task 10: complete (commit 0c473e4; exact Discord provenance, CEO-channel/thread routing, durable invocation reservation, 90-second fallback dedupe; 163 cross-meeting tests passed)
Task 11: complete (visible worker calls removed; six-role baseline is 12 discussion calls plus 1 outcome call; at most 3 concurrent calls per round; 111 focused tests and Runtime v2 UTF-8 suite 805 passed, 1 skipped). The default Windows code page run retained the same 5 documented CP949-only failures. Repository-wide Ruff remains blocked by 679 pre-existing style findings; changed-file F/I and diff checks are the completion gate for this task.
Task 12: complete (bounded convergence policy; minimum 2 and maximum 6 rounds; unresolved-role plus validation continuation; repeated disagreement after 2 outcomes triggers one evidence-bound CEO arbitration; non-agreed arbitration and round-six exhaustion require user decision; arbitration is stored as a visible CEO consensus round). TDD RED was observed for the missing policy and orchestration. Final focused suite: 125 passed. Runtime v2 UTF-8 suite: 819 passed, 1 skipped. Changed-file Ruff F/I and `git diff --check` passed.
