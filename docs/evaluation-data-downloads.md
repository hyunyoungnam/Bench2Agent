# Per-paper evaluation data downloads

## User outcome

Select a paper, select an evaluation dataset used or introduced in that paper,
inspect its configuration/split and a few records, then download the actual
records as CSV. One CSV contains one dataset/configuration/split; datasets with
different schemas are not concatenated into a single table. The existing
`/datasets/*.csv` files contain source inventories and do not satisfy this goal.

## Feasibility findings (2026-09-27)

The workflow is implementable for accessible datasets containing text or table
records. Universal automatic reconstruction of every paper's evaluation set is
not established. Two separate facts must be verified: a source can be exported,
and its records match what this paper evaluated.

| Source checked | Evidence inspected | Export supported by the source structure |
|---|---|---|
| [GSM8K](https://huggingface.co/datasets/openai/gsm8k/blob/main/README.md) | Configurations `main` and `socratic`; `test` has 1,319 examples; `question` and `answer` are strings | Question/answer CSV for a selected configuration and split |
| [MATH-500](https://huggingface.co/datasets/HuggingFaceH4/MATH-500) | Viewer exposes 500 test rows with `problem`, `solution`, `answer`, `subject`, `level`, `unique_id` | Six-column CSV of the public test release |
| [SPORTU](https://github.com/chili-lab/SPORTU) | Author repository links text and video-question JSON files and separately hosted video ZIPs | Text questions can become CSV; video question/annotation CSV still requires separate video assets |
| [SPORTU text JSON](https://raw.githubusercontent.com/chili-lab/SPORTU/main/data/SportU_text.json) | Inspected records contain `question`, `Answer`, `Explanation` | Preserve those columns and embedded newlines using CSV quoting |
| [ImageNet](https://huggingface.co/datasets/ILSVRC/imagenet-1k) | Access conditions required; image data; public test labels are missing | Authorized access and a media download are required; CSV alone cannot carry the full image evaluation dataset |

Hugging Face provides [split discovery](https://huggingface.co/docs/dataset-viewer/splits),
[row slices](https://huggingface.co/docs/dataset-viewer/rows) and
[Parquet file discovery](https://huggingface.co/docs/dataset-viewer/parquet).
The row endpoint is limited to 100 rows per request and requires Parquet exports.
Check pending/failed/partial/truncated results rather than treating a preview as
the complete dataset. GitHub's [contents API](https://docs.github.com/en/rest/repos/contents)
provides file discovery and raw download locations.

These findings come from source documentation, dataset viewers, and inspection
of published raw JSON. No end-to-end external dataset download/conversion was
completed in this investigation: the terminal API request was blocked by the
network sandbox and its escalation was interrupted. Do not report it as a
successful conversion test.

## Gaps in Bench2Agent's current data

- `card_terms.json` supplies abstract dataset names, not evaluation split,
  revision or sample IDs. Mention does not establish experimental use. For
  example, local gid 70 (Omni-MATH) mentions GSM8K while discussing limitations
  of prior benchmarks; that abstract alone cannot establish evaluation on it.
- Local gid 3840 (QeRL) explicitly reports results on MATH-500 in its abstract.
  This establishes benchmark use, but exact release revision and preprocessing
  still require the paper's experimental details or code.
- Local gid 156 (SPORTU) describes its introduced benchmark and points to the
  author repository. It is a useful first case for a JSON-to-CSV adapter.
- `resources.json` preserves extracted links and evidence. Its full-text
  coverage describes the bundle's extraction inputs. At inspection time this
  checkout had no `data/interim/fulltext*.jsonl` files, so the recorded coverage
  does not mean the original full texts are locally available.

## Implementation contract

1. Store a paper-to-evaluation-dataset record with `gid`, relationship
   (`existing` or `introduced` when supported), source URL/repository, revision,
   configuration, split, subset/filter or sample IDs, and the paper/code evidence.
   Keep `mentioned`, `evaluation confirmed`, and `exact subset confirmed` distinct.
2. Begin with explicit mappings for SPORTU-text and a public text benchmark such
   as MATH-500. Fetch only the requested source. Do not infer that every Hub
   `test` split is the exact set used by a paper.
3. Preserve source columns and values. Quote commas, newlines and quotes. Encode
   nested lists/objects as JSON cells with a documented schema, or offer the
   native format if flattening would lose meaning. Media exports use separate
   assets plus a CSV manifest, clearly labeled as such.
4. Pin source revisions and record source URL, retrieval date, checksum, schema,
   row count and dataset configuration in a sidecar manifest. Include recorded
   access/license information. Never execute a repository's dataset loader as
   part of automatic discovery.
5. In the paper view, show datasets with role, verification status, available
   splits, row count, preview and download action. If the exact paper subset is
   unknown, label an available download as the canonical source dataset. For
   gated, missing or non-tabular data, show the actual source/access requirement.

## Acceptance checks for the first implementation

- Selecting a paper reaches its datasets without a corpus-wide CSV download.
- A confirmed SPORTU-text source exports actual question/answer/explanation
  records and round-trips CSV values without loss.
- A confirmed MATH-500 release exports all 500 records, not only a preview;
  the manifest records the exact revision and split.
- Background mentions do not become confirmed evaluation datasets.
- A paper's custom subset is not silently replaced by the complete benchmark.
- Partial fetches, missing access, media fields and unavailable labels are
  visible; no successful-download response is returned for incomplete data.
