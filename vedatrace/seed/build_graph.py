"""Deterministic AML seed. Writes ONLY raw facts: Customer, Account, Device, Transaction, Policy.

Ground truth (which structures were planted) goes to seed/ground_truth.json and is read by tests only.
Planted accounts get ordinary-looking ids drawn from the same pool as everyone else.
"""
import json
import math
import os
import random
from datetime import datetime, timezone

from config import get_graph

BASE_TS = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
DAY = 86400
HOUR = 3600
SPAN_DAYS = 90
GROUND_TRUTH_PATH = os.path.join(os.path.dirname(__file__), "ground_truth.json")

SIZES = {
    # normal_accounts excludes planted/role accounts; tx_normal is background noise count
    "dev": dict(normal_accounts=36, merchants=4, tx_normal=50, households=2, offices=1,
                office_size=4, payroll_employees=8, marketplace_sellers=5, rent_pairs=1,
                solo_device_rate=0.35, near_threshold_noise=3, decoy_alerts=2),
    "demo": dict(normal_accounts=1450, merchants=40, tx_normal=7200, households=30, offices=6,
                 office_size=5, payroll_employees=60, marketplace_sellers=18, rent_pairs=8,
                 solo_device_rate=0.25, near_threshold_noise=60, decoy_alerts=8),
}

FIRST = ["Aarav", "Meera", "Rohan", "Isha", "Kabir", "Anaya", "Vihaan", "Diya", "Arjun", "Sara",
         "Neel", "Tara", "Dev", "Riya", "Kian", "Mira", "Ishan", "Zoya", "Yash", "Nila",
         "Omar", "Leena", "Sam", "Priya", "Tariq", "Hana", "Ravi", "Elena", "Jonas", "Amara"]
LAST = ["Sharma", "Iyer", "Khan", "Das", "Patel", "Nair", "Singh", "Rao", "Menon", "Gupta",
        "Bose", "Mehta", "Reddy", "Joshi", "Verma", "Kapoor", "Shah", "Pillai", "Ghosh", "Malik"]
BIZ = ["Trading", "Foods", "Logistics", "Studio", "Supplies", "Traders", "Repairs", "Cafe", "Textiles", "Pharma"]
CHANNELS = ["transfer", "transfer", "transfer", "card", "bank_wire"]
THRESHOLD = 1000.0

POLICIES = [
    dict(id="POL-STRUCTURING", title="Structuring below reporting threshold",
         description="Splitting a larger sum into several transfers each just below the internal alert threshold of 1,000 in order to avoid review. Look for clusters of similar just-under-threshold amounts in a short window.",
         severity="HIGH", regulatory_ref="Internal AML Policy s.4.2 (illustrative)"),
    dict(id="POL-SHARED-DEVICE", title="Undisclosed device sharing across unrelated customers",
         description="Accounts owned by different, apparently unrelated customers operated from the same device without a disclosed relationship (household, workplace). Independently weak; strong when combined with other indicators.",
         severity="MEDIUM", regulatory_ref="Internal AML Policy s.5.1 (illustrative)"),
    dict(id="POL-LAYERING", title="Layering through circular fund movement",
         description="Funds moved through a chain of accounts and returned to the originator or their associated device cluster with no apparent economic purpose.",
         severity="HIGH", regulatory_ref="Internal AML Policy s.4.4 (illustrative)"),
    dict(id="POL-RAPID-FANOUT", title="Rapid fan-out to many recipients",
         description="An account receiving funds and redistributing them to many distinct recipients within a short window, outside a documented business purpose such as payroll or marketplace payouts.",
         severity="HIGH", regulatory_ref="Internal AML Policy s.4.3 (illustrative)"),
    dict(id="POL-NEW-ACCOUNT-VELOCITY", title="High-velocity activity on newly opened accounts",
         description="Accounts opened shortly before the activity that receive and forward funds rapidly.",
         severity="MEDIUM", regulatory_ref="Internal AML Policy s.6.1 (illustrative)"),
    dict(id="POL-LARGE-CASH", title="Large cash transaction reporting",
         description="Cash deposits or withdrawals above the cash reporting limit must be reported.",
         severity="MEDIUM", regulatory_ref="Internal AML Policy s.3.1 (illustrative)"),
    dict(id="POL-THIRD-PARTY-FUNDING", title="Unexplained third-party funding",
         description="Account funded repeatedly by unrelated third parties with no documented relationship.",
         severity="LOW", regulatory_ref="Internal AML Policy s.7.2 (illustrative)"),
]


class Builder:
    def __init__(self, size: str, seed: int = 42):
        self.p = SIZES[size]
        self.rng = random.Random(seed)
        self.customers, self.accounts, self.devices, self.txs = [], [], [], []
        self.used_device_edges = []  # (account_id, device_id)
        self.acc_by_id = {}
        self.truth = {}
        self._cust_n = 0
        self._dev_n = 0

    # ---- primitives -------------------------------------------------
    def new_customer(self, segment="retail", name=None):
        r = self.rng
        self._cust_n += 1
        cid = f"CUST-{self._cust_n:04d}"
        if name is None:
            name = f"{r.choice(FIRST)} {r.choice(LAST)}" if segment == "retail" else f"{r.choice(LAST)} {r.choice(BIZ)}"
        self.customers.append(dict(id=cid, name=name, segment=segment))
        return cid

    def new_account(self, cust, atype="checking", opened_days_before=None):
        r = self.rng
        n = len(self.accounts) + 1
        aid = f"ACC-{n:04d}"  # placeholder; renumbered by shuffle in finalize()
        if opened_days_before is None:
            opened_days_before = r.randint(120, 2400)
        acc = dict(id=aid, customer=cust, type=atype, opened_ts=BASE_TS - opened_days_before * DAY, status="active")
        self.accounts.append(acc)
        self.acc_by_id[aid] = acc
        return aid

    def new_device(self, kind=None):
        self._dev_n += 1
        did = f"DEV-{self._dev_n:04d}"
        self.devices.append(dict(id=did, kind=kind or self.rng.choice(["mobile", "mobile", "mobile", "desktop", "tablet"])))
        return did

    def link(self, acc, dev):
        self.used_device_edges.append((acc, dev))

    def tx(self, src, dst, amount, ts, channel=None, score=None, alerted=False):
        r = self.rng
        self.txs.append(dict(src=src, dst=dst, amount=round(amount, 2), ts=int(ts),
                             channel=channel or r.choice(CHANNELS),
                             monitor_score=round(score if score is not None else min(0.45, r.betavariate(1.2, 12)), 3),
                             alerted=alerted))
        return len(self.txs) - 1

    def rand_ts(self, lo_day=0, hi_day=SPAN_DAYS):
        return BASE_TS + self.rng.uniform(lo_day, hi_day) * DAY

    def normal_amount(self):
        return min(max(self.rng.lognormvariate(math.log(90), 1.0), 5), 960)

    # ---- build stages -----------------------------------------------
    def build(self):
        r, p = self.rng, self.p
        t = self.truth

        # Background population
        normal = []
        n_cust_target = p["normal_accounts"]
        while len(normal) < n_cust_target:
            c = self.new_customer()
            for _ in range(2 if r.random() < 0.15 else 1):
                if len(normal) >= n_cust_target:
                    break
                normal.append(self.new_account(c, r.choice(["checking", "checking", "savings"])))
        merchants = []
        for _ in range(p["merchants"]):
            c = self.new_customer("business")
            merchants.append(self.new_account(c, "business"))
        # solo devices for some normal accounts (digital channel users)
        for a in normal:
            if r.random() < p["solo_device_rate"]:
                self.link(a, self.new_device())
        pool = list(normal)
        r.shuffle(pool)

        def take(n):
            out, rest = pool[:n], pool[n:]
            pool[:] = rest
            return out

        # --- red herring: payroll (high fan-out, legit) ---
        payroll_acc = self.new_account(self.new_customer("business"), "business", 900)
        employees = take(p["payroll_employees"])
        for m in range(3):
            day = 3 + 29 * m  # monthly runs around the same day
            for e in employees:
                self.tx(payroll_acc, e, r.uniform(1800, 5200), BASE_TS + day * DAY + r.uniform(8, 11) * HOUR,
                        channel="bank_wire")
        # funding of payroll account from a few corporate clients
        for _ in range(8):
            self.tx(r.choice(merchants), payroll_acc, r.uniform(40000, 90000), self.rand_ts(0, 85), channel="bank_wire")
        t["payroll_account"] = payroll_acc
        t["payroll_employees"] = employees

        # --- red herring: marketplace payouts (fan-out, varied amounts, weekly) ---
        market_acc = self.new_account(self.new_customer("business"), "business", 700)
        sellers = take(p["marketplace_sellers"])
        for wk in range(12):
            for s in sellers:
                if r.random() < 0.7:
                    self.tx(market_acc, s, r.uniform(120, 1700), BASE_TS + (wk * 7 + 4) * DAY + r.uniform(0, 6) * HOUR)
        for _ in range(60 if self.p["tx_normal"] > 1000 else 15):
            self.tx(r.choice(normal), market_acc, self.normal_amount() * 2, self.rand_ts(), channel="card")
        t["marketplace_account"] = market_acc
        t["marketplace_sellers"] = sellers

        # --- red herring: benign cycles ---
        rent_pairs = []
        for _ in range(p["rent_pairs"]):
            tenant, landlord = take(2)
            for m in range(3):
                self.tx(tenant, landlord, 1500, BASE_TS + (2 + 30 * m) * DAY + r.uniform(0, 5) * HOUR)
            self.tx(landlord, tenant, 1500, BASE_TS + 71 * DAY + r.uniform(0, 8) * HOUR)  # deposit refund
            rent_pairs.append([tenant, landlord])
        a, b, c = take(3)
        for s, d, day in ((a, b, 20), (b, c, 21), (c, a, 23)):
            self.tx(s, d, 62.5, BASE_TS + day * DAY + r.uniform(0, 12) * HOUR)  # bill-split loop
        t["benign_rent_cycles"] = rent_pairs
        t["benign_split_cycle"] = [a, b, c]

        # --- red herring: legit shared devices (households, offices) ---
        hh_devs, office_devs = [], []
        for _ in range(p["households"]):
            members = take(r.choice([2, 3]))
            d = self.new_device(r.choice(["tablet", "desktop"]))
            for m in members:
                self.link(m, d)
            for _ in range(r.randint(2, 5)):  # family transfers
                s, dd = r.sample(members, 2)
                self.tx(s, dd, r.uniform(100, 600), self.rand_ts())
            hh_devs.append(dict(device=d, accounts=members))
        for _ in range(p["offices"]):
            members = take(p["office_size"])
            d = self.new_device("desktop")
            for m in members:
                self.link(m, d)
            office_devs.append(dict(device=d, accounts=members))
        t["benign_household_devices"] = hh_devs
        t["benign_office_devices"] = office_devs

        # --- PLANTED RING ---
        s_cust, h_cust, k_cust = (self.new_customer() for _ in range(3))
        S = self.new_account(s_cust, "checking", 1400)
        H = self.new_account(h_cust, "checking", 520)
        K = self.new_account(k_cust, "checking", 610)
        D_shared = self.new_device("mobile")
        for acc in (S, H, K):
            self.link(acc, D_shared)
        S_home = self.new_device("desktop")
        self.link(S, S_home)  # S also has a personal home device, shared with a household member (legitimate)
        spouse = self.new_account(self.new_customer(), "savings", 1300)
        self.link(spouse, S_home)
        for _ in range(5):
            self.tx(S, spouse, r.uniform(150, 700), self.rand_ts(0, 55))
            self.tx(spouse, S, r.uniform(50, 300), self.rand_ts(0, 55))
        mules = [self.new_account(self.new_customer(), "checking", r.randint(9, 26)) for _ in range(8)]
        inter = [self.new_account(self.new_customer(), "checking", r.randint(200, 700)) for _ in range(2)]
        for m in mules:
            if r.random() < 0.5:
                self.link(m, self.new_device())
        # S has an ordinary history
        for _ in range(14 if p["tx_normal"] < 1000 else 55):
            self.tx(S, r.choice(merchants), self.normal_amount(), self.rand_ts(0, 59))
        t0 = BASE_TS + 61 * DAY + 9 * HOUR
        flagship_idx = self.tx(S, H, 8400.00, t0, channel="transfer", score=0.54, alerted=True)
        fan_start = t0 + 2 * HOUR
        mule_in = []
        for i, m in enumerate(mules):
            ts = fan_start + i * r.uniform(2.5, 3.8) * HOUR
            mule_in.append(ts)
            self.tx(H, m, r.uniform(955, 995), ts, channel="transfer")
        for i, m in enumerate(mules):
            self.tx(m, inter[i % 2], r.uniform(900, 940), mule_in[i] + r.uniform(3, 7) * HOUR)
        for i_acc in inter:
            self.tx(i_acc, K, r.uniform(3300, 3800), fan_start + 36 * HOUR + r.uniform(0, 5) * HOUR, channel="bank_wire")
        self.tx(K, S, r.uniform(7000, 7400), fan_start + 52 * HOUR, channel="bank_wire")  # closes the loop
        # ordinary background activity for the ring members so they are not isolated
        for acc in [H, K] + inter:
            for _ in range(5 if p["tx_normal"] < 1000 else 20):
                self.tx(r.choice(normal), acc, self.normal_amount(), self.rand_ts(0, 85))
        for m in mules:
            for _ in range(2):
                self.tx(r.choice(normal), m, self.normal_amount(), self.rand_ts(55, 61))
        # K is also an employee of the payroll account: the ring neighbourhood touches a legitimate high-fan-out payer
        for m in range(3):
            self.tx(payroll_acc, K, r.uniform(1800, 5200), BASE_TS + (3 + 29 * m) * DAY + r.uniform(8, 11) * HOUR, channel="bank_wire")
        t.update(benign_household_near_ring=dict(device=S_home, accounts=[S, spouse]), payroll_employee_in_ring=K)
        t.update(flagship_tx="TX-FLAGSHIP", sender=S, hub=H, device_cluster_member=K,
                 shared_device=D_shared, mules=mules, intermediaries=inter)

        # --- background noise ---
        senders = normal + merchants
        for _ in range(p["tx_normal"]):
            s = r.choice(normal)
            d = r.choice(merchants) if r.random() < 0.45 else r.choice(senders)
            if d == s:
                continue
            self.tx(s, d, self.normal_amount(), self.rand_ts())
        for _ in range(p["near_threshold_noise"]):  # unrelated just-under-threshold transfers
            s, d = r.sample(normal, 2)
            self.tx(s, d, r.uniform(900, 999), self.rand_ts())
        for _ in range(p["decoy_alerts"]):
            s, d = r.sample(normal, 2)
            self.tx(s, d, r.uniform(300, 900), self.rand_ts(), score=r.uniform(0.5, 0.62), alerted=True)

        self._finalize(flagship_idx)

    def _finalize(self, flagship_idx):
        """Shuffle account ids so ring members are indistinguishable by id order; assign tx ids by time."""
        r = self.rng
        old_ids = [a["id"] for a in self.accounts]
        new_ids = [f"ACC-{1000 + i}" for i in range(len(old_ids))]
        r.shuffle(new_ids)
        remap = dict(zip(old_ids, new_ids))

        def fix(x):
            if isinstance(x, str):
                return remap.get(x, x)
            if isinstance(x, list):
                return [fix(i) for i in x]
            if isinstance(x, dict):
                return {k: fix(v) for k, v in x.items()}
            return x

        for a in self.accounts:
            a["id"] = remap[a["id"]]
        self.used_device_edges = [(remap[a], d) for a, d in self.used_device_edges]
        self.truth = fix(self.truth)
        order = sorted(range(len(self.txs)), key=lambda i: (self.txs[i]["ts"], i))
        for rank, i in enumerate(order, start=1):
            tx = self.txs[i]
            tx["src"], tx["dst"] = remap[tx["src"]], remap[tx["dst"]]
            tx["id"] = "TX-FLAGSHIP" if i == flagship_idx else f"TX-{rank:05d}"
        self.accounts.sort(key=lambda a: a["id"])
        self.txs.sort(key=lambda t: t["id"])


# ---- graph loading -------------------------------------------------------
def _batches(rows, n=1000):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def load(builder: Builder, graph):
    for label in ("Customer", "Account", "Device", "Transaction", "Policy"):
        graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")
    for b in _batches(builder.customers):
        graph.query("UNWIND $rows AS r CREATE (:Customer {id:r.id, name:r.name, segment:r.segment})", params={"rows": b})
    for b in _batches(builder.accounts):
        graph.query("UNWIND $rows AS r CREATE (:Account {id:r.id, type:r.type, opened_ts:r.opened_ts, status:r.status})",
                    params={"rows": b})
    for b in _batches(builder.accounts):
        graph.query("UNWIND $rows AS r MATCH (c:Customer {id:r.customer}), (a:Account {id:r.id}) CREATE (c)-[:OWNS]->(a)",
                    params={"rows": b})
    for b in _batches(builder.devices):
        graph.query("UNWIND $rows AS r CREATE (:Device {id:r.id, kind:r.kind})", params={"rows": b})
    rows = [dict(a=a, d=d) for a, d in builder.used_device_edges]
    for b in _batches(rows):
        graph.query("UNWIND $rows AS r MATCH (a:Account {id:r.a}), (d:Device {id:r.d}) CREATE (a)-[:USED_DEVICE]->(d)",
                    params={"rows": b})
    for b in _batches(builder.txs, 500):
        graph.query(
            "UNWIND $rows AS r MATCH (a:Account {id:r.src}), (b:Account {id:r.dst}) "
            "CREATE (a)-[:SENT]->(:Transaction {id:r.id, amount:r.amount, ts:r.ts, channel:r.channel, "
            "currency:'USD', monitor_score:r.monitor_score, alerted:r.alerted})-[:TO]->(b)",
            params={"rows": b})
    graph.query("UNWIND $rows AS r CREATE (:Policy {id:r.id, title:r.title, description:r.description, "
                "severity:r.severity, regulatory_ref:r.regulatory_ref})", params={"rows": POLICIES})


def build(size: str = "dev", graph=None, write_truth: bool = True) -> dict:
    b = Builder(size)
    b.build()
    g = graph if graph is not None else get_graph()
    load(b, g)
    if write_truth:
        with open(GROUND_TRUTH_PATH, "w") as f:
            json.dump(dict(size=size, **b.truth), f, indent=2, sort_keys=True)
    return dict(customers=len(b.customers), accounts=len(b.accounts), devices=len(b.devices),
                transactions=len(b.txs), policies=len(POLICIES))
