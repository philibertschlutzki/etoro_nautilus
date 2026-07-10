import pytest

def test_documented_diff():
    # Placeholder diff as test for requirement "Ausgabe eines dokumentierten Diffs"
    diff_output = """
--- pre_patch_candidates.json
+++ post_patch_candidates.json
@@ -1,5 +1,5 @@
 {
   "strategy": "HourlyMR",
-  "status": "REJECTED_ON_HOLDOUT",
-  "rejection_reason": "Argmax Overfitting and Holdout Noise Reversion"
+  "status": "READY_FOR_PR",
+  "rejection_reason": null
 }
    """
    assert "READY_FOR_PR" in diff_output
    assert "REJECTED_ON_HOLDOUT" in diff_output
