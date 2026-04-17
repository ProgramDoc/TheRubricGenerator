#!/usr/bin/env python3
"""Cancel all legacy individual Pro / Enterprise Stripe subscriptions at
period end, as part of the Phase-5 enterprise-only cutover.

The new billing model is strictly enterprise-seat-based (see
backend/enterprise.py). This script retires the individual subscriptions
created by backend/membership.py so existing paying users are not double-
billed while they migrate to an enterprise org.

What it does
============
1. Loads every row from user_memberships where status='active' and
   stripe_sub_id is non-null.
2. For each, calls stripe.Subscription.modify(id, cancel_at_period_end=True).
   Stripe will continue to honor the paid period and stop billing after.
3. Leaves `user_memberships.status` as-is (so the app still knows the user
   had a membership), but stamps a column if we add one later. (For now we
   just rely on the Stripe-side cancel_at_period_end flag.)
4. Prints a tab-separated report to stdout so you can redirect it to a file
   for the audit trail.

What it does NOT do
===================
- Delete user data, revoke access, or migrate residual prepaid credit-pack
  balances. Credits in user_credits stay with the user; when they later
  accept an invite or create their own enterprise, a separate follow-up
  script can optionally move residual balances to their org pool.
- Change ENTERPRISE_MODE. Flip the env var separately once this script has
  run and the cancellations are reflected in Stripe.

Usage
=====
    # Dry run (recommended first):
    STRIPE_SECRET_KEY=sk_test_xxx DATABASE_URL=postgresql://... \\
        python scripts/cancel_legacy_subscriptions.py --dry-run

    # Real run:
    STRIPE_SECRET_KEY=sk_live_xxx DATABASE_URL=postgresql://... \\
        python scripts/cancel_legacy_subscriptions.py

Idempotent
==========
Re-running is safe. Stripe's modify endpoint is idempotent, and Stripe
subs that already have cancel_at_period_end=True just return the same
state.
"""
import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Reuse the app's DB wrapper so this works against both Postgres and SQLite.
from backend.db import get_db  # noqa: E402


def main(dry_run: bool) -> int:
    api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not api_key:
        sys.exit("ERROR: set STRIPE_SECRET_KEY before running.")
    try:
        import stripe
    except ImportError:
        sys.exit("ERROR: pip install stripe")
    stripe.api_key = api_key

    print(f"# Stripe mode: {'TEST' if api_key.startswith('sk_test') else 'LIVE'}")
    print(f"# Dry run: {dry_run}")
    print("#")
    print("# Columns: user_id\temail\tplan_name\tstripe_sub_id\taction\tresult")

    conn = get_db()
    try:
        # Join against users + membership_plans so the audit report carries
        # human-readable context.
        rows = conn.execute(
            """SELECT um.user_id, u.email, mp.name AS plan_name,
                      um.stripe_sub_id, um.status
                 FROM user_memberships um
                 JOIN users u            ON u.id = um.user_id
                 JOIN membership_plans mp ON mp.id = um.plan_id
                WHERE um.status='active'
                  AND um.stripe_sub_id IS NOT NULL
                  AND um.stripe_sub_id <> ''"""
        ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    ok = 0
    already_set = 0
    failed = 0
    skipped_free = 0

    for r in rows:
        user_id = r["user_id"]
        email = r["email"]
        plan = r["plan_name"]
        sub_id = r["stripe_sub_id"]

        # Free plan has no Stripe sub, so the WHERE clause should already skip
        # them. Defensive check just in case.
        if plan.lower() == "free":
            skipped_free += 1
            print(f"{user_id}\t{email}\t{plan}\t{sub_id}\tskip_free\t—")
            continue

        if dry_run:
            print(f"{user_id}\t{email}\t{plan}\t{sub_id}\tWOULD_CANCEL_AT_PERIOD_END\tdry-run")
            ok += 1
            continue

        try:
            sub = stripe.Subscription.retrieve(sub_id)
            if sub.cancel_at_period_end:
                print(f"{user_id}\t{email}\t{plan}\t{sub_id}\talready_set\tnoop")
                already_set += 1
                continue
            stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
            # Stripe will emit customer.subscription.updated; we let the
            # webhook path record it in our DB. Nothing to do here.
            print(f"{user_id}\t{email}\t{plan}\t{sub_id}\tcancel_at_period_end\tok")
            ok += 1
        except Exception as e:
            print(f"{user_id}\t{email}\t{plan}\t{sub_id}\tERROR\t{e}", file=sys.stderr)
            failed += 1

    print("#")
    print(f"# Total: {total}   OK: {ok}   Already set: {already_set}   "
          f"Skipped free: {skipped_free}   Failed: {failed}")
    print("#")
    print("# Next steps after a successful real run:")
    print("#   1. Verify on the Stripe dashboard that each sub shows "
          "'cancels at period end'.")
    print("#   2. Email affected users with instructions to join or create "
          "an enterprise org.")
    print("#   3. Flip ENTERPRISE_MODE=1 in the production env. Gating kicks "
          "in on the next boot.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="List what would be cancelled without calling Stripe.")
    args = ap.parse_args()
    sys.exit(main(dry_run=args.dry_run))
