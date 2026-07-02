"""Campaign results: mode-aware agreement / correlation / descriptive statistics.

Pure-Python + numpy (no scipy). All public entry points return JSON-serialisable
dicts so they can be cached on the campaign, rendered in templates, and exported.

Metrics by evaluation mode:
  likert     -> per-criterion descriptives, Krippendorff's alpha (interval),
                mean pairwise Pearson/Spearman, annotator correlation matrix,
                per-system mean scores.
  pairwise   -> preference distribution, Fleiss'/Cohen's kappa + % agreement,
                A/tie/B outcome rates, per-system win rates.
  span_only  -> per error-type span counts/density, pairwise span F1 + kappa.
"""

from __future__ import annotations
import math
import re
import numpy as np


# --------------------------------------------------------------------------
# small statistics helpers
# --------------------------------------------------------------------------

def _r(x, nd=3):
    """Round + make JSON-safe (None for nan/inf)."""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf) or math.isinf(xf):
        return None
    return round(xf, nd)


def pearson(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _rankdata(x):
    """Average ranks (ties get the mean rank), like scipy.stats.rankdata."""
    x = np.asarray(x, dtype=float)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1)
    # average ties
    _, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inv, ranks)
    avg = sums / counts
    return avg[inv]


def spearman(a, b):
    if len(a) < 2:
        return None
    return pearson(_rankdata(a), _rankdata(b))


def krippendorff_alpha_interval(matrix):
    """Krippendorff's alpha (interval metric).

    matrix: list of rows (one per unit/item), each a list of coder values with
    None for missing. Returns alpha in (-inf, 1], or None if undefined.
    """
    # Keep only units rated by >= 2 coders.
    units = []
    for row in matrix:
        vals = [float(v) for v in row if v is not None]
        if len(vals) >= 2:
            units.append(vals)
    if not units:
        return None

    # Coincidence counts over value pairs.
    from collections import defaultdict
    o = defaultdict(float)          # o[(c,k)] coincidences
    marginal = defaultdict(float)   # n_c
    n = 0.0
    for vals in units:
        m = len(vals)
        for i in range(m):
            for j in range(m):
                if i != j:
                    o[(vals[i], vals[j])] += 1.0 / (m - 1)
        for v in vals:
            marginal[v] += 1.0
            n += 1.0
    if n < 2:
        return None

    def delta(c, k):
        return (c - k) ** 2

    Do = 0.0
    for (c, k), w in o.items():
        Do += w * delta(c, k)
    Do = Do / n

    vals_list = list(marginal.keys())
    De = 0.0
    for c in vals_list:
        for k in vals_list:
            De += marginal[c] * marginal[k] * delta(c, k)
    De = De / (n * (n - 1))

    if De == 0:
        return 1.0 if Do == 0 else None
    return 1.0 - Do / De


def cohen_kappa(labels_a, labels_b):
    """Cohen's kappa for two raters over paired categorical labels."""
    if len(labels_a) == 0:
        return None
    cats = sorted(set(labels_a) | set(labels_b))
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    if k < 2:
        return 1.0  # everyone always picks the same single category
    conf = np.zeros((k, k))
    for x, y in zip(labels_a, labels_b):
        conf[idx[x], idx[y]] += 1
    n = conf.sum()
    if n == 0:
        return None
    po = np.trace(conf) / n
    pe = (conf.sum(axis=0) @ conf.sum(axis=1)) / (n * n)
    if pe == 1:
        return 1.0 if po == 1 else None
    return float((po - pe) / (1 - pe))


def fleiss_kappa(rows):
    """Fleiss' kappa. rows: list of per-item category-count vectors (same length).
    Items must have the same number of ratings; items with !=mode raters are dropped.
    """
    rows = [np.asarray(r, dtype=float) for r in rows if sum(r) > 0]
    if not rows:
        return None
    totals = [int(r.sum()) for r in rows]
    # use only items with the modal number of raters (Fleiss requires fixed n)
    from collections import Counter
    if not totals:
        return None
    common_n = Counter(totals).most_common(1)[0][0]
    rows = [r for r, t in zip(rows, totals) if t == common_n]
    if len(rows) < 2 or common_n < 2:
        return None
    mat = np.vstack(rows)
    N, k = mat.shape
    nn = common_n
    p = mat.sum(axis=0) / (N * nn)
    P = (np.square(mat).sum(axis=1) - nn) / (nn * (nn - 1))
    Pbar = P.mean()
    Pe = float(np.square(p).sum())
    if Pe == 1:
        return 1.0 if Pbar == 1 else None
    return float((Pbar - Pe) / (1 - Pe))


# --------------------------------------------------------------------------
# shared extraction
# --------------------------------------------------------------------------

def _annotators(ratings):
    seen = {}
    for r in ratings:
        if r.annotator_id not in seen:
            ann = r.annotator
            seen[r.annotator_id] = {
                "id": r.annotator_id,
                "name": ann.name if ann else r.annotator_id,
                "email": ann.email if ann else "",
                "expertise": (getattr(ann, "expertise", "") or "") if ann else "",
            }
    return list(seen.values())


def _overlap_segments(ratings, complete_fn):
    """seg_id -> number of annotators with a COMPLETE rating for it."""
    from collections import defaultdict
    cnt = defaultdict(int)
    for r in ratings:
        if complete_fn(r):
            cnt[r.segment_id] += 1
    return cnt


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------

def compute_results(campaign, ratings, criteria):
    """Return a JSON-serialisable results dict for a campaign."""
    mode = campaign.mode
    complete = [r for r in ratings if campaign.rating_is_complete(r, criteria)]
    annotators = _annotators(complete)
    overlap = _overlap_segments(complete, lambda r: True)
    n_overlap = sum(1 for v in overlap.values() if v >= 2)

    per_ann_counts = {}
    for r in complete:
        per_ann_counts[r.annotator_id] = per_ann_counts.get(r.annotator_id, 0) + 1
    for a in annotators:
        a["n_complete"] = per_ann_counts.get(a["id"], 0)
        from models import CampaignAnnotator
        link = CampaignAnnotator.query.filter_by(campaign_id=campaign.id, annotator_id=a["id"]).first()
        a["has_star"] = bool(link and link.has_star)
        a["quality_score"] = float(link.quality_score if link else 100.0)

    warnings = []
    iaa_ok = len(annotators) >= 2 and n_overlap >= 1
    if len(annotators) < 2:
        warnings.append("Inter-annotator agreement needs at least two annotators; "
                        "only %d has submitted complete ratings." % len(annotators))
    elif n_overlap == 0:
        warnings.append("No segment has been completed by two or more annotators yet, "
                        "so agreement metrics cannot be computed.")
    if 0 < n_overlap < 15:
        warnings.append("Only %d segments have overlapping ratings; agreement metrics "
                        "(kappa / alpha / F1) are unstable on small samples." % n_overlap)

    out = {
        "mode": mode,
        "summary": {
            "n_segments": campaign.num_segments,
            "n_annotators": len(annotators),
            "n_complete_ratings": len(complete),
            "n_overlap_segments": n_overlap,
            "annotators": annotators,
            "warnings": warnings,
            "computable_iaa": iaa_ok,
        },
    }

    if mode == "likert":
        out["likert"] = _likert_stats(campaign, complete, criteria, annotators)
        # Likert campaigns can also collect error spans; summarise them too.
        if getattr(campaign, "enable_spans", False):
            sp = _span_stats(campaign, complete, criteria, annotators)
            if any(c["n_spans"] for c in sp["criteria"]):
                out["likert"]["spans"] = sp
    elif mode == "pairwise":
        out["pairwise"] = _pairwise_stats(campaign, complete, annotators)
    elif mode == "span_only":
        out["span_only"] = _span_stats(campaign, complete, criteria, annotators)
    elif mode == "post_edit":
        out["post_edit"] = _post_edit_stats(campaign, complete, annotators)

    diff = _difficulty_breakdown(campaign, complete, criteria)
    if diff:
        out["difficulty"] = diff

    out["performance"] = _performance(campaign, complete, criteria, mode)
    dis = _disagreement(campaign, complete, criteria, mode)
    if dis and dis["contested_segments"]:
        out["disagreement"] = dis
    return out


def _disagreement(campaign, complete, criteria, mode):
    """Where annotators disagree: per-criterion spread + the most-contested segments.

    likert    -> std of scores across annotators (per criterion, averaged)
    pairwise  -> fraction of votes not with the majority preference
    span_only -> 1 - mean pairwise character F1 across error types
    post_edit -> std of normalised edit distances
    """
    from collections import defaultdict, Counter
    by_seg = defaultdict(list)
    for r in complete:
        by_seg[r.segment_id].append(r)
    seg_lookup = {s.get("id"): s for s in campaign.segments}
    contested = []
    crit_disagree = defaultdict(list)

    for seg_id, rs in by_seg.items():
        if len(rs) < 2:
            continue
        src = (seg_lookup.get(seg_id, {}).get("source", "") or "")[:140]
        score = None
        detail = ""

        if mode == "likert":
            per_crit = {}
            for crit in criteria:
                cid = crit["id"]
                vals = [r.scores_dict().get(cid) for r in rs]
                vals = [v for v in vals if isinstance(v, (int, float))]
                if len(vals) >= 2:
                    m = sum(vals) / len(vals)
                    std = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
                    per_crit[cid] = std
                    crit_disagree[cid].append(std)
            if per_crit:
                score = sum(per_crit.values()) / len(per_crit)
                detail = ", ".join(f"{c['name']} {round(per_crit[c['id']], 2)}"
                                   for c in criteria if c["id"] in per_crit)

        elif mode == "pairwise":
            prefs = [r.preference or "" for r in rs if r.preference]
            if len(prefs) >= 2:
                top = Counter(prefs).most_common(1)[0][1]
                score = 1.0 - top / len(prefs)
                pl = {p["id"]: p["label"] for p in campaign.preferences}
                detail = ", ".join(f"{pl.get(k, k)}×{v}" for k, v in Counter(prefs).items())

        elif mode == "span_only":
            by_ann = {r.annotator_id: r.spans_dict() for r in rs}
            f1s = []
            anns = list(by_ann)
            tgt = (seg_lookup.get(seg_id, {}).get("target", "") or "")
            for crit in criteria:
                cid = crit["id"]
                for i in range(len(anns)):
                    for j in range(i + 1, len(anns)):
                        ma = _char_mask(by_ann[anns[i]].get(cid, []), len(tgt))
                        mb = _char_mask(by_ann[anns[j]].get(cid, []), len(tgt))
                        f1s.append(_overlap_f1(ma, mb))
            if f1s:
                score = 1.0 - sum(f1s) / len(f1s)

        elif mode == "post_edit":
            tgt = (seg_lookup.get(seg_id, {}).get("target", "") or "")
            dists = []
            for r in rs:
                ed = (r.edited_text or "")
                base = max(len(tgt), len(ed), 1)
                dists.append(_edit_distance(tgt, ed) / base)
            if len(dists) >= 2:
                m = sum(dists) / len(dists)
                score = (sum((d - m) ** 2 for d in dists) / len(dists)) ** 0.5

        if score is not None:
            contested.append({
                "segment_id": seg_id, "source": src, "n_raters": len(rs),
                "disagreement": round(score, 3), "detail": detail,
            })

    contested.sort(key=lambda x: -x["disagreement"])
    by_criterion = []
    if mode == "likert":
        for cid, vals in crit_disagree.items():
            name = next((c["name"] for c in criteria if c["id"] == cid), cid)
            by_criterion.append({"name": name,
                                 "mean_disagreement": round(sum(vals) / len(vals), 3)})
        by_criterion.sort(key=lambda x: -x["mean_disagreement"])

    metric = {"likert": "std of scores (0 = perfect agreement)",
              "pairwise": "fraction of votes outside the majority",
              "span_only": "1 − mean character F1",
              "post_edit": "std of normalised edit distance"}.get(mode, "")
    return {"by_criterion": by_criterion, "contested_segments": contested[:15],
            "metric": metric, "n_contested": len(contested)}


def _consensus_deviation(complete, criteria):
    """Per-annotator and per-rating absolute deviation from peers' mean (likert).

    Returns (ann_dev, pair_dev): ann_dev maps annotator_id -> [deviations]; pair_dev maps
    (annotator_id, segment_id) -> [deviations]. Only segments rated by >=2 annotators count.
    """
    from collections import defaultdict
    by_seg = defaultdict(list)
    for r in complete:
        by_seg[r.segment_id].append((r.annotator_id, r.scores_dict()))
    ann_dev = defaultdict(list)
    pair_dev = defaultdict(list)
    for seg_id, lst in by_seg.items():
        if len(lst) < 2:
            continue
        for crit in criteria:
            cid = crit["id"]
            vals = [(aid, sc.get(cid)) for aid, sc in lst
                    if isinstance(sc.get(cid), (int, float))]
            if len(vals) < 2:
                continue
            for aid, v in vals:
                others = [vv for a2, vv in vals if a2 != aid]
                if not others:
                    continue
                dev = abs(v - sum(others) / len(others))
                ann_dev[aid].append(dev)
                pair_dev[(aid, seg_id)].append(dev)
    return ann_dev, pair_dev


def _performance(campaign, complete, criteria, mode):
    """Per-annotator quality signals + an analysis of whether the AI assistant helped."""
    try:
        from models import AssistantLog
        logs = AssistantLog.query.filter_by(campaign_id=campaign.id, role="annotator").all()
    except Exception:
        logs = []

    # (annotator_id, segment_id) pairs where the annotator consulted the assistant.
    ai_pairs = {(lg.annotator_id, lg.segment_id) for lg in logs if lg.annotator_id}
    ai_by_ann = {}
    ai_helpful_by_ann = {}
    for lg in logs:
        if not lg.annotator_id:
            continue
        ai_by_ann[lg.annotator_id] = ai_by_ann.get(lg.annotator_id, 0) + 1
        if lg.action == "helpful":
            ai_helpful_by_ann[lg.annotator_id] = ai_helpful_by_ann.get(lg.annotator_id, 0) + 1

    ann_dev, pair_dev = ({}, {})
    if mode == "likert":
        ann_dev, pair_dev = _consensus_deviation(complete, criteria)

    # pairwise: agreement with the majority preference per segment
    pref_majority = {}
    if mode == "pairwise":
        from collections import defaultdict, Counter
        by_seg = defaultdict(list)
        for r in complete:
            by_seg[r.segment_id].append((r.annotator_id, r.preference or ""))
        for seg_id, lst in by_seg.items():
            prefs = [p for _, p in lst if p]
            if prefs:
                pref_majority[seg_id] = Counter(prefs).most_common(1)[0][0]

    # group ratings + times by annotator
    by_ann = {}
    for r in complete:
        by_ann.setdefault(r.annotator_id, []).append(r)

    rows = []
    for a in _annotators(complete):
        aid = a["id"]
        rs = by_ann.get(aid, [])
        times = [r.time_spent_seconds for r in rs if (r.time_spent_seconds or 0) > 0]
        mean_time = round(sum(times) / len(times), 1) if times else None
        devs = ann_dev.get(aid, [])
        mean_dev = round(sum(devs) / len(devs), 3) if devs else None
        pref_agree = None
        if mode == "pairwise":
            shared = [r for r in rs if r.segment_id in pref_majority]
            if shared:
                hit = sum(1 for r in shared if (r.preference or "") == pref_majority[r.segment_id])
                pref_agree = round(100.0 * hit / len(shared), 1)
        rows.append({
            "id": aid, "name": a["name"], "email": a["email"],
            "expertise": a.get("expertise", ""),
            "n_complete": a.get("n_complete", len(rs)),
            "mean_time_sec": mean_time,
            "mean_deviation": mean_dev,
            "pref_agreement_pct": pref_agree,
            "ai_used": ai_by_ann.get(aid, 0),
            "ai_helpful": ai_helpful_by_ann.get(aid, 0),
            "flags": [],
        })

    # Flags: outlier disagreement, and unusually fast work (heuristic, for admin attention).
    mdevs = [r["mean_deviation"] for r in rows if r["mean_deviation"] is not None]
    if len(mdevs) >= 3:
        mu = sum(mdevs) / len(mdevs)
        sd = (sum((x - mu) ** 2 for x in mdevs) / len(mdevs)) ** 0.5
        for r in rows:
            if r["mean_deviation"] is not None and sd > 0 and r["mean_deviation"] > mu + 1.5 * sd:
                r["flags"].append("low agreement")
    mtimes = sorted(r["mean_time_sec"] for r in rows if r["mean_time_sec"] is not None)
    if len(mtimes) >= 3:
        median_t = mtimes[len(mtimes) // 2]
        for r in rows:
            if r["mean_time_sec"] is not None and median_t > 0 and r["mean_time_sec"] < 0.4 * median_t:
                r["flags"].append("very fast")

    # AI helpfulness: compare assisted vs un-assisted ratings on time and (likert) agreement.
    ai = {
        "total_assists": len(logs),
        "n_users": len({lg.annotator_id for lg in logs if lg.annotator_id}),
        "actions": {},
        "assisted_ratings": 0, "unassisted_ratings": 0,
        "mean_time_assisted": None, "mean_time_unassisted": None,
        "mean_dev_assisted": None, "mean_dev_unassisted": None,
        "verdict": "",
    }
    for lg in logs:
        ai["actions"][lg.action or "(none)"] = ai["actions"].get(lg.action or "(none)", 0) + 1

    t_as, t_un, d_as, d_un = [], [], [], []
    for r in complete:
        assisted = (r.annotator_id, r.segment_id) in ai_pairs
        if (r.time_spent_seconds or 0) > 0:
            (t_as if assisted else t_un).append(r.time_spent_seconds)
        if mode == "likert":
            dv = pair_dev.get((r.annotator_id, r.segment_id))
            if dv:
                (d_as if assisted else d_un).extend(dv)
    ai["assisted_ratings"] = sum(1 for r in complete if (r.annotator_id, r.segment_id) in ai_pairs)
    ai["unassisted_ratings"] = len(complete) - ai["assisted_ratings"]
    if t_as: ai["mean_time_assisted"] = round(sum(t_as) / len(t_as), 1)
    if t_un: ai["mean_time_unassisted"] = round(sum(t_un) / len(t_un), 1)
    if d_as: ai["mean_dev_assisted"] = round(sum(d_as) / len(d_as), 3)
    if d_un: ai["mean_dev_unassisted"] = round(sum(d_un) / len(d_un), 3)

    if ai["total_assists"] == 0:
        ai["verdict"] = "The AI assistant hasn't been used in this campaign yet."
    elif ai["mean_dev_assisted"] is not None and ai["mean_dev_unassisted"] is not None:
        if ai["mean_dev_assisted"] < ai["mean_dev_unassisted"]:
            ai["verdict"] = ("On segments where annotators consulted the assistant, their ratings "
                             "agreed more closely with peers (lower deviation from consensus). This "
                             "is associational, not causal.")
        elif ai["mean_dev_assisted"] > ai["mean_dev_unassisted"]:
            ai["verdict"] = ("Assisted ratings deviated from consensus slightly more than un-assisted "
                             "ones; no clear agreement benefit so far (associational only).")
        else:
            ai["verdict"] = "Assisted and un-assisted ratings show similar agreement so far."
    else:
        ai["verdict"] = ("Not enough overlapping data yet to compare assisted vs un-assisted "
                         "agreement; check back as more ratings arrive.")

    # Randomised experiment (A/B): compare AI-eligible vs not-eligible segments. Because
    # eligibility is randomised, this is a causal (intent-to-treat) estimate, not a correlation.
    # Uses every complete rating, whether or not the assistant was actually opened.
    ai["ab_enabled"] = bool(getattr(campaign, "ai_ab_enabled", False))
    if ai["ab_enabled"]:
        et, ut, ed, ud = [], [], [], []
        n_elig = 0
        for r in complete:
            elig = campaign.ai_ab_eligible(r.annotator_id, r.segment_id)
            n_elig += 1 if elig else 0
            if (r.time_spent_seconds or 0) > 0:
                (et if elig else ut).append(r.time_spent_seconds)
            if mode == "likert":
                dv = pair_dev.get((r.annotator_id, r.segment_id))
                if dv:
                    (ed if elig else ud).extend(dv)
        ai["ab_fraction"] = getattr(campaign, "ai_ab_fraction", 50)
        ai["ab_eligible_ratings"] = n_elig
        ai["ab_ineligible_ratings"] = len(complete) - n_elig
        ai["ab_mean_time_eligible"] = round(sum(et) / len(et), 1) if et else None
        ai["ab_mean_time_ineligible"] = round(sum(ut) / len(ut), 1) if ut else None
        ai["ab_mean_dev_eligible"] = round(sum(ed) / len(ed), 3) if ed else None
        ai["ab_mean_dev_ineligible"] = round(sum(ud) / len(ud), 3) if ud else None
        if ai["ab_mean_dev_eligible"] is not None and ai["ab_mean_dev_ineligible"] is not None:
            delta = ai["ab_mean_dev_ineligible"] - ai["ab_mean_dev_eligible"]
            if delta > 0.001:
                ai["ab_verdict"] = ("Randomised result: segments where AI was available showed lower "
                                    "deviation from consensus, evidence that AI access improved agreement.")
            elif delta < -0.001:
                ai["ab_verdict"] = ("Randomised result: AI-available segments showed higher deviation from "
                                    "consensus; no agreement benefit, possibly a drawback.")
            else:
                ai["ab_verdict"] = "Randomised result: AI access made no measurable difference to agreement."
        else:
            ai["ab_verdict"] = ("Randomised experiment is on, but there isn't enough overlapping data yet "
                                "to estimate the effect; check back as ratings accumulate.")

    return {"annotators": rows, "ai": ai}


# --------------------------------------------------------------------------
# likert
# --------------------------------------------------------------------------

def _likert_stats(campaign, ratings, criteria, annotators):
    ann_ids = [a["id"] for a in annotators]
    seg_ids = [s.get("id") for s in campaign.segments]
    by_ann = {}
    for r in ratings:
        by_ann.setdefault(r.annotator_id, {})[r.segment_id] = r.scores_dict()

    crit_rows = []
    for c in criteria:
        cid = c["id"]
        # matrix: one row per segment, one column per annotator
        matrix = []
        all_scores = []
        dist = {}
        for sid in seg_ids:
            row = []
            for aid in ann_ids:
                v = by_ann.get(aid, {}).get(sid, {}).get(cid)
                v = v if isinstance(v, (int, float)) else None
                row.append(v)
                if v is not None:
                    all_scores.append(v)
                    dist[int(v)] = dist.get(int(v), 0) + 1
            matrix.append(row)
        alpha = krippendorff_alpha_interval(matrix)

        # mean pairwise correlations on co-rated segments
        pcorrs, scorrs = [], []
        for i in range(len(ann_ids)):
            for j in range(i + 1, len(ann_ids)):
                xa, xb = [], []
                for row in matrix:
                    if row[i] is not None and row[j] is not None:
                        xa.append(row[i]); xb.append(row[j])
                if len(xa) >= 2:
                    p = pearson(xa, xb); s = spearman(xa, xb)
                    if p is not None: pcorrs.append(p)
                    if s is not None: scorrs.append(s)
        crit_rows.append({
            "id": cid, "name": c["name"],
            "n_ratings": len(all_scores),
            "mean": _r(np.mean(all_scores)) if all_scores else None,
            "std": _r(np.std(all_scores)) if all_scores else None,
            "alpha": _r(alpha),
            "mean_pairwise_pearson": _r(np.mean(pcorrs)) if pcorrs else None,
            "mean_pairwise_spearman": _r(np.mean(scorrs)) if scorrs else None,
            "distribution": {str(k): dist[k] for k in sorted(dist)},
        })

    # overall annotator correlation matrix on concatenated (seg,crit) cells
    cells = {}
    for aid in ann_ids:
        vec = {}
        segs = by_ann.get(aid, {})
        for sid, sc in segs.items():
            for c in criteria:
                v = sc.get(c["id"])
                if isinstance(v, (int, float)):
                    vec[(sid, c["id"])] = v
        cells[aid] = vec
    labels = [a["name"] for a in annotators]
    pmat, smat = [], []
    pair_rows = []
    for i, ai in enumerate(ann_ids):
        prow, srow = [], []
        for j, aj in enumerate(ann_ids):
            keys = set(cells[ai]) & set(cells[aj])
            if i == j:
                prow.append(1.0); srow.append(1.0); continue
            if len(keys) >= 2:
                xa = [cells[ai][k] for k in keys]
                xb = [cells[aj][k] for k in keys]
                p = pearson(xa, xb); s = spearman(xa, xb)
                prow.append(_r(p)); srow.append(_r(s))
                if j > i:
                    pair_rows.append({"a": labels[i], "b": labels[j],
                                      "pearson": _r(p), "spearman": _r(s), "n": len(keys)})
            else:
                prow.append(None); srow.append(None)
        pmat.append(prow); smat.append(srow)

    # per-system mean scores
    systems = _likert_systems(campaign, ratings, criteria)

    return {
        "criteria": crit_rows,
        "pairwise_corr": pair_rows,
        "annotator_corr_matrix": {"labels": labels, "pearson": pmat, "spearman": smat},
        "systems": systems,
    }


def _likert_systems(campaign, ratings, criteria):
    seg_system = {}
    for s in campaign.segments:
        sysname = s.get("system") or ""
        if sysname:
            seg_system[s.get("id")] = sysname
    if not seg_system:
        return None
    from collections import defaultdict
    acc = defaultdict(list)  # (system, crit) -> [scores]
    for r in ratings:
        sysname = seg_system.get(r.segment_id)
        if not sysname:
            continue
        sc = r.scores_dict()
        for c in criteria:
            v = sc.get(c["id"])
            if isinstance(v, (int, float)):
                acc[(sysname, c["id"])].append(v)
    rows = []
    crit_names = {c["id"]: c["name"] for c in criteria}
    for (sysname, cid), vals in sorted(acc.items()):
        rows.append({"system": sysname, "criterion": crit_names.get(cid, cid),
                     "mean": _r(np.mean(vals)), "std": _r(np.std(vals)), "n": len(vals)})
    return rows


# --------------------------------------------------------------------------
# pairwise
# --------------------------------------------------------------------------

def _pref_groups(campaign):
    """Map each preference option id to 'A' / 'tie' / 'B' by its position."""
    prefs = campaign.preferences
    n = len(prefs)
    groups = {}
    if n == 0:
        return groups, "No preference options defined."
    if n % 2 == 1:
        mid = n // 2
        for i, p in enumerate(prefs):
            groups[p["id"]] = "A" if i < mid else ("tie" if i == mid else "B")
        note = ("Outcomes inferred from option order: the first %d favour A, the "
                "middle option is a tie, the last %d favour B." % (mid, mid))
    else:
        half = n // 2
        for i, p in enumerate(prefs):
            groups[p["id"]] = "A" if i < half else "B"
        note = ("Outcomes inferred from option order: the first %d favour A, the "
                "last %d favour B (no tie option)." % (half, half))
    return groups, note


def _pairwise_stats(campaign, ratings, annotators):
    prefs = campaign.preferences
    labels = {p["id"]: p["label"] for p in prefs}
    groups, note = _pref_groups(campaign)

    # preference distribution
    dist = {p["id"]: 0 for p in prefs}
    for r in ratings:
        if r.preference in dist:
            dist[r.preference] += 1
    pref_dist = [{"id": p["id"], "label": p["label"], "count": dist[p["id"]]} for p in prefs]

    # agreement: build per-segment label lists
    from collections import defaultdict
    by_seg = defaultdict(list)
    for r in ratings:
        if r.preference:
            by_seg[r.segment_id].append(r.preference)
    overlap_items = {s: v for s, v in by_seg.items() if len(v) >= 2}

    pct_agreement = None
    if overlap_items:
        agree = 0; total = 0
        for s, labs in overlap_items.items():
            for i in range(len(labs)):
                for j in range(i + 1, len(labs)):
                    total += 1
                    if labs[i] == labs[j]:
                        agree += 1
        pct_agreement = _r(100.0 * agree / total) if total else None

    cat_ids = [p["id"] for p in prefs]
    fk = None
    if overlap_items:
        rows = []
        for s, labs in overlap_items.items():
            vec = [labs.count(cid) for cid in cat_ids]
            rows.append(vec)
        fk = fleiss_kappa(rows)

    # pairwise Cohen kappa (averaged) for extra robustness w/ 2 raters
    ann_ids = [a["id"] for a in annotators]
    seg_label = defaultdict(dict)
    for r in ratings:
        if r.preference:
            seg_label[r.annotator_id][r.segment_id] = r.preference
    ck_list = []
    for i in range(len(ann_ids)):
        for j in range(i + 1, len(ann_ids)):
            keys = set(seg_label[ann_ids[i]]) & set(seg_label[ann_ids[j]])
            if len(keys) >= 2:
                la = [seg_label[ann_ids[i]][k] for k in keys]
                lb = [seg_label[ann_ids[j]][k] for k in keys]
                ck = cohen_kappa(la, lb)
                if ck is not None:
                    ck_list.append(ck)
    mean_cohen = _r(np.mean(ck_list)) if ck_list else None

    # A / tie / B outcome rates
    outcome = {"A": 0, "tie": 0, "B": 0}
    for r in ratings:
        g = groups.get(r.preference)
        if g in outcome:
            outcome[g] += 1
    tot_out = sum(outcome.values())
    outcome_rates = {k: _r(100.0 * v / tot_out) if tot_out else None for k, v in outcome.items()}

    # per-system win rates (needs system_a / system_b)
    systems = _pairwise_systems(campaign, ratings, groups)

    return {
        "n_judgements": sum(dist.values()),
        "preference_distribution": pref_dist,
        "percent_agreement": pct_agreement,
        "fleiss_kappa": _r(fk),
        "mean_cohen_kappa": mean_cohen,
        "outcome_counts": outcome,
        "outcome_rates": outcome_rates,
        "systems": systems,
        "mapping_note": note,
    }


def _pairwise_systems(campaign, ratings, groups):
    seg_ab = {}
    has_sys = False
    for s in campaign.segments:
        _a, _b, sa, sb = campaign.pairwise_candidates(s)
        if sa or sb:
            has_sys = True
        seg_ab[s.get("id")] = (sa or "A", sb or "B")
    if not has_sys:
        return None
    from collections import defaultdict
    rec = defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0, "n": 0})
    for r in ratings:
        g = groups.get(r.preference)
        if g is None or r.segment_id not in seg_ab:
            continue
        sa, sb = seg_ab[r.segment_id]
        rec[sa]["n"] += 1; rec[sb]["n"] += 1
        if g == "A":
            rec[sa]["wins"] += 1; rec[sb]["losses"] += 1
        elif g == "B":
            rec[sb]["wins"] += 1; rec[sa]["losses"] += 1
        else:
            rec[sa]["ties"] += 1; rec[sb]["ties"] += 1
    rows = []
    for sysname, d in sorted(rec.items()):
        n = d["n"]
        wr = (d["wins"] + 0.5 * d["ties"]) / n if n else None
        rows.append({"system": sysname, "wins": d["wins"], "losses": d["losses"],
                     "ties": d["ties"], "n": n, "win_rate": _r(wr)})
    rows.sort(key=lambda x: (x["win_rate"] is not None, x["win_rate"]), reverse=True)
    return rows


# --------------------------------------------------------------------------
# span-only
# --------------------------------------------------------------------------

def _char_mask(spans_for_crit, length):
    mask = np.zeros(max(length, 1), dtype=bool)
    for sp in spans_for_crit:
        if len(sp) >= 2 and isinstance(sp[0], int) and isinstance(sp[1], int):
            s = max(0, sp[0]); e = min(length, sp[1])
            if e > s:
                mask[s:e] = True
    return mask


_TOKEN_RE = re.compile(r"\S+")


def _token_spans(text):
    """Whitespace-delimited tokens as (start, end) character offsets (script-agnostic)."""
    return [(m.start(), m.end()) for m in _TOKEN_RE.finditer(text or "")]


def _token_mask(spans_for_crit, token_spans):
    """Boolean per token; a token is flagged if any flagged span overlaps it.
    This ignores off-by-a-character boundary differences between annotators."""
    mask = np.zeros(max(len(token_spans), 1), dtype=bool)
    for sp in spans_for_crit:
        if len(sp) >= 2 and isinstance(sp[0], int) and isinstance(sp[1], int):
            s, e = sp[0], sp[1]
            if e <= s:
                continue
            for ti, (ts, te) in enumerate(token_spans):
                if ts < e and te > s:        # token overlaps the flagged span
                    mask[ti] = True
    return mask


def _overlap_f1(ma, mb):
    """2*|A∩B| / (|A|+|B|); both-empty counts as full agreement; both-zero-after-skip = None."""
    inter = np.logical_and(ma, mb).sum()
    sza = ma.sum(); szb = mb.sum()
    if sza == 0 and szb == 0:
        return 1.0
    if sza + szb == 0:
        return None
    return 2.0 * inter / (sza + szb)


def _span_stats(campaign, ratings, criteria, annotators):
    seg_len = {}
    seg_tokens = {}
    seg_system = {}
    for s in campaign.segments:
        target = s.get("target", "") or ""
        seg_len[s.get("id")] = len(target)
        seg_tokens[s.get("id")] = _token_spans(target)
        if s.get("system"):
            seg_system[s.get("id")] = s.get("system")

    by_ann = {}
    for r in ratings:
        by_ann.setdefault(r.annotator_id, {})[r.segment_id] = r.spans_dict()

    # per-criterion counts/density
    from collections import defaultdict
    crit_rows = []
    for c in criteria:
        cid = c["id"]
        n_spans = 0; n_chars = 0; n_segwith = 0; seg_seen = set()
        for r in ratings:
            spans = r.spans_dict().get(cid, [])
            if spans:
                n_spans += len(spans)
                for sp in spans:
                    if len(sp) >= 2:
                        n_chars += max(0, sp[1] - sp[0])
                key = (r.annotator_id, r.segment_id)
                seg_seen.add(key)
        crit_rows.append({
            "id": cid, "name": c["name"],
            "n_spans": n_spans,
            "char_count": n_chars,
            "marked_instances": len(seg_seen),
        })

    # pairwise span agreement: character-level F1 and token-level (word-overlap) F1,
    # averaged across criteria and co-reviewed segments.
    ann_ids = [a["id"] for a in annotators]
    labels = [a["name"] for a in annotators]
    f1_mat = []
    f1_tok_mat = []
    pair_rows = []
    all_f1 = []
    all_f1_tok = []
    for i, ai in enumerate(ann_ids):
        frow = []
        trow = []
        for j, aj in enumerate(ann_ids):
            if i == j:
                frow.append(1.0); trow.append(1.0); continue
            f1s = []; f1ts = []
            for sid, length in seg_len.items():
                sa = by_ann.get(ai, {}).get(sid)
                sb = by_ann.get(aj, {}).get(sid)
                if sa is None or sb is None:
                    continue  # both must have reviewed this segment
                toks = seg_tokens.get(sid, [])
                for c in criteria:
                    cid = c["id"]
                    fc = _overlap_f1(_char_mask(sa.get(cid, []), length),
                                     _char_mask(sb.get(cid, []), length))
                    if fc is not None:
                        f1s.append(fc)
                    ft = _overlap_f1(_token_mask(sa.get(cid, []), toks),
                                     _token_mask(sb.get(cid, []), toks))
                    if ft is not None:
                        f1ts.append(ft)
            mf1 = float(np.mean(f1s)) if f1s else None
            mf1t = float(np.mean(f1ts)) if f1ts else None
            frow.append(_r(mf1)); trow.append(_r(mf1t))
            if j > i and mf1 is not None:
                n_seg = len({sid for sid in seg_len
                             if by_ann.get(ai, {}).get(sid) is not None
                             and by_ann.get(aj, {}).get(sid) is not None})
                pair_rows.append({"a": labels[i], "b": labels[j], "f1": _r(mf1),
                                  "f1_token": _r(mf1t), "n_segments": n_seg})
                all_f1.append(mf1)
                if mf1t is not None:
                    all_f1_tok.append(mf1t)
        f1_mat.append(frow)
        f1_tok_mat.append(trow)

    systems = None
    if seg_system:
        acc = defaultdict(lambda: {"spans": 0, "chars": 0, "n": 0})
        for r in ratings:
            sysname = seg_system.get(r.segment_id)
            if not sysname:
                continue
            acc[sysname]["n"] += 1
            for c in criteria:
                for sp in r.spans_dict().get(c["id"], []):
                    acc[sysname]["spans"] += 1
                    if len(sp) >= 2:
                        acc[sysname]["chars"] += max(0, sp[1] - sp[0])
        systems = []
        for sysname, d in sorted(acc.items()):
            systems.append({"system": sysname, "spans": d["spans"], "chars": d["chars"],
                            "n": d["n"],
                            "spans_per_segment": _r(d["spans"] / d["n"]) if d["n"] else None})

    return {
        "criteria": crit_rows,
        "f1_matrix": {"labels": labels, "f1": f1_mat},
        "f1_token_matrix": {"labels": labels, "f1": f1_tok_mat},
        "pairwise_f1": pair_rows,
        "mean_span_f1": _r(np.mean(all_f1)) if all_f1 else None,
        "mean_span_f1_token": _r(np.mean(all_f1_tok)) if all_f1_tok else None,
        "systems": systems,
    }


def _edit_distance(a, b):
    """Character-level Levenshtein distance."""
    a = a or ""; b = b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _post_edit_stats(campaign, ratings, annotators):
    """Summarise post-editing: how much each output was edited (less = better quality).

    Per segment/annotator we compare the edited text to the original output and compute a
    character-level edit distance, normalised by the longer of the two lengths (0 = identical,
    1 = completely rewritten). We also track the share of outputs that were changed at all.
    """
    seg_orig = {}
    seg_system = {}
    for s in campaign.segments:
        seg_orig[s.get("id")] = s.get("target", "") or ""
        if s.get("system"):
            seg_system[s.get("id")] = s.get("system")

    rows = []
    by_system = {}
    n_changed = 0
    norm_all = []
    for r in ratings:
        orig = seg_orig.get(r.segment_id)
        if orig is None:
            continue
        edited = (r.edited_text or "")
        dist = _edit_distance(orig, edited)
        denom = max(len(orig), len(edited)) or 1
        norm = dist / denom
        changed = (edited.strip() != orig.strip())
        if changed:
            n_changed += 1
        norm_all.append(norm)
        sysname = seg_system.get(r.segment_id, "")
        by_system.setdefault(sysname or "—", []).append(norm)
        rows.append({"segment_id": r.segment_id, "system": sysname,
                     "char_edits": dist, "norm_distance": _r(norm), "changed": changed})

    n = len(rows)
    systems = []
    for name, vals in sorted(by_system.items()):
        systems.append({"system": name, "n": len(vals),
                        "mean_norm_distance": _r(sum(vals) / len(vals)) if vals else None})

    # Inter-annotator: agreement on whether a segment needed editing (binary).
    overlap_changed = {}
    for r in ratings:
        orig = seg_orig.get(r.segment_id)
        if orig is None:
            continue
        changed = ((r.edited_text or "").strip() != orig.strip())
        overlap_changed.setdefault(r.segment_id, []).append(changed)
    agree = [v for v in overlap_changed.values() if len(v) >= 2]
    pct_change_agreement = None
    if agree:
        unanimous = sum(1 for v in agree if all(v) or not any(v))
        pct_change_agreement = _r(100.0 * unanimous / len(agree))

    # Finer inter-annotator measure: when ≥2 annotators post-edited the same segment, how
    # similar are their corrected versions? similarity = 1 - normalised edit distance,
    # averaged over all annotator pairs and co-edited segments (1 = identical edits).
    by_seg_edits = {}
    for r in ratings:
        if seg_orig.get(r.segment_id) is None:
            continue
        by_seg_edits.setdefault(r.segment_id, []).append(r.edited_text or "")
    sims = []
    n_pairs = 0
    for sid, texts in by_seg_edits.items():
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                d = _edit_distance(texts[i], texts[j])
                denom = max(len(texts[i]), len(texts[j])) or 1
                sims.append(1.0 - d / denom)
                n_pairs += 1
    mean_pairwise_similarity = _r(sum(sims) / len(sims)) if sims else None

    return {
        "n_post_edits": n,
        "n_changed": n_changed,
        "percent_changed": _r(100.0 * n_changed / n) if n else None,
        "mean_norm_distance": _r(sum(norm_all) / len(norm_all)) if norm_all else None,
        "systems": systems,
        "change_agreement_pct": pct_change_agreement,
        "mean_pairwise_similarity": mean_pairwise_similarity,
        "n_edit_pairs": n_pairs,
        "rows": rows[:200],
    }


def _difficulty_breakdown(campaign, ratings, criteria):
    """Group completed ratings by segment difficulty (easy/medium/hard) and report a
    per-level primary metric appropriate to the mode."""
    diff_map = campaign.difficulty_for_segments()
    if not diff_map:
        return None
    mode = campaign.mode
    levels = ["easy", "medium", "hard"]
    seg_ids = {lv: set() for lv in levels}
    vals = {lv: [] for lv in levels}
    counts = {lv: 0 for lv in levels}
    crit_names = {c["id"]: c["name"] for c in (criteria or [])}
    per_crit = {lv: {cid: [] for cid in crit_names} for lv in levels}  # likert

    seg_orig = {s.get("id"): (s.get("target", "") or "") for s in campaign.segments}
    crit_ids = [c["id"] for c in (criteria or [])]

    for r in ratings:
        lv = diff_map.get(r.segment_id)
        if lv not in levels:
            continue
        counts[lv] += 1
        seg_ids[lv].add(r.segment_id)
        if mode == "likert" and crit_ids:
            sc = r.scores_dict()
            xs = [sc[c] for c in crit_ids if isinstance(sc.get(c), (int, float))]
            if xs:
                vals[lv].append(sum(xs) / len(xs))
            for cid in crit_ids:
                if isinstance(sc.get(cid), (int, float)):
                    per_crit[lv][cid].append(sc[cid])
        elif mode == "post_edit":
            orig = seg_orig.get(r.segment_id, "")
            ed = r.edited_text or ""
            denom = max(len(orig), len(ed)) or 1
            vals[lv].append(_edit_distance(orig, ed) / denom)
        elif mode == "span_only":
            sp = r.spans_dict()
            vals[lv].append(sum(len(v) for v in sp.values()))

    metric_label = {"likert": "Mean score", "post_edit": "Mean edit distance",
                    "span_only": "Spans / segment"}.get(mode)
    rows = []
    for lv in levels:
        nseg = len(seg_ids[lv])
        if nseg == 0 and counts[lv] == 0:
            continue
        m = (_r(sum(vals[lv]) / len(vals[lv])) if vals[lv] else None) if metric_label else None
        rows.append({"level": lv, "n_segments": nseg, "n_ratings": counts[lv], "metric": m})
    if not rows:
        return None
    present_levels = [r["level"] for r in rows]
    by_criterion = None
    if mode == "likert" and crit_names:
        by_criterion = {"criteria": [crit_names[cid] for cid in crit_ids],
                        "levels": present_levels,
                        "data": {lv: [(_r(sum(per_crit[lv][cid]) / len(per_crit[lv][cid]))
                                       if per_crit[lv][cid] else None) for cid in crit_ids]
                                 for lv in present_levels}}
    return {"rows": rows, "metric_label": metric_label,
            "by_criterion": by_criterion,
            "source": "provided" if campaign.has_explicit_difficulty() else "auto"}


# --------------------------------------------------------------------------
# charts (matplotlib -> base64 PNG data URIs)
# --------------------------------------------------------------------------

def build_charts(results):
    """Return {chart_name: 'data:image/png;base64,...'} for the given results."""
    import io, base64
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import rcParams

    ACCENT = "#0072B2"
    PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442"]

    # Polished, consistent look across every figure.
    rcParams.update({
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": "#d8d6d0",
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "axes.axisbelow": True,
        "axes.titlesize": 12,
        "axes.titleweight": "600",
        "axes.titlepad": 12,
        "axes.labelcolor": "#4a4843",
        "axes.labelsize": 10,
        "grid.color": "#ecebe6",
        "grid.linewidth": 0.9,
        "xtick.color": "#6b6b66",
        "ytick.color": "#6b6b66",
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "font.size": 10,
        "legend.frameon": False,
        "legend.fontsize": 8,
    })

    def _grid_y(ax):
        ax.grid(axis="x", visible=False)
        ax.tick_params(length=0)

    def _bar_labels(ax, bars, fmt="{:.2f}"):
        for b in bars:
            h = b.get_height()
            if h:
                ax.annotate(fmt.format(h), (b.get_x() + b.get_width() / 2, h),
                            ha="center", va="bottom", fontsize=8, color="#4a4843",
                            xytext=(0, 2), textcoords="offset points")

    def _fig_to_uri(fig):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")

    def _heatmap(matrix_dict, title):
        labels = matrix_dict["labels"]
        if len(labels) < 2:
            return None
        data = np.array([[(v if v is not None else np.nan) for v in row]
                         for row in matrix_dict["f1"]], dtype=float)
        fig, ax = plt.subplots(figsize=(max(3.5, len(labels) * 0.9),
                                        max(3, len(labels) * 0.8)))
        im = ax.imshow(data, vmin=0, vmax=1, cmap="YlGn")
        ax.grid(False)
        ax.set_xticks(range(len(labels))); ax.set_yticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_yticklabels(labels, fontsize=8)
        for i in range(len(labels)):
            for j in range(len(labels)):
                if not np.isnan(data[i, j]):
                    ax.text(j, i, f"{data[i,j]:.2f}", ha="center", va="center",
                            fontsize=7, color="#222")
        ax.set_title(title)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        return _fig_to_uri(fig)

    charts = {}
    mode = results.get("mode")

    if mode == "likert":
        L = results["likert"]
        crits = L["criteria"]
        if crits:
            names = [c["name"] for c in crits]
            means = [c["mean"] if c["mean"] is not None else 0 for c in crits]
            stds = [c["std"] if c["std"] is not None else 0 for c in crits]
            fig, ax = plt.subplots(figsize=(max(4, len(names) * 1.3), 3.2))
            bars = ax.bar(names, means, yerr=stds, color=ACCENT, capsize=4,
                          alpha=0.92, width=0.62, edgecolor="white", linewidth=0.6)
            ax.set_ylabel("Mean score (±std)")
            ax.set_title("Mean score by criterion")
            ax.tick_params(axis="x", rotation=0, length=0)
            charts["criteria_means"] = _fig_to_uri(fig)

            # score distribution (stacked by value)
            all_vals = sorted({int(v) for c in crits for v in c["distribution"].keys()
                               for v in [int(v)]})
            if all_vals:
                fig, ax = plt.subplots(figsize=(max(4, len(names) * 1.3), 3.2))
                bottom = np.zeros(len(names))
                for vi, val in enumerate(all_vals):
                    heights = [c["distribution"].get(str(val), 0) for c in crits]
                    ax.bar(names, heights, bottom=bottom, label=str(val),
                           color=PALETTE[vi % len(PALETTE)])
                    bottom += np.array(heights)
                ax.set_ylabel("Count")
                ax.set_title("Rating distribution by criterion")
                ax.tick_params(axis="x", rotation=0, length=0)
                ax.legend(title="Score", fontsize=8, title_fontsize=8,
                          loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
                charts["score_distribution"] = _fig_to_uri(fig)

        mat = L["annotator_corr_matrix"]
        labels = mat["labels"]
        if len(labels) >= 2:
            data = np.array([[ (v if v is not None else np.nan) for v in row]
                             for row in mat["pearson"]], dtype=float)
            fig, ax = plt.subplots(figsize=(max(3.5, len(labels) * 0.9),
                                            max(3, len(labels) * 0.8)))
            im = ax.imshow(data, vmin=-1, vmax=1, cmap="RdYlGn")
            ax.grid(False)
            ax.set_xticks(range(len(labels))); ax.set_yticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
            ax.set_yticklabels(labels, fontsize=8)
            for i in range(len(labels)):
                for j in range(len(labels)):
                    if not np.isnan(data[i, j]):
                        ax.text(j, i, f"{data[i,j]:.2f}", ha="center", va="center",
                                fontsize=7, color="#222")
            ax.set_title("Annotator agreement (Pearson)")
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            charts["annotator_corr"] = _fig_to_uri(fig)

        if L.get("systems"):
            # grouped bar: system mean per criterion
            sysset = sorted({r["system"] for r in L["systems"]})
            critset = sorted({r["criterion"] for r in L["systems"]})
            lookup = {(r["system"], r["criterion"]): r["mean"] for r in L["systems"]}
            x = np.arange(len(critset)); w = 0.8 / max(1, len(sysset))
            fig, ax = plt.subplots(figsize=(max(4, len(critset) * 1.5), 3.2))
            for si, s in enumerate(sysset):
                vals = [lookup.get((s, c)) or 0 for c in critset]
                ax.bar(x + si * w, vals, w, label=s, color=PALETTE[si % len(PALETTE)])
            ax.set_xticks(x + w * (len(sysset) - 1) / 2)
            ax.set_xticklabels(critset, rotation=0)
            ax.set_ylabel("Mean score"); ax.set_title("Mean score by system")
            ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
            charts["system_means"] = _fig_to_uri(fig)

        sp = L.get("spans")
        if sp:
            scs = sp["criteria"]
            if scs:
                fig, ax = plt.subplots(figsize=(max(4, len(scs) * 1.3), 3.2))
                bars = ax.bar([c["name"] for c in scs], [c["n_spans"] for c in scs],
                              color=PALETTE[:len(scs)], alpha=0.92, width=0.6,
                              edgecolor="white", linewidth=0.6)
                _bar_labels(ax, bars, "{:.0f}")
                ax.set_ylabel("Spans marked"); ax.set_title("Error spans by criterion")
                ax.tick_params(axis="x", rotation=0, length=0)
                charts["likert_spans_per_type"] = _fig_to_uri(fig)
            labels = sp["f1_matrix"]["labels"]
            if len(labels) >= 2:
                data = np.array([[(v if v is not None else np.nan) for v in row]
                                 for row in sp["f1_matrix"]["f1"]], dtype=float)
                fig, ax = plt.subplots(figsize=(max(3.5, len(labels) * 0.9),
                                                max(3, len(labels) * 0.8)))
                im = ax.imshow(data, vmin=0, vmax=1, cmap="YlGn")
                ax.grid(False)
                ax.set_xticks(range(len(labels))); ax.set_yticks(range(len(labels)))
                ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
                ax.set_yticklabels(labels, fontsize=8)
                for i in range(len(labels)):
                    for j in range(len(labels)):
                        if not np.isnan(data[i, j]):
                            ax.text(j, i, f"{data[i,j]:.2f}", ha="center", va="center",
                                    fontsize=7, color="#222")
                ax.set_title("Span agreement (F1)")
                fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                charts["likert_span_agreement"] = _fig_to_uri(fig)
            if sp.get("f1_token_matrix"):
                tok = _heatmap(sp["f1_token_matrix"], "Span agreement (token F1)")
                if tok:
                    charts["likert_span_agreement_token"] = tok

    elif mode == "pairwise":
        P = results["pairwise"]
        pd = P["preference_distribution"]
        if pd:
            fig, ax = plt.subplots(figsize=(max(4, len(pd) * 1.2), 3.2))
            bars = ax.bar([p["label"] for p in pd], [p["count"] for p in pd],
                          color=ACCENT, alpha=0.92, width=0.6, edgecolor="white", linewidth=0.6)
            _bar_labels(ax, bars, "{:.0f}")
            ax.set_ylabel("Count"); ax.set_title("Preference distribution")
            ax.tick_params(axis="x", rotation=15, length=0)
            charts["preference_distribution"] = _fig_to_uri(fig)
        if P.get("systems"):
            sysrows = P["systems"]
            fig, ax = plt.subplots(figsize=(max(4, len(sysrows) * 1.2), 3.2))
            bars = ax.bar([r["system"] for r in sysrows],
                          [(r["win_rate"] or 0) * 100 for r in sysrows],
                          color="#3a9b7a", alpha=0.92, width=0.55, edgecolor="white", linewidth=0.6)
            _bar_labels(ax, bars, "{:.0f}%")
            ax.set_ylabel("Win rate (%)"); ax.set_ylim(0, 105)
            ax.set_title("System win rate")
            ax.tick_params(axis="x", rotation=15, length=0)
            charts["system_winrate"] = _fig_to_uri(fig)

    elif mode == "span_only":
        S = results["span_only"]
        crits = S["criteria"]
        if crits:
            fig, ax = plt.subplots(figsize=(max(4, len(crits) * 1.3), 3.2))
            bars = ax.bar([c["name"] for c in crits], [c["n_spans"] for c in crits],
                          color=PALETTE[:len(crits)], alpha=0.92, width=0.6,
                          edgecolor="white", linewidth=0.6)
            _bar_labels(ax, bars, "{:.0f}")
            ax.set_ylabel("Spans marked"); ax.set_title("Error spans by type")
            ax.tick_params(axis="x", rotation=0, length=0)
            charts["spans_per_type"] = _fig_to_uri(fig)
        mat = S["f1_matrix"]; labels = mat["labels"]
        if len(labels) >= 2:
            data = np.array([[ (v if v is not None else np.nan) for v in row]
                             for row in mat["f1"]], dtype=float)
            fig, ax = plt.subplots(figsize=(max(3.5, len(labels) * 0.9),
                                            max(3, len(labels) * 0.8)))
            im = ax.imshow(data, vmin=0, vmax=1, cmap="YlGn")
            ax.grid(False)
            ax.set_xticks(range(len(labels))); ax.set_yticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
            ax.set_yticklabels(labels, fontsize=8)
            for i in range(len(labels)):
                for j in range(len(labels)):
                    if not np.isnan(data[i, j]):
                        ax.text(j, i, f"{data[i,j]:.2f}", ha="center", va="center",
                                fontsize=7, color="#222")
            ax.set_title("Annotator span agreement (F1)")
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            charts["span_agreement"] = _fig_to_uri(fig)
        if S.get("f1_token_matrix"):
            tok = _heatmap(S["f1_token_matrix"], "Annotator span agreement (token F1)")
            if tok:
                charts["span_agreement_token"] = tok

    elif mode == "post_edit":
        PE = results["post_edit"]
        sysrows = [s for s in PE.get("systems", []) if s.get("mean_norm_distance") is not None]
        if sysrows:
            fig, ax = plt.subplots(figsize=(max(4, len(sysrows) * 1.4), 3.2))
            names = [s["system"] for s in sysrows]
            vals = [s["mean_norm_distance"] for s in sysrows]
            bars = ax.bar(names, vals, color=PALETTE[:len(sysrows)], alpha=0.92, width=0.6,
                          edgecolor="white", linewidth=0.6)
            _bar_labels(ax, bars, "{:.2f}")
            ax.set_ylim(0, max(0.1, max(vals) * 1.25))
            ax.set_ylabel("Mean edit distance (0–1)")
            ax.set_title("Post-editing effort by system (lower = better)")
            ax.tick_params(axis="x", rotation=0, length=0)
            charts["postedit_by_system"] = _fig_to_uri(fig)

    # Difficulty breakdown charts (any mode that produced one).
    DF = results.get("difficulty")
    if DF:
        DIFF_COLORS = {"easy": "#3a9b7a", "medium": "#e0962e", "hard": "#c0563f"}
        rows = [r for r in DF["rows"] if r.get("metric") is not None]
        if rows and DF.get("metric_label"):
            fig, ax = plt.subplots(figsize=(max(3.5, len(rows) * 1.3), 3.2))
            names = [r["level"].capitalize() for r in rows]
            vals = [r["metric"] for r in rows]
            bars = ax.bar(names, vals, color=[DIFF_COLORS.get(r["level"], "#888") for r in rows],
                          alpha=0.92, width=0.55, edgecolor="white", linewidth=0.6)
            _bar_labels(ax, bars, "{:.2f}")
            ax.set_ylabel(DF["metric_label"]); ax.set_title(DF["metric_label"] + " by difficulty")
            ax.tick_params(axis="x", rotation=0, length=0)
            charts["difficulty_metric"] = _fig_to_uri(fig)
        bc = DF.get("by_criterion")
        if bc and bc["criteria"] and bc["levels"]:
            crits = bc["criteria"]
            x = np.arange(len(crits))
            w = 0.8 / max(1, len(bc["levels"]))
            fig, ax = plt.subplots(figsize=(max(4.5, len(crits) * 1.6), 3.4))
            for k, lv in enumerate(bc["levels"]):
                ys = [(v if v is not None else 0) for v in bc["data"][lv]]
                ax.bar(x + k * w, ys, width=w, label=lv.capitalize(),
                       color=DIFF_COLORS.get(lv, "#888"), alpha=0.92,
                       edgecolor="white", linewidth=0.6)
            ax.set_xticks(x + w * (len(bc["levels"]) - 1) / 2)
            ax.set_xticklabels(crits, rotation=0)
            ax.set_ylabel("Mean score"); ax.set_title("Mean score by criterion and difficulty")
            ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
            charts["difficulty_by_criterion"] = _fig_to_uri(fig)

    return charts
