# Pre-Merge Review Checklist

- [x] Frozen four-stock identity unchanged.
- [x] Existing 1,600-click baseline protocol unchanged.
- [x] Enrichment is a new protocol.
- [x] Completed context candles only.
- [x] Event effective time must not exceed click time.
- [x] Unknown event time delayed to next session open.
- [x] Future baseline tape reused, not refetched.
- [x] Outcome excluded from feature construction.
- [x] Options, IV, Greeks, Futures excluded.
- [x] Research/shadow only; zero capital.
- [x] V1 thresholds documented before result review.
- [ ] Pull-request CI green.
- [ ] Main deployment verified.
- [ ] Enriched 1,600-observation replay completed.
- [ ] Zero look-ahead violations verified.
