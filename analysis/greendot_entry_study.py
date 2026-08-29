"""
The green-dot ENTRY-SCHEDULE study (2026-08-29, Eric: "enter on the
green dot and DCA until it turns?" → bounded tranches, and his HA
refinement: "the heiken ashi candles usually give a clear doji and
then a green candle to show the change of trend" on the 16D chart —
to skip the post-dot drawdown the main study measured at 17-25%).

Pre-registered variants, graded per dot on REAL closes at real signal
dates (HA values are displays, never fill prices — phantom doctrine):

  dot_lump    all-in at the dot bar's close (baseline)
  ladder      1/3 at dot, 1/3 if -15% touches, 1/3 if -25% touches
              (daily lows inside the 6-mo window; avg px of DEPLOYED
              capital, deployed fraction reported beside it)
  raw_green   first raw 16D bar closing green after the dot
  ha_doji_any Eric's rule: first HA doji (body <= 15% of range) then a
              green HA bar; enter at that bar's real close
  ha_doji_brk stricter cousin: the green bar must also CLOSE above the
              doji bar's HA high

Trigger window: 8 blocks (~6 months) after the dot; a variant that
never fires records no_entry with the reason (missed_runner if price
sits above the dot at window end, still_falling if below). Outcomes
per entered variant: MAE after entry (the metric Eric is optimizing),
fwd 6/12-mo from ENTRY, all from daily closes. One-shot fleet pass,
resume by ticker, marker greendot_entry_v1. Writes only its own table.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.greendot_entry")

COMPLETE_MARKER = "greendot_entry_v1"
DOJI_BODY_MAX = 0.15
WINDOW_BLOCKS = 8
LADDER = (0.0, -0.15, -0.25)


def heikin_ashi(bars):
    """Pure. bars: [{o,h,l,c}] → [{ho,hh,hl,hc}] classic recursion."""
    out = []
    for i, b in enumerate(bars):
        hc = (b["o"] + b["h"] + b["l"] + b["c"]) / 4
        ho = (b["o"] + b["c"]) / 2 if i == 0 else \
            (out[-1]["ho"] + out[-1]["hc"]) / 2
        out.append({"ho": ho, "hc": hc,
                    "hh": max(b["h"], ho, hc), "hl": min(b["l"], ho, hc)})
    return out


def is_doji(ha):
    rng = ha["hh"] - ha["hl"]
    return rng > 0 and abs(ha["hc"] - ha["ho"]) <= DOJI_BODY_MAX * rng


def find_ha_entry(ha, start, end, require_break):
    """Pure. First doji-then-green sequence in (start, end]; returns the
    GREEN bar's index or None. require_break: green HA close must clear
    the doji bar's HA high."""
    for i in range(start + 1, min(end, len(ha) - 1)):
        if is_doji(ha[i]):
            g = ha[i + 1]
            if g["hc"] > g["ho"] and \
               (not require_break or g["hc"] > ha[i]["hh"]):
                return i + 1
    return None


def find_raw_green(bars, start, end):
    for i in range(start + 1, min(end + 1, len(bars))):
        if bars[i]["c"] > bars[i]["o"]:
            return i
    return None


def run(batch: int = 400) -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("""SELECT trade_date FROM daily_prices
                         WHERE ticker='SPY' ORDER BY trade_date""")
            cal = {r[0]: i for i, r in enumerate(c.fetchall())}
            c.execute("""SELECT DISTINCT g.ticker FROM greendot_dots g
                         WHERE NOT EXISTS (SELECT 1 FROM greendot_entry e
                                           WHERE e.dot_id = g.id)
                         ORDER BY g.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                          "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                          (COMPLETE_MARKER,))
            conn.commit()
            log.info("[greendot-entry] complete.")
            return True
        for tk in todo:
            try:
                _one_ticker(conn, tk, cal)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot-entry] %s failed: %s", tk, str(e)[:300])
                # Poison-pill guard: record a hole row per dot so the
                # ticker doesn't loop forever.
                with conn.cursor() as c:
                    c.execute("""INSERT INTO greendot_entry
                                 (dot_id, variant, entered, note)
                                 SELECT id, 'dot_lump', false,
                                        'ticker_error'
                                 FROM greendot_dots WHERE ticker=%s
                                 ON CONFLICT DO NOTHING""", (tk,))
                conn.commit()
        log.info("[greendot-entry] processed %d ticker(s).", len(todo))
        return False
    finally:
        conn.close()


def _one_ticker(conn, tk, cal):
    from analysis.greendot_study import blocks_16d
    with conn.cursor() as c:
        c.execute("""SELECT id, dot_date, px_at_dot FROM greendot_dots
                     WHERE ticker=%s ORDER BY dot_date""", (tk,))
        dots = c.fetchall()
        c.execute("""SELECT trade_date, COALESCE(open, close),
                            COALESCE(high, close), COALESCE(low, close), close
                     FROM daily_prices
                     WHERE ticker=%s AND close IS NOT NULL
                     ORDER BY trade_date""", (tk,))
        rows = c.fetchall()
    if not dots or len(rows) < 100:
        return
    dates = [r[0] for r in rows]
    daily = [dict(o=float(r[1]), h=float(r[2]), l=float(r[3]),
                  c=float(r[4])) for r in rows]
    blk = blocks_16d(dates, cal)
    bars, bar_end_di, cur, cur_id = [], [], None, None
    for i, d in enumerate(daily):
        if blk[i] != cur_id:
            if cur is not None:
                bars.append(cur)
                bar_end_di.append(cur["di"])
            cur_id = blk[i]
            cur = dict(o=d["o"], h=d["h"], l=d["l"], c=d["c"], di=i)
        else:
            cur.update(h=max(cur["h"], d["h"]), l=min(cur["l"], d["l"]),
                       c=d["c"], di=i)
    ha = heikin_ashi(bars)
    date_to_di = {d: i for i, d in enumerate(dates)}

    def outcomes(entry_di, entry_px):
        seg = [d["c"] for d in daily[entry_di + 1: entry_di + 127]]
        mae = round((min(seg) / entry_px - 1) * 100, 2) if seg else None
        f6 = round((daily[entry_di + 126]["c"] / entry_px - 1) * 100, 2) \
            if entry_di + 126 < len(daily) else None
        f12 = round((daily[entry_di + 252]["c"] / entry_px - 1) * 100, 2) \
            if entry_di + 252 < len(daily) else None
        return mae, f6, f12

    for did, d0, px0 in dots:
        px0 = float(px0)
        di0 = date_to_di.get(d0)
        if di0 is None:
            continue
        b0 = None
        for bi, e in enumerate(bar_end_di):
            if e >= di0:
                b0 = bi
                break
        if b0 is None:
            continue
        b_end = b0 + WINDOW_BLOCKS
        win_end_di = min(bar_end_di[b_end] if b_end < len(bar_end_di)
                         else len(daily) - 1, len(daily) - 1)
        results = []
        # dot_lump
        results.append(("dot_lump", di0, px0, 1.0, None))
        # ladder — tranche fills from daily lows inside the window
        fills, weight = [px0], 1.0
        for lvl in LADDER[1:]:
            tpx = px0 * (1 + lvl)
            hit = next((i for i in range(di0 + 1, win_end_di + 1)
                        if daily[i]["l"] <= tpx), None)
            if hit is not None:
                fills.append(tpx)
        avg = sum(fills) / len(fills)
        # entry date for outcome purposes = last fill's date or dot date
        results.append(("ladder", di0, round(avg, 4),
                        round(len(fills) / 3, 2), None))
        # raw_green
        gi = find_raw_green(bars, b0, b_end)
        if gi is not None:
            edi = bar_end_di[gi]
            results.append(("raw_green", edi, daily[edi]["c"], 1.0, None))
        else:
            results.append(("raw_green", None, None, 0.0,
                            _miss(daily, di0, win_end_di, px0)))
        # ha variants
        for name, req in (("ha_doji_any", False), ("ha_doji_brk", True)):
            hi_ = find_ha_entry(ha, b0, b_end, req)
            if hi_ is not None and hi_ < len(bar_end_di):
                edi = bar_end_di[hi_]
                results.append((name, edi, daily[edi]["c"], 1.0, None))
            else:
                results.append((name, None, None, 0.0,
                                _miss(daily, di0, win_end_di, px0)))
        with conn.cursor() as c:
            for name, edi, epx, dep, note in results:
                if edi is None or epx is None:
                    c.execute("""INSERT INTO greendot_entry
                        (dot_id, variant, entered, note)
                        VALUES (%s,%s,false,%s) ON CONFLICT DO NOTHING""",
                        (did, name, note))
                    continue
                mae, f6, f12 = outcomes(edi, float(epx))
                c.execute("""INSERT INTO greendot_entry
                    (dot_id, variant, entered, entry_date, entry_px,
                     deployed_frac, mae_pct, fwd6m_pct, fwd12m_pct)
                    VALUES (%s,%s,true,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""",
                    (did, name, dates[edi], round(float(epx), 4), dep,
                     mae, f6, f12))
    conn.commit()


def _miss(daily, di0, win_end_di, px0):
    if win_end_di <= di0:
        return "no_entry: window beyond recorded history"
    return ("no_entry: missed_runner"
            if daily[win_end_di]["c"] > px0 else "no_entry: still_falling")
