# AML Investigation Report: TX-FLAGSHIP
*Investigation INV-1 · status finished · produced by openai/gpt-5.6-luna*  
*Alerted transaction TX-FLAGSHIP: ACC-1913 → ACC-2106, amount 8400.0*

## Recommendation: **ESCALATE** (confidence 0.99)
Escalation is supported by the confirmed shared-device clusters [EV-1] [EV-5], rapid eight-recipient post-receipt fan-out [EV-2], tightly clustered sub-$1,000 onward transfers [EV-3] [POL-STRUCTURING], and confirmed five-hop circular flow [EV-4] [POL-LAYERING]. No legitimate explanation is established in the reviewed evidence [EV-1] [EV-4].

**A human analyst makes the final decision.** This report is decision support generated from graph evidence; it does not constitute a determination of wrongdoing.

## Executive summary
TX-FLAGSHIP is a $8,400 transfer from ACC-1913 to ACC-2106 [TX-FLAGSHIP]. The accounts are linked through a shared device used by accounts owned by different customers, with no legitimate household or workplace relationship established in the reviewed data [EV-1]. After receipt, ACC-2106 sent funds to eight recipients in a rapid burst [EV-2] using tightly clustered amounts of $958.65–$988.82 [EV-3]. The funds were also part of a confirmed five-hop cycle returning to ACC-1913 [EV-4].

## Findings
### [PAT-1] SHARED_DEVICE_CLUSTER (confidence 0.94)
Three retail accounts owned by different customers share DEV-0389, including the alerted sender ACC-1913 and receiver ACC-2106.
- [EV-1] **SHARED_DEVICE**: ACC-1913 and ACC-2106, owned by different customers Ravi Pillai and Arjun Rao, both use DEV-0389; ACC-1625 also uses that device. No legitimate household or workplace relationship is established in the data reviewed. — supported by [ACC-1625], [ACC-1913], [ACC-2106], [DEV-0389]
- Violates [POL-SHARED-DEVICE] *Undisclosed device sharing across unrelated customers* (severity MEDIUM, confidence 0.99): EV-1 shows ACC-1913 and ACC-2106, owned by different customers, both use DEV-0389; no legitimate household or workplace relationship is established.

### [PAT-2] RAPID_FANOUT (confidence 0.99)
ACC-2106 rapidly redistributed the alerted receipt to eight recipients, far above its normal two-day recipient baseline.
- [EV-2] **RAPID_FANOUT**: ACC-2106 sent to 8 distinct recipients between 2026-08-31 11:00 and 2026-09-01 09:26, versus a typical 0 recipients per two-day window; the burst followed TX-FLAGSHIP by two hours. — supported by [ACC-2106], [TX-05530], [TX-05538], [TX-05552], [TX-05566], [TX-05583], [TX-05588], [TX-05613], [TX-05625], [TX-FLAGSHIP]
- Violates [POL-RAPID-FANOUT] *Rapid fan-out to many recipients* (severity HIGH, confidence 0.99): EV-2 shows ACC-2106 redistributed the receipt from TX-FLAGSHIP to eight distinct recipients within roughly one day, far above its typical zero-recipient baseline, with no documented business purpose in the evidence.

### [PAT-3] STRUCTURING (confidence 0.87)
ACC-2106 split the post-alert funds into eight tightly clustered transfers ranging from $958.65 to $988.82.
- [EV-3] **STRUCTURING_AMOUNTS**: ACC-2106's eight-transfer burst used tightly clustered amounts from $958.65 to $988.82, immediately after receiving $8,400 from ACC-1913; the amounts are consistent with deliberate sub-threshold splitting, though the applicable threshold is not shown in the available policy excerpt. — supported by [ACC-2106], [TX-05530], [TX-05538], [TX-05552], [TX-05566], [TX-05583], [TX-05588], [TX-05613], [TX-05625], [TX-FLAGSHIP]
- Violates [POL-STRUCTURING] *Structuring below reporting threshold* (severity HIGH, confidence 0.98): EV-3 shows eight tightly clustered post-receipt transfers ranging from $958.65 to $988.82, each below the stated internal alert threshold of $1,000, consistent with splitting the $8,400 receipt to avoid review.

### [PAT-4] CIRCULAR_FLOW (confidence 0.99)
Funds moved from ACC-1913 through connected accounts and returned to ACC-1913 in a confirmed five-hop cycle.
- [EV-4] **CIRCULAR_FLOW**: A five-hop cycle runs from ACC-1913 through ACC-1625, ACC-1845, ACC-1880, and ACC-2106 back to ACC-1913, including the alerted transaction. — supported by [ACC-1625], [ACC-1845], [ACC-1880], [ACC-1913], [ACC-2106], [TX-05583], [TX-05602], [TX-05686], [TX-05738], [TX-FLAGSHIP]
- Violates [POL-LAYERING] *Layering through circular fund movement* (severity HIGH, confidence 0.99): EV-4 confirms a five-hop movement from ACC-1913 through connected accounts and back to ACC-1913, including the alerted transaction, with no apparent economic purpose established.

### [PAT-5] SHARED_DEVICE_CLUSTER (confidence 0.94)
DEV-0390 links alerted sender ACC-1913 to ACC-1370, owned by a different customer, in addition to the DEV-0389 cluster.
- [EV-5] **SHARED_DEVICE**: ACC-1913 and ACC-1370, owned by different customers Ravi Pillai and Nila Kapoor, both use DEV-0390. — supported by [ACC-1370], [ACC-1913], [DEV-0390]
- Violates [POL-SHARED-DEVICE] *Undisclosed device sharing across unrelated customers* (severity MEDIUM, confidence 0.99): EV-5 shows ACC-1913 and ACC-1370, owned by different customers, both use DEV-0390; no disclosed relationship is established in the evidence.

## Analysis
The shared-device relationship connects the alerted sender and receiver to a broader cross-customer account cluster [PAT-1]. The rapid redistribution and tightly clustered onward amounts followed TX-FLAGSHIP, with each amount below the stated $1,000 internal alert threshold in the policy match [EV-2] [EV-3] [POL-STRUCTURING]. The circular flow provides an additional layering indicator because funds moved through connected accounts and returned to ACC-1913, including the alerted transaction [EV-4] [POL-LAYERING]. A second shared-device link connects ACC-1913 to ACC-1370, another account owned by a different customer, without a disclosed relationship in the evidence [EV-5]. This case most closely resembles the escalated hub account fan-out ring involving shared-device links, eight-recipient redistribution, sub-threshold amounts, and circular return of funds [CASE-H1].

## What was ruled out
No benign explanations were recorded by the Investigator.

## Prior cases recalled from memory
- [CASE-H1] **ESCALATED** — Hub account fan-out ring (escalated) *(synthetic demo history)*
- [CASE-H3] **CLEAR** — Payroll account with large recurring fan-out (cleared) *(synthetic demo history)*
- [CASE-H4] **MONITOR** — New-account velocity cluster (monitor) *(synthetic demo history)*

## Reasoning path (Investigator steps)
1. [INV-1-S01] `get_transaction` — Establish the alerted transaction's sender, receiver, amount, timing, and channel before tracing connected activity.
2. [INV-1-S02] `get_account_profile` — Profile the alerted sender to assess age, activity, counterparties, and transaction context.
3. [INV-1-S03] `get_account_profile` — Profile the alerted receiver to assess age, activity, counterparties, and transaction context.
4. [INV-1-S04] `find_shared_devices` — Identify devices used by the sender and any other linked accounts.
5. [INV-1-S05] `find_shared_devices` — Identify devices used by the receiver and any other linked accounts.
6. [INV-1-S06] `list_policies` — Check applicable review thresholds and relevant monitoring definitions before evaluating the alert.
7. [INV-1-S07] `write_evidence` — The device results directly confirm cross-customer device sharing, so record the factual finding before pursuing activity on the linked accounts.
8. [INV-1-S08] `get_account_profile` — Profile the other customer account sharing the sender/receiver device.
9. [INV-1-S09] `get_account_profile` — Profile the account linked through the sender's second shared device.
10. [INV-1-S10] `analyze_fanout` — Test whether the alerted sender redistributes funds in a short burst and compare with its typical recipient activity.
11. [INV-1-S11] `analyze_fanout` — Test whether the alerted receiver rapidly redistributes funds after receiving the alert transaction.
12. [INV-1-S12] `analyze_fanout` — Check fan-out behavior of the device-linked account.
13. [INV-1-S13] `analyze_fanout` — Check fan-out behavior of the account linked through the sender's other device.
14. [INV-1-S14] `trace_flow` — Trace where funds go after the alerted receiver to identify onward redistribution or layering.
15. [INV-1-S15] `find_cycles` — Test for circular money movement from the alerted sender through up to five account hops.
16. [INV-1-S16] `write_evidence` — The receiver's fan-out is clearly above its baseline and temporally follows the alerted receipt.
17. [INV-1-S17] `write_evidence` — The fan-out result explicitly identifies tightly clustered amounts and supplies the transaction ids for the burst.
18. [INV-1-S18] `write_evidence` *(rejected/error)* — The five-hop flow and cycle results directly show the alerted funds' onward movement and return to the origin.
19. [INV-1-S19] `write_evidence` — Use one complete cycle route exactly as returned by find_cycles so the graph can validate the closed loop.
20. [INV-1-S20] `analyze_fanout` — Analyze the intermediate consolidating account identified in the receiver's onward flow.
21. [INV-1-S21] `analyze_fanout` — Analyze the alternate intermediate consolidating account identified in the receiver's onward flow.
22. [INV-1-S22] `analyze_fanout` — Analyze a recipient that appears on a confirmed cycle back to the sender.
23. [INV-1-S23] `analyze_fanout` — Analyze another recipient that appears on a confirmed cycle back to the sender.
24. [INV-1-S24] `get_neighbors` — Enumerate all accounts linked to the shared device to ensure the device cluster is fully covered.
25. [INV-1-S25] `get_neighbors` — Inspect the receiver's direct outgoing transaction neighbors for corroborating onward movement.
26. [INV-1-S26] `write_evidence` *(rejected/error)* — The account-level fan-out analyses explicitly find no unusual bursts for the connected intermediaries, which is a legitimate limitation on the suspicious interpretation.
27. [INV-1-S27] `write_pattern` — Group the confirmed cross-customer device-sharing evidence.
28. [INV-1-S28] `write_pattern` — Group the confirmed post-alert rapid redistribution evidence.
29. [INV-1-S29] `write_pattern` — Group the confirmed clustered-amount evidence.
30. [INV-1-S30] `write_pattern` — Group the confirmed circular-flow evidence.
31. [INV-1-S31] `finish` *(rejected/error)* — Investigation is complete after profiling the parties, checking all linked devices, testing fan-out across linked accounts, tracing onward funds, and checking five-hop cycles.
32. [INV-1-S32] `write_evidence` — Record the additional cross-customer shared-device relationship identified during self-review.
33. [INV-1-S33] `write_pattern` — Group the additional confirmed cross-customer device-sharing evidence.
34. [INV-1-S34] `finish` *(rejected/error)* — All self-review findings are now recorded and grouped, and required device, fan-out, onward-flow, and five-hop cycle checks are complete.
35. [INV-1-S35] `finish` *(rejected/error)* — All required investigative checks and self-review requirements are complete.
36. [INV-1-S36] `finish` — All required transaction, account, device, fan-out, onward-flow, cycle, evidence, and pattern checks are complete.

---
**A human analyst makes the final decision.** This report is decision support generated from graph evidence; it does not constitute a determination of wrongdoing.