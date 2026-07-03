# mmc-analyser-explain

Real `ExplainAnalyser` — SHAP-based global explainability with sklearn and XGBoost adapters.

Pattern per `docs/design/002-container-base.md`: 4-line Dockerfile extends `mmc/analyser-base`,
adds this package, and points `MMC_ANALYSER_MODULE` at
`analyser_explain.analyser:ExplainAnalyser`.

The base image owns all AWS IO; this container performs zero AWS SDK calls. Model artefacts
are already local by the time `compute()` runs — the config's `model_uri` is a local path.

## Output

- `analyser_metrics`: top-K global SHAP importances keyed as `shap/<feature>` (K=20 by default —
  keeps CloudWatch cardinality bounded).
- `payload`: Clarify-compatible `AnalysisReport` (`monitor_type="EXPLAINABILITY"`).
- Outcome is always `succeeded` on a clean run; explainability is descriptive, not gating.
