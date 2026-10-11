# Evaluation

## Method
The generator injects six cause types into ~33% of sites and stores the ground truth (cause and key entity).
The harness asks about every faulty site with varied phrasing, plus 20 healthy sites where the correct answer is
"no root cause", and runs the complete workflow — the same code path as the API.

## Metrics and gates
| Metric | Definition | Gate |
|---|---|---|
| root_cause_accuracy | predicted cause equals the label | ≥ 0.90 |
| key_entity_accuracy | predicted vendor / permit / crew / region / activity equals the label | ≥ 0.85 |
| false_alarm_rate | healthy sites given a MEDIUM or HIGH root cause | ≤ 0.10 |
| citation_validity | answers whose citations all exist in the collected evidence | ≥ 0.98 |

## Caveats
- Synthetic data and rule-based ranking mean high scores verify correctness of the pipeline, not field accuracy.
- Distractors are included (late-but-delivered purchase orders, resolved minor alarms) so that naive rules fail.
- Next step: replay anonymised real incidents and add an LLM-as-judge score for report usefulness.
