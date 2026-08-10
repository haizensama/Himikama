# Himikama Variant Evaluation Summary

Main article metrics use `final_potentially_violated_articles`, not `articles_identified`.

| Variant | Precision | Recall | F1 | Outcome Acc. | Unsupported Final | Rejected Overclaim | Weak Overclaim | No-Violation False Violation | Case Hit@3 | Case MRR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| controlled_no_step9 | 0.507 | 0.739 | 0.602 | 0.560 | 0.493 | 0.325 | 0.000 | 0.600 | 0.000 | 0.000 |
| full_himikama | 0.837 | 0.783 | 0.809 | 0.740 | 0.163 | 0.036 | 0.000 | 0.100 | 0.000 | 0.000 |

## Full Himikama vs No-Step-9 Ablation Delta

For good metrics, positive means `full_himikama` is higher. For bad metrics, positive means `full_himikama` reduced the bad rate.

| Metric | Direction | Delta / Reduction |
|---|---|---:|
| final_article_precision | higher is better | 0.330 |
| final_article_recall | higher is better | 0.043 |
| final_article_f1 | higher is better | 0.207 |
| overall_assessment_accuracy | higher is better | 0.180 |
| no_violation_non_overclaim_accuracy | higher is better | 0.500 |
| unsupported_final_article_rate | lower is better | 0.330 |
| rejected_article_overclaim_rate_vs_gold_rejected | lower is better | 0.289 |
| weak_article_overclaim_rate_vs_gold_weak | lower is better | 0.000 |
| no_violation_false_violation_rate | lower is better | 0.500 |
| confidence_calibration_mean_absolute_error | lower is better | 0.234 |

## H1/H2 Interpretation

- H1 is supported by the primary overclaiming metrics: `full_himikama` reduced at least one hallucination/overclaiming metric without worsening the others.
- H2 trade-off pattern is not clearly present from aggregate metrics.

## Interpretation Notes

- `Final Article F1` is the main article prediction score.
- `Unsupported Final` is a hallucination/overclaiming proxy: lower is better.
- `Rejected Overclaim` should be as low as possible.
- `Weak Overclaim` should be as low as possible.
- `No-Violation False Violation` should be as low as possible.
- `articles_identified` is diagnostic only and is not the final article metric.
- Case retrieval metrics are evaluated only where gold relevant case IDs exist.
