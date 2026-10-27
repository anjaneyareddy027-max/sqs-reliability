# Confirmatory tests

_Numbers come from the local simulator (DRY_RUN=1), not from AWS._

| hypothesis | campaign | dv | test | stat | p | p (Holm) | effect | size | decision |
|---|---|---|---|---|---|---|---|---|---|
| H1 | A_vt_consumer_kill | loss_rate | none (no variance) | 0 | 1 | 1 | n/a | 0 | fail to reject H0 |
| H2 | B_mrc_unhandled_error | recovery_time_s | Kruskal-Wallis | 2.426 | 0.4888 | 1 | epsilon_squared | 0.128 | fail to reject H0 |
| H3_loss | E_guidance_transfer | loss_rate | none (no variance) | 0 | 1 | 1 | rank_biserial | 0 | fail to reject H0 |
| H3_recovery | E_guidance_transfer | recovery_time_s | Mann-Whitney U | 62.5 | 0.02106 | 0.08425 | rank_biserial | 0.667 | fail to reject H0 |

## Exploratory

| hypothesis | campaign | dv | test | stat | p | p (Holm) | effect | size | decision |
|---|---|---|---|---|---|---|---|---|---|
| X_B_mrc_unhandled_error_dlq_capture_rate_by_max_receive_count | B_mrc_unhandled_error | dlq_capture_rate | Kruskal-Wallis | 18.36 | 0.0003701 | nan | epsilon_squared | 0.966 | - |
| X_C_mrc_datastore_reject_dlq_capture_rate_by_max_receive_count | C_mrc_datastore_reject | dlq_capture_rate | Kruskal-Wallis | 15.93 | 0.001171 | nan | epsilon_squared | 0.839 | - |
| X_C_mrc_datastore_reject_recovery_time_s_by_max_receive_count | C_mrc_datastore_reject | recovery_time_s | Kruskal-Wallis | 2.159 | 0.5401 | nan | epsilon_squared | 0.114 | - |
| X_D_vt_datastore_timeout_recovery_time_s_by_visibility_timeout | D_vt_datastore_timeout | recovery_time_s | Kruskal-Wallis | 23.67 | 9.307e-05 | nan | epsilon_squared | 0.986 | - |
| X_A_vt_consumer_kill_recovery_time_s_by_visibility_timeout | A_vt_consumer_kill | recovery_time_s | Kruskal-Wallis | 23.75 | 8.952e-05 | nan | epsilon_squared | 0.99 | - |
| X_A_vt_consumer_kill_duplicate_rate_by_visibility_timeout | A_vt_consumer_kill | duplicate_rate | one-way ANOVA | 18.85 | 1.461e-06 | nan | eta_squared | 0.79 | - |
| X_baseline_vt_batch_throughput_msg_s_by_batch_size | baseline_vt_batch | throughput_msg_s | Kruskal-Wallis | 92.82 | 5.425e-20 | nan | epsilon_squared | 0.938 | - |
| X_baseline_vt_batch_throughput_msg_s_by_visibility_timeout | baseline_vt_batch | throughput_msg_s | Kruskal-Wallis | 0.4229 | 0.9806 | nan | epsilon_squared | 0.00427 | - |
