# F&O V7 Holdout C admission pause

`ALPHAPILOT_FNO_V7_HOLDOUT_C_PAUSED=true` blocks only POST admission to the V7 Holdout C dataset builder with HTTP 423.

The gate is operational and does not alter the frozen Holdout C protocol, cached historical transport data, decisions, result semantics, or live-execution safety. Status/result reads remain available. Set the variable back to `false` to allow the existing durable resume path to continue from cached progress.
