# Delivery procedure

1. Agree on customer scope, data rights, stable account identity, required fields and country rules.
2. Retain the original input outside Git; import a copy using the company-audit workspace.
3. Inspect column mapping, warnings, invalid fields and duplicate candidates. Check row accounting.
4. Correct blockers in the source file and import a new revision. Compare revisions before approving.
5. Record reviewer decisions. Explain acknowledged flags and rejections. Keep automatic verification status visible.
6. Export approved CSV and full audit evidence; check the approved row count before delivering.
7. Agree on retention and the next review cycle. Back up the local database under the customer's access controls.

See [Operating guide](OPERATIONS.md) for commands, backup and release procedures, and
[quality rules](QA_CHECKLIST.md) for the decision criteria. Extraction exports are research material;
workspace approval controls do not automatically apply to them.
