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
| `X` | The paper does introduce something, but the extracted name is not its name: a descriptor ("MCQ", "HOI", "Reinforcement Learning"), a truncation ("Thousand Voices" for *Thousand Voices of Trauma*), or a neighbouring word ("PyPI" for *ReccEval*). Put the real name in the note. | no |
| `N` | Not a data artifact: a method, model, model suite, metric, notation (`Dgen`, `Dshuffle`), a citation surname ("Raffel", "Das"), a section word ("Licenses", "Tier 1"). | no |
| `?` | The sentence and its context do not settle it. | no, until re-read |

`E` vs `T` when both apply: if the paper evaluates anything on it, or the title
calls it a benchmark, it is `E`. A dataset "for training and evaluating" is `E`.

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
