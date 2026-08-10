# Himikama Variant Evaluation Summary

Main article metrics use `final_potentially_violated_articles`, not `articles_identified`.

| Variant | Precision | Recall | F1 | Outcome Acc. | Unsupported Final | Rejected Overclaim | Weak Overclaim | No-Violation False Violation | Case Hit@3 | Case MRR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full_himikama | 0.837 | 0.783 | 0.809 | 0.740 | 0.163 | 0.036 | 0.000 | 0.100 | 0.000 | 0.000 |

## Interpretation Notes

- `Final Article F1` is the main article prediction score.
- `Unsupported Final` is a hallucination/overclaiming proxy: lower is better.
- `Rejected Overclaim` should be as low as possible.
- `Weak Overclaim` should be as low as possible.
- `No-Violation False Violation` should be as low as possible.
- `articles_identified` is diagnostic only and is not the final article metric.
- Case retrieval metrics are evaluated only where gold relevant case IDs exist.
