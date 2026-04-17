#!/usr/bin/env python3
"""Provision Stripe Products + Prices for the three enterprise seat types.

One-shot script — run it once against each Stripe environment (test + live)
after creating the Stripe account. Idempotent: re-running with the same seat
catalog re-uses existing products by metadata tag and only creates new Prices
if the per-seat monthly amount has changed.

Usage:
    # Dry run: prints what would be created, no network writes
    STRIPE_SECRET_KEY=sk_test_xxx python scripts/setup_enterprise_stripe.py --dry-run

    # Real run against Stripe test mode
    STRIPE_SECRET_KEY=sk_test_xxx python scripts/setup_enterprise_stripe.py

    # Production (be careful — this creates real billing products)
    STRIPE_SECRET_KEY=sk_live_xxx python scripts/setup_enterprise_stripe.py

Output:
    Prints env-var lines ready to paste into your deployment config:

        STRIPE_PRICE_SEAT_ADMIN=price_xxx
        STRIPE_PRICE_SEAT_ENGINEER=price_xxx
        STRIPE_PRICE_SEAT_GENERAL=price_xxx

Canonical source of the seat catalog is backend/enterprise.py:SEAT_TYPES. If
prices change there, re-run this script and Stripe will issue a new Price ID
for the updated amount (old Price stays active for existing subs via Stripe's
immutable-Price semantics).
"""
import argparse
import os
import sys
from pathlib import Path

# Make backend importable when run from the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.enterprise import SEAT_TYPES  # noqa: E402

PRODUCT_META_KEY = "rubricgen_seat_type"  # tag so we can locate our products

def setup(dry_run: bool = False) -> None:
    api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not api_key:
        sys.exit("ERROR: set STRIPE_SECRET_KEY in the environment first.")
    try:
        import stripe
    except ImportError:
        sys.exit("ERROR: pip install stripe")
    stripe.api_key = api_key

    print(f"# Stripe mode: {'TEST' if api_key.startswith('sk_test') else 'LIVE'}")
    print(f"# Dry run: {dry_run}")
    print()

    env_lines: list[str] = []

    for seat in SEAT_TYPES:
        code          = seat["code"]
        display_name  = seat["display_name"]
        price_cents   = seat["price_cents"]
        env_var       = seat["env_var"]
        tag           = f"enterprise_seat_{code}"

        # 1) Find or create the Product by metadata tag.
        # Stripe's Product list doesn't support metadata filtering directly, so
        # we fetch active products and scan. For a new account this is ~0 items;
        # once this script has run, exactly 3.
        existing_product = None
        for p in stripe.Product.list(active=True, limit=100).auto_paging_iter():
            if p.metadata.get(PRODUCT_META_KEY) == tag:
                existing_product = p
                break

        if existing_product:
            print(f"# {code}: product already exists ({existing_product.id})")
            product = existing_product
        else:
            if dry_run:
                print(f"# {code}: would create Product '{display_name} Seat' "
                      f"(metadata.{PRODUCT_META_KEY}={tag})")
                product = None
            else:
                product = stripe.Product.create(
                    name=f"{display_name} Seat",
                    description=f"Enterprise {display_name.lower()} seat — "
                                f"${price_cents/100:.0f}/month includes "
                                f"{seat['monthly_credits']} monthly usage credits.",
                    metadata={PRODUCT_META_KEY: tag,
                              "seat_code": code,
                              "monthly_credit_floor": str(seat["monthly_credits"])},
                )
                print(f"# {code}: created Product {product.id}")

        # 2) Look up existing monthly recurring Prices on this product.
        # Stripe Prices are immutable — if the dollar amount changes we create a
        # new Price. Re-running with the same amount re-uses the existing one.
        target_price = None
        if product:
            for pr in stripe.Price.list(product=product.id, active=True, limit=100).auto_paging_iter():
                if (pr.currency == "usd"
                        and pr.unit_amount == price_cents
                        and pr.recurring
                        and pr.recurring.interval == "month"):
                    target_price = pr
                    break

        if target_price:
            print(f"# {code}: price already exists ({target_price.id})")
        else:
            if dry_run or not product:
                print(f"# {code}: would create Price usd {price_cents} /month")
                target_price = None
            else:
                target_price = stripe.Price.create(
                    product=product.id,
                    unit_amount=price_cents,
                    currency="usd",
                    recurring={"interval": "month"},
                    metadata={PRODUCT_META_KEY: tag, "seat_code": code},
                )
                print(f"# {code}: created Price {target_price.id}")

        if target_price:
            env_lines.append(f"{env_var}={target_price.id}")
        else:
            env_lines.append(f"{env_var}=<dry-run, no price>")

    print()
    print("# Add these to your .env / Render env / staging config:")
    print("# ---")
    for line in env_lines:
        print(line)
    print("# ---")
    print("# Then restart the app so seed_seat_types() picks them up.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be created, don't call Stripe.")
    args = ap.parse_args()
    setup(dry_run=args.dry_run)
