# Fixed-Design Evaluation Workflow Draft

## 1. Statut

This note is a docs-only draft for a possible future fixed-design evaluation workflow.

- It is not implemented as a public CLI workflow.
- The existing code remains the source of truth for current behavior.
- This note does not establish physical validation.
- This note does not promote, recalibrate, or modify any material.

## 2. Etat actuel

- A fixed design can already be evaluated through the internal linear API:
  `didgeridoo_optimizer.pipeline.evaluate_linear.evaluate(...)` or
  `LinearEvaluationPipeline.evaluate(...)`.
- The current public `run_optimizer` CLI is config-driven. Outside `--dry-run`,
  it runs the full optimization workflow from `--config`.
- There is no stable public `--design` CLI argument.

## 3. Design schema minimal

Candidate top-level fields:

- `id`: recommended stable design identifier.
- `segments`: required list of segment mappings.
- `metadata`: optional mapping for caller-provided annotations.

Candidate segment fields:

- `kind`
- `length_cm`
- `d_in_cm`
- `d_out_cm`
- `material_id`
- `profile_params`: optional mapping.
- `position_start_cm` / `position_end_cm`: optional and recalculable by the
  builder.

## 4. Workflow candidat futur

Candidate commands, not implemented:

```powershell
python -m didgeridoo_optimizer.pipeline.run_optimizer --config config.yaml --design design.yaml --dry-run
python -m didgeridoo_optimizer.pipeline.run_optimizer --config config.yaml --design design.yaml --output-dir results/fixed_design_eval
```

The intended split would be:

- `--dry-run`: parse and validate config, material paths, and design schema
  without writing evaluation artifacts.
- normal run: evaluate exactly one supplied design and write neutral
  fixed-design evaluation outputs.

## 5. Scope initial recommande

The initial implementation scope should stay narrow:

- linear evaluation only;
- no robustness phase;
- no nonlinear or toot phase;
- no optimization;
- no top-20, ranking, or Pareto workflow;
- exactly one evaluated design.

## 6. Outputs candidats

Neutral candidate names:

- `evaluated_design_result.json` / `evaluated_design_result.yaml`
- `evaluated_design_summary.txt`
- adapted `post_run_interpretation.txt`, or a dedicated fixed-design
  interpretation note

Avoid optimization-specific output names:

- `best_design`
- `top20_scores.csv`
- `pareto_overview.png`

## 7. Non-claims

- `valid=True` means the evaluated design passed current model-side hard
  constraints. It does not mean physical validation.
- `model_confidence` is a model-side indicator, not experimental proof.
- Materials are not promoted by fixed-design evaluation.
- Playability, toot behavior, and harmonicity remain model proxies unless
  separately validated.

## 8. Patch sequence future

- PR 1 docs-only: this note.
- PR 2: design YAML/JSON parser plus dry-run validation.
- PR 3: minimal linear evaluation export.
- PR 4: optional reporting polish.
