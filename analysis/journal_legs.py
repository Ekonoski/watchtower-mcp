"""
The journal's LEGS vocabulary (2026-09-04, Eric: "That list works, build
it this weekend"). Every journal row — trade or skip — carries the tags
for the reasons the eye used, drawn from THIS fixed list and nothing
else, so that at ~30 rows the grade is per leg (win rate and R with vs
without each tag), exactly the cipher-study mechanism applied to Eric's
own book. A leg that separates outcomes in both halves of the sample
becomes a candidate rule; nothing here is a gate.

Each tag is either CHECKABLE (the record can verify it — boards, ranks,
the calendar) or EYE-ONLY (graded on outcomes alone); the render says
which. The vocabulary changes only at a flat, market-closed review —
never after a loss — and unknown tags are refused at the door, because
a tag typed once by feel would grade as its own category forever.
"""

# tag -> (group, checkable, meaning)
VOCAB = {
    # entry legs — why now
    "confirmed":        ("entry", False, "the level/trend had resolved on a close before entry"),
    "anticipated":      ("entry", False, "entered ahead of the resolution"),
    "retest_held":      ("entry", False, "entered on a retest that held (name the level in the note)"),
    "lower_high":       ("entry", False, "structure: a lower high justified the short"),
    "higher_low":       ("entry", False, "structure: a higher low justified the long"),
    "ema_flip":         ("entry", False, "8/21 flip on the decision timeframe, with retest"),
    "macd_cross":       ("entry", False, "MACD cross in the trade's direction"),
    "cipher_div":       ("entry", False, "Market Cipher B divergence in the trade's direction"),
    "system_alert":     ("entry", True,  "the entry came off a desk ping (link spec_id)"),
    # context legs — what kind of day
    "on_flip":          ("context", True, "index within 0.15% of its gamma flip at entry"),
    "slippery":         ("context", True, "board regime at entry: slippery"),
    "pinning":          ("context", True, "board regime at entry: pinning"),
    "low_gex":          ("context", True, "net GEX inside ±1bn at entry (battleground)"),
    "pre_holiday":      ("context", True, "session before a market holiday"),
    "binary_day":       ("context", True, "scheduled binary (CPI/NFP/FOMC) on the day"),
    "chop_expected":    ("context", False, "the eye expected chop"),
    "leader":           ("context", True, "the name ranked #1 at 9:45"),
    "midpack":          ("context", True, "the name ranked mid-pack at 9:45"),
    "laggard":          ("context", True, "the name ranked last at 9:45"),
    # exit legs — how it was managed
    "partial_at_level":   ("exit", False, "first profit taken at a named level"),
    "exit_at_level":      ("exit", False, "full exit at a named level"),
    "runner_next_level":  ("exit", False, "runner exited at the next level"),
    "runner_breakeven":   ("exit", False, "runner stopped at entry"),
    "runner_ratchet":     ("exit", False, "runner's stop walked under structure"),
    "stopped_close":      ("exit", False, "stop honored on a completed close"),
    "stopped_touch":      ("exit", False, "stop honored on a touch"),
    "no_partial":         ("exit", False, "a green trade with nothing banked"),
    # skip reasons — skips only
    "into_highs":       ("skip", False, "pushing into all-time/recent highs"),
    "low_volume":       ("skip", True,  "holiday/thin participation expected"),
    "bearish_ind":      ("skip", False, "bearish indications on the indexes"),
    "no_leader":        ("skip", True,  "no name cleared the 9:45 leader bar"),
}

GROUPS = ("entry", "context", "exit", "skip")


def normalize(legs) -> list:
    """Pure. Accepts a list or a comma/whitespace-separated string; lower-
    cases, de-duplicates in order, REFUSES unknown tags by name. [] -> None."""
    if not legs:
        return None
    if isinstance(legs, str):
        legs = legs.replace(",", " ").split()
    out, unknown = [], []
    for raw in legs:
        t = str(raw).strip().lower()
        if not t:
            continue
        if t not in VOCAB:
            unknown.append(t)
        elif t not in out:
            out.append(t)
    if unknown:
        raise ValueError(f"unknown leg(s) {unknown} — the vocabulary is fixed: "
                         f"{', '.join(sorted(VOCAB))}. New tags are added at a "
                         f"flat review, never on the fly.")
    return out or None


def leg_grade(rows, min_n: int = 1) -> list:
    """Pure. rows: iterable of (legs, r_multiple) for CLOSED trades (r not
    None). Returns one dict per tag present, sorted worst spread first:
    n_with, wins_with, avg_with, n_without, avg_without, spread. Both
    sides are stated so a tag's number never renders alone."""
    closed = [(set(l or []), float(r)) for l, r in rows if r is not None]
    tags = sorted({t for l, _ in closed for t in l})
    out = []
    for t in tags:
        w = [r for l, r in closed if t in l]
        wo = [r for l, r in closed if t not in l]
        if len(w) < min_n:
            continue
        aw = sum(w) / len(w)
        awo = (sum(wo) / len(wo)) if wo else None
        out.append({
            "tag": t, "group": VOCAB[t][0], "checkable": VOCAB[t][1],
            "n_with": len(w), "wins_with": sum(1 for r in w if r > 0), "avg_with": aw,
            "n_without": len(wo), "avg_without": awo,
            "spread": (aw - awo) if awo is not None else None,
        })
    out.sort(key=lambda d: (d["spread"] if d["spread"] is not None else 0.0))
    return out


def render_grade(grades, total_closed: int) -> list:
    """Lines for the journal: per-leg with/without, small-n stated."""
    if not grades:
        return ["", "By leg: no closed trades carry legs yet."]
    lines = ["", f"By leg (closed trades, n={total_closed} — below ~30 every line "
                 f"is anecdote; a leg becomes a rule only when it separates "
                 f"outcomes in both halves of the sample):"]
    for g in grades:
        kind = "record-checked" if g["checkable"] else "eye-only"
        wo = (f"without {g['avg_without']:+.2f}R (n={g['n_without']})"
              if g["avg_without"] is not None else "without: none")
        sp = f" · spread {g['spread']:+.2f}R" if g["spread"] is not None else ""
        lines.append(f"  {g['tag']} [{g['group']}, {kind}]: with {g['avg_with']:+.2f}R "
                     f"{g['wins_with']}W/{g['n_with'] - g['wins_with']}L (n={g['n_with']}) · {wo}{sp}")
    return lines
