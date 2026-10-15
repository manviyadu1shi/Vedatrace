# AML Investigation Report: TX-FLAGSHIP
*Investigation INV-1 · status finished · produced by ollama/qwen2.5:14b*  
*Alerted transaction TX-FLAGSHIP: ACC-1913 → ACC-2106, amount 8400.0*

## Recommendation: **ESCALATE** (confidence 0.95)
The patterns identified in the investigation strongly suggest suspicious activity, including rapid fan-out, structuring, and circular flow, which are indicative of money laundering tactics. The shared device usage further raises concerns about the legitimacy of the transactions.

**A human analyst makes the final decision.** This report is decision support generated from graph evidence; it does not constitute a determination of wrongdoing.

## Executive summary
Investigation INV-1 of alerted transaction TX-FLAGSHIP revealed suspicious patterns involving shared device usage, rapid fan-out, structuring amounts, and circular flow. The transaction is likely involved in suspicious activity.

## Findings
### [PAT-1] RAPID_FANOUT (confidence 0.9)
ACC-2106 engaged in rapid fan-out behavior with 8 distinct recipients within 7 days, far above the account's typical behavior.
- [EV-1] **RAPID_FANOUT**: ACC-2106 sent funds to 8 distinct recipients within 7 days, far above typical behavior. — supported by [ACC-2106], [TX-05530], [TX-05538], [TX-05552], [TX-05566], [TX-05583], [TX-05588], [TX-05613], [TX-05625]
- Violates [POL-RAPID-FANOUT] *Rapid fan-out to many recipients* (severity HIGH, confidence 0.95): Pattern PAT-1 describes rapid fan-out behavior by ACC-2106, which matches the POL-RAPID-FANOUT policy that covers accounts redistributing funds to many distinct recipients within a short window.

### [PAT-2] STRUCTURING (confidence 0.9)
ACC-2106 engaged in structuring behavior with tightly clustered amounts just below review thresholds.
- [EV-2] **STRUCTURING_AMOUNTS**: ACC-2106 sent funds in amounts tightly clustered just below a review threshold. — supported by [ACC-2106], [TX-05530], [TX-05538], [TX-05552], [TX-05566], [TX-05583], [TX-05588], [TX-05613], [TX-05625]
- Violates [POL-STRUCTURING] *Structuring below reporting threshold* (severity HIGH, confidence 0.90): Pattern PAT-2 describes structuring behavior by ACC-2106, sending funds in tightly clustered amounts just below the review threshold, which aligns with the POL-STRUCTURING policy.

### [PAT-3] CIRCULAR_FLOW (confidence 0.95)
Circular flow involving ACC-1913, ACC-2106, and multiple intermediary accounts.
- [EV-3] **CIRCULAR_FLOW**: Circular flow involving ACC-1913, ACC-2106, and multiple intermediary accounts. — supported by [TX-05530], [TX-05541], [TX-05566], [TX-05583], [TX-05587], [TX-05588], [TX-05602], [TX-05613], [TX-05614], [TX-05625], [TX-05628], [TX-05650], [TX-05686], [TX-05688], [TX-05738], [TX-FLAGSHIP]
- Violates [POL-LAYERING] *Layering through circular fund movement* (severity HIGH, confidence 0.95): Pattern PAT-3 describes a circular flow involving ACC-1913, ACC-2106, and intermediary accounts, which fits the POL-LAYERING policy on circular fund movement with no apparent economic purpose.

### [PAT-4] SHARED_DEVICE_CLUSTER (confidence 0.9)
Shared device cluster involving ACC-1913, ACC-2106, and ACC-1625.
- [EV-4] **SHARED_DEVICE**: DEV-0389 shared by accounts of different customers (ACC-2106 and ['ACC-1625', 'ACC-1913']) — supported by [ACC-1625], [ACC-1913], [ACC-2106], [DEV-0389]
- Violates [POL-SHARED-DEVICE] *Undisclosed device sharing across unrelated customers* (severity MEDIUM, confidence 0.90): Pattern PAT-4 indicates shared device usage by ACC-1913, ACC-2106, and ACC-1625, which corresponds to the POL-SHARED-DEVICE policy on undisclosed device sharing across unrelated customers.

### [PAT-5] SHARED_DEVICE_CLUSTER (confidence 0.9)
Shared device cluster involving ACC-1913, ACC-1370.
- [EV-5] **SHARED_DEVICE**: DEV-0390 shared by accounts of different customers (ACC-1913 and ['ACC-1370']) — supported by [ACC-1370], [ACC-1913], [DEV-0390]
- Violates [POL-SHARED-DEVICE] *Undisclosed device sharing across unrelated customers* (severity MEDIUM, confidence 0.90): Pattern PAT-5 indicates shared device usage by ACC-1913 and ACC-1370, which corresponds to the POL-SHARED-DEVICE policy on undisclosed device sharing across unrelated customers.

## Analysis
The investigation identified patterns of rapid fan-out [PAT-1], structuring [PAT-2], and circular flow [PAT-3] involving the accounts ACC-1913 and ACC-2106. Additionally, shared device usage was confirmed [PAT-4, PAT-5]. These patterns match policies on rapid fan-out [POL-RAPID-FANOUT], structuring [POL-STRUCTURING], and layering [POL-LAYERING]. The most similar prior case [CASE-H1] was escalated due to a rapid fan-out pattern with structuring and circular flow, which aligns with the current investigation findings.

## What was ruled out
No benign explanations were recorded by the Investigator.

## Prior cases recalled from memory
- [CASE-H1] **ESCALATED** — Hub account fan-out ring (escalated) *(synthetic demo history)*
- [CASE-H3] **CLEAR** — Payroll account with large recurring fan-out (cleared) *(synthetic demo history)*
- [CASE-H2] **CLEAR** — Household device and bill-splitting (cleared) *(synthetic demo history)*

## Reasoning path (Investigator steps)
1. [INV-1-S01] `get_transaction` — To understand the details of the alerted transaction, including sender, receiver, amount, time, channel, and monitoring score.
2. [INV-1-S02] `get_account_profile` — To gather details about the sender account, including its owner, devices, age, in/out counts, counterparties, volumes, and first/last activity.
3. [INV-1-S03] `get_account_profile` — To gather details about the receiver account, including its owner, devices, age, in/out counts, counterparties, volumes, and first/last activity.
4. [INV-1-S04] `find_shared_devices` — To identify other accounts and customers that share the device used by both sender and receiver.
5. [INV-1-S05] `find_shared_devices` — To identify other accounts and customers that share the device used by both sender and receiver.
6. [INV-1-S06] `analyze_fanout` — To check for rapid fan-out or structuring patterns in the sender's account.
7. [INV-1-S07] `analyze_fanout` — To check for rapid fan-out or structuring patterns in the receiver's account.
8. [INV-1-S08] `write_evidence` — Unusual recipient burst compared with this account's own baseline.
9. [INV-1-S09] `write_evidence` — Amounts are tightly clustered between 958.65 and 988.82.
10. [INV-1-S10] `write_pattern` — To document the rapid fan-out behavior of ACC-2106.
11. [INV-1-S11] `write_pattern` — To document the structuring behavior of ACC-2106.
12. [INV-1-S12] `trace_flow` — To trace the flow of money forward and backward from the alerted transaction to identify any layering loops or circular flows.
13. [INV-1-S13] `trace_flow` — To trace the flow of money forward and backward from the alerted transaction to identify any layering loops or circular flows.
14. [INV-1-S14] `find_cycles` — To check for directed money cycles involving the sender account.
15. [INV-1-S15] `find_cycles` — To check for directed money cycles involving the receiver account.
16. [INV-1-S16] `write_evidence` *(rejected/error)* — Confirmed by find_cycles analysis showing multiple multi-hop loops involving the alerted transaction.
17. [INV-1-S17] `write_pattern` *(rejected/error)* — Confirmed by find_cycles analysis showing multiple multi-hop loops involving the alerted transaction.
18. [INV-1-S18] `write_evidence` — Confirming the circular flow involving the alerted transaction and intermediary accounts.
19. [INV-1-S19] `write_pattern` — Grouping the circular flow evidence into a pattern.
20. [INV-1-S20] `finish` *(rejected/error)* — Summarizing findings and concluding the investigation.
21. [INV-1-S21] `write_evidence` — Recording the shared device evidence.
22. [INV-1-S22] `write_evidence` — Recording the shared device evidence.
23. [INV-1-S23] `analyze_fanout` — To check for unusual recipient bursts or tightly clustered amounts.
24. [INV-1-S24] `analyze_fanout` — To check for unusual recipient bursts or tightly clustered amounts.
25. [INV-1-S25] `write_evidence` — Recording the shared device evidence.
26. [INV-1-S26] `write_evidence` — Recording the shared device evidence.
27. [INV-1-S27] `write_pattern` — Grouping the shared device evidence into a pattern.
28. [INV-1-S28] `write_pattern` — Grouping the shared device evidence into a pattern.
29. [INV-1-S29] `finish` *(rejected/error)* — Completing the investigation and concluding with findings.
30. [INV-1-S30] `finish` *(rejected/error)* — Completing the investigation and concluding with findings.
31. [INV-1-S31] `finish` — Completing the investigation and concluding with findings.

---
**A human analyst makes the final decision.** This report is decision support generated from graph evidence; it does not constitute a determination of wrongdoing.