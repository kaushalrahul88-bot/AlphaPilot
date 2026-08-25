# Paper Session Quality Attestation v1

A clean paper session is a deterministic evidence result, not a user checkbox.

## Required evidence

- Attestation after 15:35 IST for the same weekday session.
- Passing API, Groww quote, 5-minute candle, and exact option-chain checks in all three windows:
  - Early: 09:15–10:30 IST
  - Mid: 11:00–13:30 IST
  - Late: 14:00–15:30 IST
- At least 210 minutes between the first and last passing critical snapshot.
- No recorded data/API incident during 09:15–15:35 IST.
- At least one completed lifecycle paper trade.
- No unresolved lifecycle paper position.
- Every completed trade must have at least one verified mark and a Groww-chain source identifier.

A failed critical snapshot makes the session unclean even if later checks pass.

## Safety boundary

The result can increment controlled-live paper-session evidence by at most one for a unique session date. It never enables live execution or calls an order endpoint.

The API is stateless and evaluates browser-submitted evidence. Therefore v1 is useful for disciplined paper validation but is not tamper-evident or a regulatory audit record. Server-side signed event persistence remains required before an execution-capable phase.
