"""
The AI-capex basket breadth line (2026-08-29, Eric's taxonomy from the
weekend the complex started rolling: "msft really runs more like a
SaaS company these days and so does pltr. nvda is the only name that
is an ai company there in how it moves"). The basket is the names
whose PRICE is the AI capex cycle — the chip, memory, power, and
networking layer plus the bellwether — deliberately excluding the
software platforms (MSFT, PLTR) whose multiples trade on recurring
revenue, and the ad/cloud megacaps (META, GOOGL, AMZN) which are
their own question.

Renders one breadth read from the stored oscillator scan: how many of
the basket's weekly wavetrends are rolling lower (wt_diff < 0), how
many carry a red weekly MACD histogram (the confirmation layer), how
many daily money flows are negative, and how many live bearish
structures the pattern scanner holds at <= 8% from trigger. Per-row
bar stamps (freshness rule); a missing row is a named hole, never a
silent skip. NVDA is broken out on its own line — the complex cracks
bottom-up and the bellwether decides last.

Read-only over every table it touches.
"""
import logging

log = logging.getLogger("watchtower.ai_capex")

# Eric's taxonomy, 2026-08-29. Membership changes are doctrine edits,
# not tuning knobs.
AI_CAPEX = ("NVDA", "AVGO", "AMD", "MU", "TSM", "ARM",
            "VRT", "SMCI", "ANET", "CRWV")
BELLWETHER = "NVDA"


def basket_read() -> str:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT DISTINCT ON (ticker, timeframe)
                                ticker, timeframe, bar_ts::date,
                                wt_diff, macd_hist, mf_candle
                         FROM oscillator_scan
                         WHERE ticker = ANY(%s)
                           AND timeframe IN ('weekly','daily')
                         ORDER BY ticker, timeframe, bar_ts DESC""",
                      (list(AI_CAPEX),))
            osc = {(r[0], r[1]): r for r in c.fetchall()}
            c.execute("""SELECT ticker, timeframe, pattern, status,
                                round(dist_to_trigger_pct::numeric, 1)
                         FROM pattern_scan
                         WHERE ticker = ANY(%s) AND direction='bearish'
                           AND scanned_at > now() - interval '3 days'
                           AND dist_to_trigger_pct BETWEEN -5 AND 8
                         ORDER BY ticker""", (list(AI_CAPEX),))
            structs = c.fetchall()
    finally:
        conn.close()

    rolling, red_macd, mf_neg, holes = [], [], [], []
    lines = []
    for tk in AI_CAPEX:
        w = osc.get((tk, "weekly"))
        d = osc.get((tk, "daily"))
        if w is None:
            holes.append(tk)
            continue
        _, _, wbar, wdiff, macdh, _ = w
        dmf = float(d[5]) if d is not None and d[5] is not None else None
        tags = []
        if wdiff is not None and float(wdiff) < 0:
            rolling.append(tk)
            tags.append(f"waves down {float(wdiff):+.1f}")
        if macdh is not None and float(macdh) < 0:
            red_macd.append(tk)
            tags.append("macd red")
        if dmf is not None and dmf < 0:
            mf_neg.append(tk)
            tags.append(f"daily mf {dmf:+.1f}")
        n_str = sum(1 for s in structs if s[0] == tk)
        if n_str:
            tags.append(f"{n_str} bearish structure(s) near trigger")
        state = "; ".join(tags) if tags else "constructive"
        mark = " [BELLWETHER]" if tk == BELLWETHER else ""
        lines.append(f"  {tk}{mark} · wk bar {wbar} · {state}")

    n = len(AI_CAPEX) - len(holes)
    head = (f"AI-CAPEX BASKET ({n} of {len(AI_CAPEX)} readable) — "
            f"weekly waves rolling lower: {len(rolling)}/{n} · "
            f"weekly MACD red: {len(red_macd)}/{n} · "
            f"daily money flow negative: {len(mf_neg)}/{n} · "
            f"bearish structures ≤8% from trigger: {len(structs)}")
    tail = ["",
            "Read: the complex cracks bottom-up — power/memory/networking"
            " first, the bellwether last. Warnings on longs, never short"
            " entries (shorts retired 2026-08-08). Basket = Eric's"
            " taxonomy 2026-08-29; MSFT/PLTR excluded as software,"
            " META/GOOGL/AMZN a separate question."]
    if holes:
        tail.insert(0, f"  unavailable (no weekly scan row): "
                       f"{', '.join(holes)}")
    return "\n".join([head, ""] + lines + tail)
