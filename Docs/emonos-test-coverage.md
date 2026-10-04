# Test requirement coverage

Generated from `test_*` pytest function docstrings by `tests/requirement_coverage.py`.
Tags on helper functions are intentionally excluded: every listed case is collected by pytest.

| Acceptance | Proves | Collected pytest cases |
|---|---|---|
| **T1** (HW-3) | Build both targets from the shared tree | `test_boot.py::test_target_image_is_present`, `test_boot.py::test_targets_use_common_runtime` |
| **T2** (ARCH-4) | Boot and serve emoncms | `test_boot.py::test_ab_layout`, `test_boot.py::test_app_stack`, `test_boot.py::test_boot_runtime` |
| **T3** (DATA-2) | Feed survives reboot | `test_boot.py::test_feed_survives_reboot` |
| **T4** (OS-1, OS-2) | RAUC slot reporting and signed bundle verification | `test_rauc.py::test_rauc_bundle_on_target`, `test_rauc.py::test_rauc_bundle_payloads`, `test_rauc.py::test_rauc_rejects_untrusted_signer`, `test_rauc.py::test_rauc_status` |
| **T5** (OS-11, OS-13, OS-15) | Install v2, boot B, retain data | `test_rauc.py::test_stage_pi_update_bundle`, `test_rauc.py::test_update_round_trip`, `test_rollback.py::test_healthy_update_commits_and_reboots` |
| **T6** (HC-4, OS-8, HC-9) | Broken v2 autonomously rolls back | `test_rollback.py::test_broken_update_rolls_back_autonomously` |
| **T7** (OS-14) | Interrupted install preserves prior slot | `test_power_cut.py::test_interrupted_install_keeps_active_slot` |
| **T8** (TEST-7) | One runner selects either target | `test_boot.py::test_harness_contract`, `test_coverage.py::test_generated_coverage_is_current` |

## Additional tagged coverage

| ID | Collected pytest cases |
|---|---|
| `HC-1` | `test_boot.py::test_app_stack`, `test_rollback.py::test_health_commits_factory_slot`, `test_rollback.py::test_healthy_update_commits_and_reboots` |
| `OS-10` | `test_rollback.py::test_runtime_watchdog_is_active` |
