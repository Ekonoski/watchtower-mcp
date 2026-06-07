"""
Watchtower — Email alerts via Resend.
Intraday alerts: sent every 15-30 min during trading hours.
Daily hidden gems: sent once per day (pre-market, ~6 AM ET).
"""
import json
import os
import urllib.request
from datetime import datetime
from typing import List

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "Watchtower <onboarding@resend.dev>")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")

# Signal type → CSS color
_SIGNAL_COLORS = {
    "GAP_AND_GO":        "#16a34a",  # green
    "INTRADAY_BREAKOUT": "#2563eb",  # blue
    "VWAP_BREAKOUT":     "#0d9488",  # teal
    "FLUSH_REVERSAL":    "#ea580c",  # orange
    "GAP_REVERSAL":      "#7c3aed",  # purple
    "VOLUME_SURGE":      "#ca8a04",  # yellow/amber
}


def _build_html(results: List[dict], minutes_elapsed: int, is_market_hours: bool) -> str:
    """Build the HTML email body."""
    try:
        import pytz
        et = pytz.timezone("America/New_York")
        now_et = datetime.now(et)
        time_str = now_et.strftime("%I:%M %p ET")
        date_str = now_et.strftime("%A, %B %-d, %Y")
    except Exception:
        now_et = datetime.utcnow()
        time_str = now_et.strftime("%H:%M UTC")
        date_str = now_et.strftime("%Y-%m-%d")

    market_status = "Market Open" if is_market_hours else "Market Closed"
    session_note = (
        f"{minutes_elapsed} min into session" if is_market_hours
        else "Showing last-session snapshot data"
    )

    rows_html = ""
    for r in results:
        signal = r.get("signal_type", "")
        color = _SIGNAL_COLORS.get(signal, "#6b7280")
        ticker = r.get("ticker", "")
        score = r.get("score", 0)
        change_pct = r.get("change_pct", 0)
        change_str = f"{change_pct:+.1f}%"
        vol_pace = r.get("vol_pace_ratio", 0)
        vwap_flag = "↑" if r.get("above_vwap") else "↓"
        vwap = r.get("vwap", 0)
        price = r.get("current_price", 0)
        rationale = r.get("rationale", "")

        rows_html += f"""
        <tr>
          <td style="padding:8px 10px;font-weight:bold;font-size:14px;">{ticker}</td>
          <td style="padding:8px 10px;">
            <span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;">
              {signal}
            </span>
          </td>
          <td style="padding:8px 10px;text-align:right;">{score:.0f}</td>
          <td style="padding:8px 10px;text-align:right;">{change_str}</td>
          <td style="padding:8px 10px;text-align:right;">{vol_pace:.1f}x</td>
          <td style="padding:8px 10px;text-align:right;">{vwap_flag}${vwap:.2f}</td>
          <td style="padding:8px 10px;text-align:right;">${price:.2f}</td>
          <td style="padding:8px 10px;color:#6b7280;font-size:13px;">{rationale}</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Watchtower Intraday Alert</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:900px;margin:24px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">

    <!-- Header -->
    <div style="background:#111827;padding:20px 28px;">
      <h1 style="margin:0;color:#f9fafb;font-size:20px;font-weight:700;">
        📡 Watchtower Intraday Alert
      </h1>
      <p style="margin:6px 0 0;color:#9ca3af;font-size:13px;">
        {date_str} &nbsp;·&nbsp; {time_str} &nbsp;·&nbsp; {market_status} &nbsp;·&nbsp; {session_note}
      </p>
    </div>

    <!-- Summary -->
    <div style="padding:16px 28px;background:#f9fafb;border-bottom:1px solid #e5e7eb;">
      <p style="margin:0;color:#374151;font-size:14px;">
        <strong>{len(results)} setup{'s' if len(results) != 1 else ''}</strong> found above threshold.
      </p>
    </div>

    <!-- Table -->
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb;">
            <th style="padding:10px;text-align:left;color:#374151;font-weight:600;">Ticker</th>
            <th style="padding:10px;text-align:left;color:#374151;font-weight:600;">Signal</th>
            <th style="padding:10px;text-align:right;color:#374151;font-weight:600;">Score</th>
            <th style="padding:10px;text-align:right;color:#374151;font-weight:600;">Change%</th>
            <th style="padding:10px;text-align:right;color:#374151;font-weight:600;">Vol Pace</th>
            <th style="padding:10px;text-align:right;color:#374151;font-weight:600;">VWAP</th>
            <th style="padding:10px;text-align:right;color:#374151;font-weight:600;">Price</th>
            <th style="padding:10px;text-align:left;color:#374151;font-weight:600;">Rationale</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    <!-- Footer -->
    <div style="padding:16px 28px;background:#f9fafb;border-top:1px solid #e5e7eb;">
      <p style="margin:0;color:#9ca3af;font-size:12px;">
        15-min delayed &nbsp;·&nbsp; Watchtower GMMSS &nbsp;·&nbsp; Not financial advice
      </p>
    </div>

  </div>
</body>
</html>"""
    return html


def send_intraday_alert(
    results: List[dict],
    minutes_elapsed: int = 0,
    is_market_hours: bool = True,
) -> bool:
    """
    Format and send an intraday alert email via Resend.

    Returns True if the email was sent successfully, False otherwise.
    Fails silently if env vars are missing or results are empty.
    """
    if not RESEND_API_KEY or not ALERT_EMAIL_TO:
        return False
    if not results:
        return False

    try:
        import pytz
        et = pytz.timezone("America/New_York")
        time_str = datetime.now(et).strftime("%I:%M %p ET")
    except Exception:
        time_str = datetime.utcnow().strftime("%H:%M UTC")

    n = len(results)
    subject = f"Watchtower Intraday Alert — {n} setup{'s' if n != 1 else ''} | {time_str}"
    html = _build_html(results, minutes_elapsed, is_market_hours)

    payload = {
        "from": RESEND_FROM,
        "to": [ALERT_EMAIL_TO],
        "subject": subject,
        "html": html,
    }

    try:
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def _build_gems_html(results: List[dict]) -> str:
    """Build HTML email body for daily hidden gems / up-and-comer report."""
    try:
        import pytz
        et = pytz.timezone("America/New_York")
        now_et = datetime.now(et)
        date_str = now_et.strftime("%A, %B %-d, %Y")
    except Exception:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    rows_html = ""
    for r in results:
        ticker = r.get("ticker", "")
        score = r.get("score", 0)
        price = r.get("current_price", 0)
        hi_52w = r.get("hi_52w", 0)
        dd = r.get("drawdown_pct", 0)
        rsi = r.get("rsi") or 0
        rationale = r.get("rationale", "")
        sector = (r.get("sector") or "")[:22]
        pt = r.get("price_target_avg")
        rev_qoq = r.get("revenue_growth_qoq")
        pio = r.get("piotroski_score")

        upside_str = ""
        if pt and price > 0:
            upside = (pt - price) / price * 100
            upside_str = f"<span style='color:#16a34a;font-weight:600;'> ↑{upside:.0f}% to PT</span>"

        fund_str = ""
        if rev_qoq is not None:
            fund_str = f"Rev QoQ: {rev_qoq*100:+.0f}%"
        if pio is not None:
            fund_str += f" | Piotroski: {pio}/9"

        # Score badge color
        if score >= 60:
            badge_color = "#16a34a"
        elif score >= 45:
            badge_color = "#2563eb"
        else:
            badge_color = "#6b7280"

        rows_html += f"""
        <tr style="border-bottom:1px solid #f3f4f6;">
          <td style="padding:10px 12px;font-weight:700;font-size:15px;color:#111827;">{ticker}</td>
          <td style="padding:10px 12px;">
            <span style="background:{badge_color};color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;">
              {score:.0f}
            </span>
          </td>
          <td style="padding:10px 12px;text-align:right;font-weight:600;">${price:.2f}</td>
          <td style="padding:10px 12px;text-align:right;color:#dc2626;">{dd:.0f}% off hi</td>
          <td style="padding:10px 12px;text-align:right;">{rsi:.0f}</td>
          <td style="padding:10px 12px;color:#6b7280;font-size:12px;">{sector}</td>
          <td style="padding:10px 12px;font-size:12px;">{rationale}{upside_str}</td>
          <td style="padding:10px 12px;font-size:11px;color:#9ca3af;">{fund_str}</td>
        </tr>
        """

    synthesis_html = ""
    if results and results[0].get("synthesis"):
        synthesis_html = f"""
    <div style="padding:20px 28px;background:#fefce8;border-top:1px solid #e5e7eb;">
      <h3 style="margin:0 0 8px;color:#92400e;font-size:14px;font-weight:700;">Grok Analysis</h3>
      <p style="margin:0;color:#451a03;font-size:13px;line-height:1.6;white-space:pre-wrap;">{results[0]['synthesis']}</p>
    </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Watchtower Hidden Gems</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:960px;margin:24px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">

    <div style="background:#1e1b4b;padding:20px 28px;">
      <h1 style="margin:0;color:#f9fafb;font-size:20px;font-weight:700;">
        💎 Watchtower Hidden Gems
      </h1>
      <p style="margin:6px 0 0;color:#a5b4fc;font-size:13px;">
        {date_str} &nbsp;·&nbsp; Daily Up-and-Comer Scan &nbsp;·&nbsp; Full US Market Universe
      </p>
    </div>

    <div style="padding:14px 28px;background:#f5f3ff;border-bottom:1px solid #e5e7eb;">
      <p style="margin:0;color:#374151;font-size:14px;">
        <strong>{len(results)} hidden gem{'s' if len(results) != 1 else ''}</strong> found —
        stocks 18-60% off highs, breaking out of bases, with 10x potential.
        Not your mainstream momentum names.
      </p>
    </div>

    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb;">
            <th style="padding:10px 12px;text-align:left;color:#374151;font-weight:600;">Ticker</th>
            <th style="padding:10px 12px;text-align:left;color:#374151;font-weight:600;">Score</th>
            <th style="padding:10px 12px;text-align:right;color:#374151;font-weight:600;">Price</th>
            <th style="padding:10px 12px;text-align:right;color:#374151;font-weight:600;">Drawdown</th>
            <th style="padding:10px 12px;text-align:right;color:#374151;font-weight:600;">RSI</th>
            <th style="padding:10px 12px;text-align:left;color:#374151;font-weight:600;">Sector</th>
            <th style="padding:10px 12px;text-align:left;color:#374151;font-weight:600;">Signal</th>
            <th style="padding:10px 12px;text-align:left;color:#374151;font-weight:600;">Fundamentals</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    {synthesis_html}

    <div style="padding:16px 28px;background:#f9fafb;border-top:1px solid #e5e7eb;">
      <p style="margin:0;color:#9ca3af;font-size:12px;">
        15-min delayed data &nbsp;·&nbsp; Watchtower GMMSS &nbsp;·&nbsp; Not financial advice &nbsp;·&nbsp;
        These are early-stage candidates — do your own due diligence.
      </p>
    </div>

  </div>
</body>
</html>"""


def send_hidden_gems_alert(results: List[dict]) -> bool:
    """
    Send the daily hidden gems / up-and-comer email via Resend.
    Intended to run once per day, pre-market (~6 AM ET).
    Returns True if sent successfully.
    """
    if not RESEND_API_KEY or not ALERT_EMAIL_TO:
        return False
    if not results:
        return False

    try:
        import pytz
        et = pytz.timezone("America/New_York")
        date_str = datetime.now(et).strftime("%b %-d")
    except Exception:
        date_str = datetime.utcnow().strftime("%b %d")

    n = len(results)
    subject = f"💎 Watchtower Hidden Gems — {n} up-and-comer{'s' if n != 1 else ''} | {date_str}"
    html = _build_gems_html(results)

    payload = {
        "from": RESEND_FROM,
        "to": [ALERT_EMAIL_TO],
        "subject": subject,
        "html": html,
    }

    try:
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:
        return False
