# F&O Market Brain V4 validation contract

Allowed before test-criteria agreement:

- unit tests of deterministic transformations and safety contracts;
- syntax/compile checks;
- CI validation;
- code review of point-in-time/derivative-exclusion behavior.

Not allowed before test-criteria agreement:

- resolving V4 historical outcomes;
- computing V4 accuracy, directional return, MFE/MAE, barrier, or P&L metrics;
- comparing V4 performance with V1/V2/V3;
- retuning any V4 constant from observed outcomes.

The first real V4 evaluation must use criteria agreed in advance. Any later changes prompted by those outcomes require a new Brain version.
