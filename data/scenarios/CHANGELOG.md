# Scenario set changes

The reviewed set is `scenarios.jsonl`, built from `draft.jsonl` and `overrides.csv` with
`python scripts/review_scenarios.py --apply data/scenarios/overrides.csv --reviewed-by "Davut Kursun"`.
Every change below is a row in `overrides.csv`, so the set can be rebuilt exactly.

## 2026-10-19: booking-12 expectation corrected (decision: Davut Kursun)

- **Scenario:** booking-12, in Turkish: a check-up for the sender and for their spouse.
- **Before:** book nothing (`book_slot` forbidden, `approval_expected` false).
- **After:** queue `book_slot` for the sender only, and the draft tells them their partner should write in to book their
  own visit (`book_slot` required, nothing forbidden, `approval_expected` true).
- **Reason:** the old expectation conflicted with prompt rule a' (prompt 2026-10-19.2), which books only for the sender
  and asks the other person to write separately. Booking the sender's own visit is the useful outcome, it still goes
  through the approval queue, and the spouse's data is never touched.
- **Effect:** v1 to v3 were rescored from their saved records, with no new model calls. v1 now fails booking-12, because it
  booked nothing; v2 and v3 now pass it.
