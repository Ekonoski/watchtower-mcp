"""
The fundamentals tag (2026-08-27, Eric: "should we choose these names
on fundamentals as well as technicals?" — answered per doctrine: it's a
measurable question, so measure it).

Same contract as the cipher and sector tags: stamped on every swing
spec AFTER curation so arming stays blind (a tiebreaker is a gate in
disguise), holes carry reasons, graded on the book's own resolutions.
The stated prior (sector-study precedent): price already embodies most
of what fundamentals know at this horizon — if the tag earns anything,
it is most likely a VETO cell (distress / dilution / imminent earnings)
promoted as a warning, not a quality selector. The tag decides with
data; nobody argues.

Content, from tables the nightly FMP jobs already fill:
  piotroski (0-9), altman_z, their as-of date, and days_to_earnings
  (next report on the calendar). Missing pieces are None with the
  overall reason recorded — *unavailable*, never neutral.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.fundamentals_tag")


def fundamentals_state_for(conn, ticker: str) -> dict:
    """One ticker's tag. Never raises — a lookup failure is a reason."""
    out = {"piotroski": None, "altman_z": None, "scores_asof": None,
           "days_to_earnings": None, "asof": dt.date.today().isoformat()}
    try:
        with conn.cursor() as c:
            c.execute("""SELECT piotroski_score, altman_z_score, as_of_date
                         FROM financial_scores WHERE ticker=%s
                         ORDER BY as_of_date DESC LIMIT 1""", (ticker,))
            r = c.fetchone()
        if r:
            out["piotroski"] = int(r[0]) if r[0] is not None else None
            out["altman_z"] = round(float(r[1]), 2) if r[1] is not None else None
            out["scores_asof"] = str(r[2]) if r[2] is not None else None
        else:
            out["reason"] = "no financial_scores row"
        with conn.cursor() as c:
            c.execute("""SELECT report_date FROM earnings_calendar
                         WHERE ticker=%s AND report_date >= CURRENT_DATE
                         ORDER BY report_date LIMIT 1""", (ticker,))
            e = c.fetchone()
        if e:
            out["days_to_earnings"] = (e[0] - dt.date.today()).days
    except Exception as e:
        return {"piotroski": None, "altman_z": None,
                "reason": f"tag_error: {str(e)[:300]}",
                "asof": dt.date.today().isoformat()}
    return out


def flag_line(tag: dict) -> str:
    """Compact render for logs/ledger. Direction kept, holes stated."""
    if tag.get("reason"):
        return f"unavailable ({tag['reason']})"
    bits = []
    if tag.get("piotroski") is not None:
        bits.append(f"F{tag['piotroski']}")
    if tag.get("altman_z") is not None:
        z = tag["altman_z"]
        bits.append(f"Z{z:g}" + (" ⚠distress" if z < 1.8 else ""))
    if tag.get("days_to_earnings") is not None:
        d = tag["days_to_earnings"]
        bits.append(f"ER{d}d" + (" ⚠inside-hold" if d <= 21 else ""))
    return " ".join(bits) if bits else "unavailable (empty)"
