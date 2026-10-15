# Reviewing introduction claims

`icml.bench_mentions` finds sentences where a paper says it built a benchmark or
dataset ("we introduce X, a new benchmark"). A rule finds them; a reviewer
decides each one. This file is the reviewer's rubric. Decisions go to
`config/introduced_review.json` through `--review-import`, one line per claim:

    claim_id<TAB>label<TAB>note

The claim to judge is one paper saying it built one **name**. Read the name, the
paper's title and the context sentences; judge what the sentence says the
authors made, not how the name looks.

## Labels

| Label | Meaning | Counted? |
|---|---|---|
| `E` | The paper introduces this benchmark and it is meant for evaluation: called a benchmark, test set, evaluation suite or testbed, or methods/models are evaluated on it in the paper. | yes, as a benchmark |
| `T` | The paper introduces this dataset or resource, built for training, fine-tuning, pre-training, or as a general resource, with no evaluation framing. | yes, as a dataset |
| `U` | The paper uses, downloads, subsets or re-processes data that already exists under that name. "We construct the FLEURS dataset for en→de" builds a FLEURS subset; "we collected the AlpacaEval dataset" downloads it. | no |
| `X` | The paper does introduce something, but the extracted name is not its name: a descriptor ("MCQ", "HOI", "Reinforcement Learning"), a truncation ("Thousand Voices" for *Thousand Voices of Trauma*), or a neighbouring word ("PyPI" for *ReccEval*). Note `real name <name>; E` or `real name <name>; T` (or just `real name unknown` when the paper gives no name). | yes, under the real name, when one is given |
| `N` | Not a data artifact: a method, model, model suite, metric, notation (`Dgen`, `Dshuffle`), a citation surname ("Raffel", "Das"), a section word ("Licenses", "Tier 1"). | no |
| `?` | The sentence and its context do not settle it. | no, until re-read |

`E` vs `T`: `E` needs evaluation evidence in the title or the context — it is
called a benchmark, test set, evaluation suite or testbed, or the context says
methods are evaluated or compared on it. A dataset whose context states no
purpose, or states training, is `T`. (A `T` dataset becomes a benchmark in the
counts when other papers state they evaluate on it; the label is about what its
own paper says.) A dataset "for training and evaluating" is `E`.

A long form of the published short name ("BEfore-AFter" for *BEAF*, "Comprehensive
RAG" for *CRAG*): label it as the claim deserves (`E`/`T`) and note
`name <short name>` — the short name is what other papers write.

## Notes

- Claims on a **registered** name (column `registered` is not `-`): is this the
  registered benchmark's own paper? If yes, note `same`. If it is a different
  product that happens to share the name, note `homonym`.
  Calibration examples (2026-10-06): "MTBench, a benchmark for motion transfer"
  is a homonym of MT-Bench; "MM-Bench, 111 mathematical-modeling problems" of
  MMBench; "MuSE, a benchmark for multi-modal agent systems" of MUSE; "BIRD,
  bronze inscription restoration" of BIRD (text-to-SQL). "We introduce
  BigCodeBench" in the BigCodeBench paper is `same`.
- For `X`, write the correct name: `real name EnConda-Bench`.
- Spelling of the name that differs only in case or punctuation is not an `X`
  ("Prism" for PRISM is fine).

## Calibration (150 claims, 2026-10-06)

| Stratum | Confirmed (E or T) |
|---|---|
| name in the claiming paper's title | 97% |
| body-only claim | 80% |
| a name several claims share | 63% |
| a plain-word name | 50% |
| a registered name | 70% (most of the rest: `U`) |

A title is a reason to look first, never a reason to skip: four in five
body-only claims are real.

## Corrections onto a registered name (2026-10-13)

A claim whose corrected name folds onto a registered benchmark (an `X` with
`real name LIBERO` in the LIBERO paper, or an `E`/`T` with `name …`) is that
benchmark's own introduction only by a recorded decision, never by the
paper's title. The claim's review entry carries a `target`:

    "target": {"benchmark_id": "libero", "decision": "same" | "homonym" | "excluded",
               "evidence": "<why this paper introduces (or does not introduce) that benchmark>",
               "reviewed_by": "<who>", "reviewed": "<date>"}

Without a `target` the claim is `pending`: listed for review, counted
nowhere. The four corrections decided so far (LIBERO, Bench2Drive, SMACv2:
`same`; Wikipedia: `excluded`) were recorded by Claude Code from the paper
title and claim sentence, and say so.

## One name, several papers (2026-10-13)

Claims on one name from different papers are never merged by a rule.
Shared authors, a citation between the papers (the later paper's reference
list naming the earlier title in full) or a shared repository are relations,
recorded on both entities as `identity_candidates` with their relation
kinds. One product is confirmed only by a reviewed record in
`config/introduced_review.json`:

    "same_product": [{"fold": "relbench", "arxiv_bases": ["2312.04615", "2407.20060"],
                      "evidence": "<the statement or official release that makes them one product>",
                      "reviewed_by": "<who>", "reviewed": "<date>"}]

Until then the entities stay apart with their own ids, developer sets and
adoption counts (the earlier claim keeps the existing id; the later one gets
a new id that is never reused). RelBench (position paper 2312.04615 and
benchmark paper 2407.20060, same authors, mutual citation) and Sine
(2405.16194 and 2410.05429, same lab, mutual citation) are candidates, not
confirmed: in this corpus the two RelBench papers link different
repositories and the Sine papers different project pages.

## A claim that vanished from the latest text (2026-10-15)

When a paper's arXiv text changes and a reviewed introduction sentence is
no longer extracted, `icml.update` keeps the last sentence on the review
entry (`retained_evidence`) and queues the claim as `vanished`. The
benchmark stays in the catalogue with that claim marked
`vanished_in_latest_version` — a benchmark that was introduced does not
stop having existed because a later version words it differently — and
its other claims are untouched. A reviewer compares the old sentence with
the new text and records one decision on the claim:

    "vanished_decision": {"decision": "retain" | "retire",
                          "evidence_old": "<the sentence reviewed>", "evidence_new": "<what the latest text says, or that it says nothing>",
                          "reason": "<why>", "reviewed_by": "<who>", "reviewed": "<date>"}

`retain` keeps the claim confirmed on the old sentence; `retire` excludes
it (label `U`). Either decision takes the claim out of the review queue at
the next update; its history row (`claim_history`) stays. The entry keeps
what was known of the paper when the claim was last extracted (`title`,
`authors`), so a benchmark whose paper left the matched set still names
its paper and developers. `retained_evidence.reason` says why the claim is gone:
`sentence_not_extracted` (the paper is still matched; its text, the parser
or the rules changed) or `paper_unmatched` (the paper left the edition's
matched set — a match or identity change — so no sentence of it is read
at all; the decision then also says whether the paper belongs to the
edition). The two claims that vanished before this record existed were
seeded from the git history of the catalogue and the archived HTML:
`2410.02942#noisymnist` (confirmed T, `paper_unmatched`: ICLR 2025 event
31893 "SymmetricDiffusers … Models over Finite Symmetric Groups" no longer
matches arXiv title "… on Finite Symmetric Groups", token overlap 0.70 <
0.85) and `2602.00628#175m+` (excluded X, `sentence_not_extracted`). A review whose sentence changed (`evidence_sha` differs) is
stale: nothing of it — label, name correction, target — is reused until the
claim is reviewed again.
