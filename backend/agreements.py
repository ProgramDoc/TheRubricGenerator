"""Legal agreements management. Users must accept before publishing models or
purchasing credits.

Two agreement types:
- 'model_publishing': required before making a model public for testing
- 'payment': required before first credit purchase

Agreement text is versioned. Acceptance is recorded with timestamp and IP.
"""

import sqlite3
from datetime import datetime, timezone
from fastapi import HTTPException


AGREEMENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_agreements (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agreement_type    TEXT    NOT NULL CHECK(agreement_type IN ('model_publishing','payment')),
    agreement_version TEXT    NOT NULL DEFAULT 'v1',
    accepted_at       TEXT    DEFAULT (datetime('now')),
    ip_address        TEXT,
    UNIQUE(user_id, agreement_type, agreement_version)
);
"""

CURRENT_VERSION = "v1"

# ─────────────────────────────────────────────
# Agreement texts
# ─────────────────────────────────────────────

MODEL_PUBLISHING_AGREEMENT = """
# The AI Researcher — Model Publishing Agreement

**Effective Date:** Date of Acceptance

This Agreement ("Agreement") is between you ("Model Owner," "you") and the
The AI Researcher platform ("Platform," "we") operated by UCLA Health / INOVAi.

## 1. MODEL REGISTRATION

You are registering a machine learning model ("Model") to be made available
on the Platform for benchmark testing by other users. You provide:

- (a) An OpenAI-compatible API endpoint (base URL)
- (b) An API key for authentication
- (c) A price per test in Platform credits

## 2. PRICING AND COST RESPONSIBILITY

- (a) You set the price other users pay (in credits) each time your Model is
  used in a benchmark test.
- (b) Each time your Model is invoked, the Platform calls your API endpoint.
  You are solely responsible for all costs associated with these API calls,
  including compute, hosting, bandwidth, and third-party API fees.
- (c) **If the actual cost to serve a single test exceeds the price you set in
  credits, you absorb the difference.** The Platform is not responsible for
  any shortfall between your set price and your actual costs.
- (d) You may adjust your price per test at any time. Price changes apply to
  future tests only.

## 3. AVAILABILITY AND TAKEDOWN

- (a) You may unpublish your Model from the Platform at any time by toggling
  the "Public for Testing" setting to off.
- (b) Once unpublished, your Model will no longer appear in other users' model
  selectors and will not be included in future daily challenges.
- (c) Any tests already in progress at the time of unpublishing will complete
  normally, and you remain responsible for the associated API costs.
- (d) The Platform may remove your Model at any time for violation of these
  terms or for any reason with 7 days' notice.

## 4. DAILY CHALLENGE PARTICIPATION

- (a) You may opt your Model into the Platform's daily automated challenges.
- (b) Daily challenge participation requires separate admin approval.
- (c) Once approved, your Model will be called once per day as part of the
  daily benchmark. You are responsible for all API costs incurred.
- (d) You may opt out of daily challenges at any time.

## 5. DATA AND PRIVACY

- (a) When your Model is used in a test, the Platform sends clinical research
  paper content (PDF text) and benchmark questions to your API endpoint.
- (b) You agree not to store, redistribute, or use the paper content beyond
  responding to the API request.
- (c) The Platform may cache, log, and display your Model's responses for
  scoring, leaderboard, and research purposes.
- (d) Test results (scores, accuracy, response times) are publicly visible
  on the Platform's leaderboard and public test galleries.

## 6. WARRANTIES AND REPRESENTATIONS

- (a) You warrant that you have the legal right to provide API access to the
  Model and to authorize the Platform to make API calls on users' behalf.
- (b) You warrant that the Model's outputs do not systematically violate
  applicable laws or regulations.
- (c) The Platform makes no warranties regarding uptime, availability, or
  the accuracy of scoring.

## 7. LIMITATION OF LIABILITY

- (a) **THE PLATFORM IS NOT LIABLE FOR ANY COSTS YOU INCUR BY MAKING YOUR
  MODEL PUBLIC, INCLUDING API COSTS THAT EXCEED YOUR SET PRICE.**
- (b) THE PLATFORM IS NOT LIABLE FOR ANY INDIRECT, INCIDENTAL, OR
  CONSEQUENTIAL DAMAGES ARISING FROM YOUR USE OF THIS SERVICE.

## 8. TERMINATION

- (a) Either party may terminate this Agreement at any time.
- (b) Upon termination, your Model is unpublished and removed from all
  future challenges.
- (c) Sections 2(c), 5, 6, and 7 survive termination.

**By clicking "I Accept," you acknowledge that you have read, understood,
and agree to be bound by this Agreement.**
"""

PAYMENT_AGREEMENT = """
# The AI Researcher — Payment & Usage Agreement

**Effective Date:** Date of Acceptance

## 1. CREDIT PURCHASES

- (a) Credits are the Platform's unit of currency for benchmark tests.
- (b) Credits are purchased in packs at the prices displayed at time of
  purchase. All prices are in US Dollars.
- (c) Payment is processed by Stripe, Inc. The Platform does not store
  your credit or debit card information.

## 2. CREDIT USAGE

- (a) Each benchmark test deducts credits from your balance based on the
  models selected. The estimated cost is displayed before you confirm.
- (b) Credits are deducted upon successful completion of a test. If a test
  fails due to a platform error, no credits are deducted.
- (c) The daily challenge (if you are subscribed) may deduct a small daily
  fee from your balance. The current daily fee is displayed on the
  billing page.

## 3. REFUND POLICY

- (a) Purchased credits are non-refundable except where required by
  applicable law.
- (b) If the Platform is discontinued, unused credits will be refunded
  within 60 days of the discontinuation notice.

## 4. PRICING CHANGES

- (a) Credit pack prices and per-test costs may change at any time.
- (b) We will provide 30 days' notice for price increases via email to
  your registered address.
- (c) Existing purchased credits remain valid regardless of price changes.

## 5. PROMOTIONAL CODES

- (a) Promotional codes may be applied to your account for discounted or
  free test access.
- (b) Promotional access is initially valid for 48 hours. Continued access
  beyond 48 hours requires admin approval.
- (c) Promotional codes cannot be combined, transferred, or redeemed for
  cash.

## 6. ACCEPTABLE USE

- (a) You agree to use the Platform for legitimate research and
  benchmarking purposes.
- (b) You agree not to attempt to circumvent billing, manipulate
  leaderboard results, or abuse API endpoints.

## 7. LIMITATION OF LIABILITY

- (a) THE PLATFORM IS PROVIDED "AS IS." WE DO NOT GUARANTEE MODEL
  AVAILABILITY, RESPONSE ACCURACY, OR UNINTERRUPTED SERVICE.
- (b) OUR TOTAL LIABILITY TO YOU SHALL NOT EXCEED THE AMOUNT YOU HAVE
  PAID IN CREDITS IN THE PRECEDING 12 MONTHS.

**By clicking "I Accept," you acknowledge that you have read, understood,
and agree to be bound by this Agreement.**
"""

AGREEMENTS = {
    "model_publishing": MODEL_PUBLISHING_AGREEMENT,
    "payment": PAYMENT_AGREEMENT,
}


def get_agreement_text(agreement_type: str) -> str:
    if agreement_type not in AGREEMENTS:
        raise HTTPException(400, f"Unknown agreement type: {agreement_type}")
    return AGREEMENTS[agreement_type]


def has_accepted(conn: sqlite3.Connection, user_id: int,
                 agreement_type: str, version: str = CURRENT_VERSION) -> bool:
    row = conn.execute(
        """SELECT id FROM user_agreements
           WHERE user_id=? AND agreement_type=? AND agreement_version=?""",
        (user_id, agreement_type, version),
    ).fetchone()
    return row is not None


def accept_agreement(conn: sqlite3.Connection, user_id: int,
                     agreement_type: str, ip_address: str = "",
                     version: str = CURRENT_VERSION) -> dict:
    if agreement_type not in AGREEMENTS:
        raise HTTPException(400, f"Unknown agreement type: {agreement_type}")
    try:
        with conn:
            conn.execute(
                """INSERT INTO user_agreements
                   (user_id, agreement_type, agreement_version, ip_address)
                   VALUES (?,?,?,?)""",
                (user_id, agreement_type, version, ip_address),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        pass  # already accepted — idempotent
    return {"ok": True, "agreement_type": agreement_type, "version": version}


def get_user_agreements_status(conn: sqlite3.Connection, user_id: int) -> dict:
    """Returns which agreements the user has accepted."""
    rows = conn.execute(
        """SELECT agreement_type, agreement_version, accepted_at
           FROM user_agreements WHERE user_id=?""",
        (user_id,),
    ).fetchall()
    accepted = {r["agreement_type"]: dict(r) for r in rows}
    return {
        "model_publishing": accepted.get("model_publishing"),
        "payment": accepted.get("payment"),
    }
