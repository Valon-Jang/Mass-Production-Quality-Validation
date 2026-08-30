# Design notes

The public core models mass-production readiness as separate state dimensions rather than one overloaded status.

Each validation item tracks:

1. validation/test state,
2. quality review state,
3. external/internal approval state when required,
4. evidence completeness,
5. risk level and whether the risk is still open,
6. ownership and target date,
7. next action.

A later record does not silently overwrite an unrelated state dimension. A test PASS does not automatically mean quality approval, evidence completeness, or release permission.

The release gate returns `READY` only when every tracked item satisfies all required dimensions.
