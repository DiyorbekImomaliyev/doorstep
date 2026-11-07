# Model card: Doorstep delivery-risk model (`risk_v1`)

**Information, not a decision.** The model suggests a safer delivery option at checkout. It
never decides money, liability or a case; those stay with the rules, the policy service and
people.

## What it does

Gives the probability that an order sent by doorstep delivery ends in a delivery failure that
needs a fix (lost, stolen, damaged or misdelivered). It also gives the top three reasons, in plain
words. At or above `LOCKER_RISK_THRESHOLD` (tuning/engine_config.py) checkout recommends a locker.
High-value orders get a signature recommendation from the PayPal rule file first, whatever the
score.

## Training data: synthetic

The model is trained on **synthetic data**, so no real order, buyer or seller is in it. The
synthetic world is seeded from published delivery-failure rates:

- About 1–2% of orders have a costly problem. US doorstep theft affects about 0.5–1% of parcels.
- UK surveys report a much higher rate of any delivery problem (37%).
- The synthetic label sits between these: a failure needing a fix, at about 5–6% overall.
- Valuable parcels, US doorsteps, shops with more past problems, new shops and addresses the
  buyer kept after a failed check all raise the rate.
- Lockers and signatures lower it.

The size of each effect is an **assumption**. It is written down as `TRUTH` in
`app/ai/risk_train.py`. Production retrains on each seller's own outcomes, which is the
"Learn" stage.

## Inputs (minimum data)

| Feature | Meaning |
| --- | --- |
| `order_value` | Order value in major units (capped at 1,000) |
| `buyer_in_us` | 1 for a US delivery, 0 for the UK |
| `seller_case_rate` | The shop's own smoothed rate of delivery cases |
| `new_shop` | 1 if the shop has fewer than 20 orders |
| `method_locker` | 1 for locker pickup |
| `method_signature` | 1 for signature on delivery |
| `address_overridden` | 1 if the buyer kept an address that failed the check |

The model never sees a name, email, street address or postcode. That is deliberate: postcode-level
scoring can turn into unfair treatment of whole neighbourhoods.

## Method

- XGBoost: 186 depth-3 trees, early-stopped on a validation split.
- Monotone constraints: value, case rate, new shop and an overridden address can only raise the
  risk; a locker or a signature can only lower it.
- Platt scaling on the validation split for calibrated probabilities.
- Reasons are exact Shapley values from XGBoost's TreeSHAP (`pred_contribs`). They add up to the
  model's score exactly.
- Deterministic: `make risk-model` with the same seed rebuilds the same model. A test fails if
  `tuning/engine_config.py` changes without a retrain.

## Results (held-out synthetic test split, 24,000 orders)

| Metric | Value |
| --- | --- |
| Failure base rate | 5.64% |
| PR-AUC | 0.155 (a random score would get 0.056) |
| PR-AUC ceiling (true synthetic probabilities) | 0.156 |
| Expected calibration error | 0.005 |

The model gets close to the ceiling, which shows it learns this synthetic world well. It says
nothing about real-world accuracy. The full report and calibration plot are in
`evals/risk_report.md`.

## Scope and operation

- **Markets:** GB and US orders, in GBP and USD. Values are compared in major units.
- **Inputs:** the seven features above and nothing personal; carrier, area or season features
  would each need a fairness review first.
- **Fallback:** when the model is unavailable, checkout uses a transparent rule of thumb, so a
  buyer always sees a recommendation.
- **Retraining:** `make risk-model` is deterministic: the same seed gives the same model, report
  and calibration plot.
