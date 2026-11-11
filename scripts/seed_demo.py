"""`make demo`: seed the filmed Diyorbek and Malika scenario (E2E-02) and print its private links.

Idempotent: the same ids every run, so running it twice changes nothing. The links it prints
are the only way into the filmed demo; the public site offers judges scratch copies instead.
"""

from __future__ import annotations

from app.api.journey import BUYER, SELLER
from app.demo.seed import DEMO_TAG, seed_scenario
from app.policy.rules import load_rules
from app.runtime import ROOT, build_runtime


def main() -> None:
    rt = build_runtime()
    with rt.session_factory() as s:
        summary = seed_scenario(
            s,
            rulebook=load_rules(ROOT / "rules"),
            clock=rt.clock,
            settings=rt.settings,
            signer=rt.services.receipts,
            tag=DEMO_TAG,
        )
        s.commit()
    base = rt.services.app_base_url
    links = rt.services.links
    print(f"Seeded {len(summary.orders)} demo orders.")
    print(f"Seller Control Room: {base}/seller/{links.issue(summary.seller_id, scope=SELLER)}")
    for order in summary.orders:
        print(f"  buyer {order.key:16} {base}/t/{links.issue(order.order_id, scope=BUYER)}")


if __name__ == "__main__":
    main()
