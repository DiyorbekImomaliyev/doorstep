# PayPal integration plan — Phase C (PP)

SDK: `paypal` 2.29 from `context-plugins/paypal-python-sdk` @ `143e57f2a3729d7df3727386b8c9ddf023d9a5df`
(ADR-014). Every SDK fact below was looked up in `sdk-map.md`, `map/operations/{orders,payments}.md`
or the installed modules at that SHA. None comes from memory.

## Scope

| Acceptance | Operation | Route | How |
| --- | --- | --- | --- |
| PP-02 | create order with shipping options | `POST /v2/checkout/orders` | SDK models `OrderRequest` → body |
| PP-10 | patch shipping address | `PATCH /v2/checkout/orders/{id}` | SDK models `Patch`, `Address` → body |
| PP-03 | capture | `POST /v2/checkout/orders/{id}/capture` | response decoded with SDK `Order` |
| PP-04 | add tracking | `POST /v2/checkout/orders/{id}/track` | SDK model `OrderTrackerRequest` → body |
| PP-07 | refund (full / partial) | `POST /v2/payments/captures/{capture_id}/refund` | SDK `RefundRequest` → body; `Refund` decodes |
| PP-08 | capture state | `GET /v2/checkout/orders/{id}` | SDK `Order` decodes |
| PP-09 | dispute provide-evidence | `POST /v1/customer/disputes/{id}/provide-evidence` | REST multipart (not in SDK) |
| WH-01 | verify webhook signature | `POST /v1/notifications/verify-webhook-signature` | REST (not in SDK) |

## Design decision: SDK models plus Doorstep's own transport (ADR-015)

The SDK client (`PaypalClient`) is **not** constructed. Request bodies are built and validated with SDK
models and serialized with `to_dict()`. Success responses are decoded with SDK models. Transport,
OAuth, retries, logging and error mapping stay in `app/paypal_client/client.py` (httpx). Reasons,
each from a lookup:

1. **Token cache clock.** The SDK caches tokens on `time.monotonic()` with its own expiry buffer
   (`core/auth/schemes/oauth2_schemes.py:_expires_at`). PP-01 requires the app `Clock` and the
   configured `token_refresh_margin_s`.
2. **401 policy.** The SDK invalidates the token on a 401 and does not retry (`core/raw_client.py`:
   "a caller sees one 401 and then recovery"). PP-01 requires one refresh plus one retry in the same call.
3. **Error decoding.** The `Error` arm requires `name`, `message` and `debug_id` (`models/error.py`).
   An error body missing one fails to decode, raising `ValidationError` instead of `ApiError`, so
   the status is lost. PP-03 and PP-07 must map 422/4xx/5xx by status, even for thin bodies.
4. **Missing idempotency header.** `orders.patch_order` and `orders.create_order_tracking` take no
   `pay_pal_request_id` parameter. PP-05 requires `PayPal-Request-Id` on every write.
5. **Retries.** The SDK retries by default (`RetryOptions.max_retries=3`). Doorstep retries only in
   the jobs outbox with the same key (PP-07); a transport retry would hide attempts from it.

Revisit in Phase D: if the recorded sandbox shapes show that every PayPal error carries
`message` and `debug_id`, the SDK client could own transport via `custom_http_client` and
`oauth2_token_source`. Points 1, 2 and 4 would still need adapters.

## Contract sheet

Sync only. The app runs sync handlers and a sync worker, and no `AsyncPaypalClient` is used. No SDK
client exists, so `close()`/`aclose()` and `base_url` don't apply. The host is
`PayPalConfig.base_url`, which defaults to `PAYPAL_BASE_URL` (sandbox).

Models (`paypal.models`; enums `paypal.models.enums`). `Optional[T]` here is `T | UnsetType`, never
`None`. Unset fields are omitted by `to_dict()` (smoke-checked).

| Model | Required | Optional members we set | Wire aliases |
| --- | --- | --- | --- |
| `OrderRequest` | `intent: CheckoutPaymentIntentOrStr` (`CAPTURE`), `purchase_units: list[PurchaseUnitRequest]` | — | — |
| `PurchaseUnitRequest` | `amount: AmountWithBreakdown` | `shipping: ShippingDetails` | — |
| `AmountWithBreakdown` | `currency_code: str`, `value: str` | — | — |
| `ShippingDetails` | — | `options: list[ShippingOption]` | `type_` ↔ `type` |
| `ShippingOption` | `id: str`, `label: str`, `selected: bool` | `type_: ShippingTypeOrStr` (`SHIPPING`/`PICKUP`), `amount: Money` | `type_` ↔ `type` |
| `Money` | `currency_code: str`, `value: str` | — | — |
| `Patch` | `op: PatchOpOrStr` (`replace`) | `path: str`, `value: Any` | `from_` ↔ `from` |
| `Address` | `country_code: str` | `address_line_1`, `address_line_2`, `admin_area_2`, `admin_area_1`, `postal_code` | — |
| `RefundRequest` | — (empty body = full refund) | `amount: Money` | — |
| `OrderTrackerRequest` | `capture_id: str` | `tracking_number`, `carrier: ShipmentCarrierOrStr` (`OTHER`), `carrier_name_other`, `notify_payer` (default `False`) | — |
| `Order` (decode) | none (all `NotRequired`) | `id`, `status`, `links`, `purchase_units[].payments.{captures,refunds}` | — |
| `Refund` (decode) | none | `id`, `status`, `amount` | — |

Error unions (for reference only; the SDK's error path is not used, see point 3). `Error` arms:
create_order [400, 401, 422]; capture_order [400, 401, 403, 404, 422, 500]; get_order [401, 404];
patch_order [400, 401, 404, 422]; create_order_tracking [400, 403, 404, 422, 500];
refund_captured_payment [400, 401, 403, 404, 409, 422], with 500 → `RawError`.

Doorstep's error mapping (`app/paypal_client/errors.py`):
- 401 → refresh the token and retry once; a second 401 → `PayPalAuthError`.
- Other 4xx → `PayPalClientError(status, name, body)`; 422 `INSTRUMENT_DECLINED` on capture → `CaptureResult(status="DECLINED")`.
- 5xx → `PayPalServerError`; `httpx.TimeoutException` → `PayPalTimeout`.
- No transport-level retries.

Headers: `PayPal-Request-Id` on every write (PP-05). `Prefer: return=representation` on create
and capture, because the SDK default `return=minimal` omits captures. `Authorization: Bearer …`
is never logged.

Multipart provide-evidence and verify-webhook-signature are REST, outside the SDK (CLAUDE.md).

## Duplicate claims

| Write | Where the claim is stored | What rejects the second one | Where that rejection is caught | Where in the code |
| --- | --- | --- | --- | --- |
| Refund | `money_actions` row (Postgres/SQLite), written before the job is enqueued | unique `(decision_id, kind)` constraint | `IntegrityError` in `PolicyService` approval path | `app/policy/service.py::PolicyService._claim_money_action` → `app/policy/service.py::RefundJobHandler.__call__` |
| Capture / create / patch / tracking | `jobs` row + `PayPal-Request-Id` derived from the entity id | PayPal dedupes on the same key; the job row is unique per entity/action | job status check in the runner (row lock) | `app/worker/runner.py::enqueue` → `app/worker/handlers.py::AddTrackingHandler.__call__` |

## Assumptions & blockers

- No blocker for offline tests.
- Live checks: the PayPal MCP is broken server-side, so smoke checks use direct REST with the `.env`
  credentials (read-only `GET` order only).
- `PAYPAL_WEBHOOK_ID` is not set. Live webhook verification waits for the human (Phase D).

## Required reading

- MUST load `python-models`: building `OrderRequest`/`Patch`/`RefundRequest`; UNSET vs None.
- MUST load `python-error-handling` (floor): why the SDK error path is bypassed (decode failures).
- `python-client-initialization` and `python-testing`: not triggered. No SDK client is constructed
  and no test file is created (tests are locked); revisit if ADR-015 is reversed.
