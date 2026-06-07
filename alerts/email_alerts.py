"""
Watchtower — Email alerts via Resend.
Intraday alerts: sent every 15-30 min during trading hours.
Daily hidden gems: sent once per day (pre-market, ~6 AM ET).
"""
import json
import os
import urllib.request
from datetime import datetime
from typing import List, Optional

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "Watchtower <onboarding@resend.dev>")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")

# Signal type → CSS color
_SIGNAL_COLORS = {
    "GAP_AND_GO":        "#16a34a",
    "INTRADAY_BREAKOUT": "#2563eb",
    "VWAP_BREAKOUT":     "#0d9488",
    "FLUSH_REVERSAL":    "#ea580c",
    "GAP_REVERSAL":      "#7c3aed",
    "VWAP_REJECTION":    "#dc2626",
    "INTRADAY_BREAKDOWN":"#b91c1c",
    "GAP_DOWN_CONFIRM":  "#991b1b",
    "DISTRIBUTION":      "#c2410c",
    "VOLUME_SURGE":      "#ca8a04",
}

# News sentiment → badge color
_NEWS_COLORS = {
    "bullish": "#16a34a",
    "bearish": "#dc2626",
    "neutral": "#6b7280",
}

# News category → readable label
_CATEGORY_LABELS = {
    "earnings_beat":      "Earnings Beat",
    "earnings_miss":      "Earnings Miss",
    "revenue_beat":       "Revenue Beat",
    "revenue_miss":       "Revenue Miss",
    "fda_approval":       "FDA Approval",
    "fda_rejection":      "FDA Rejection",
    "clinical_trial":     "Clinical Trial",
    "merger":             "Merger",
    "acquisition":        "Acquisition",
    "buyout":             "Buyout",
    "takeover":           "Takeover",
    "analyst_initiation": "New Coverage",
    "analyst_upgrade":    "Upgrade",
    "analyst_downgrade":  "Downgrade",
    "contract_win":       "Contract Win",
    "partnership":        "Partnership",
    "product_launch":     "Product Launch",
    "guidance_raise":     "Guidance ↑",
    "guidance_cut":       "Guidance ↓",
    "insider_buying":     "Insider Buy",
    "short_squeeze":      "Short Squeeze",
    "general":            "News",
}


_SIGNAL_BADGE = {
    "STRONG_BUY":  ("#14532d", "STRONG BUY ▲▲"),
    "BUY":         ("#16a34a", "BUY ▲"),
    "WATCH":       ("#2563eb", "WATCH"),
    "NEUTRAL":     ("#6b7280", "NEUTRAL"),
    "AVOID":       ("#9a3412", "AVOID"),
    "SELL":        ("#dc2626", "SELL ▼"),
    "STRONG_SELL": ("#7f1d1d", "STRONG SELL ▼▼"),
}


def _build_news_section(news_alerts: List[dict]) -> str:
    """Build the news alerts HTML section for the intraday email."""
    if not news_alerts:
        return ""

    rows = ""
    synthesis_blocks = ""

    for n in news_alerts:
        ticker = n.get("primary_ticker", "")
        sentiment = n.get("sentiment", "neutral")
        magnitude = n.get("magnitude", "low")
        category = n.get("category", "general")
        one_liner = n.get("one_liner", "")
        price = n.get("price", 0)
        change_pct = n.get("change_pct", 0)
        vol_ratio = n.get("vol_ratio", 1.0)
        publisher = n.get("publisher", "")
        is_off_radar = n.get("is_off_radar", False)
        combined_signal = n.get("combined_signal", "")
        has_synthesis = n.get("has_synthesis", False)

        news_badge_color = _NEWS_COLORS.get(sentiment, "#6b7280")
        cat_label = _CATEGORY_LABELS.get(category, category.replace("_", " ").title())
        change_color = "#16a34a" if change_pct >= 0 else "#dc2626"
        off_radar_badge = (
            '<span style="background:#7c3aed;color:#fff;padding:1px 6px;border-radius:10px;'
            'font-size:10px;font-weight:600;margin-left:4px;">OFF RADAR</span>'
            if is_off_radar else ""
        )
        mag_star = {"high": "★★★", "medium": "★★", "low": "★"}.get(magnitude, "★")
        price_str = f"${price:.2f}" if price else "—"
        vol_str = f"{vol_ratio:.1f}x vol" if vol_ratio and vol_ratio != 1.0 else ""

        # Combined signal badge from Grok synthesis
        if combined_signal and combined_signal in _SIGNAL_BADGE:
            sig_color, sig_label = _SIGNAL_BADGE[combined_signal]
            signal_cell = (
                f'<span style="background:{sig_color};color:#fff;padding:3px 8px;'
                f'border-radius:4px;font-size:11px;font-weight:700;">{sig_label}</span>'
            )
        else:
            signal_cell = f'<span style="color:#ca8a04;">{mag_star}</span>'

        rows += f"""
        <tr style="border-bottom:1px solid #f3f4f6;{'background:#f0fdf4;' if combined_signal in ('STRONG_BUY','BUY') else 'background:#fef2f2;' if combined_signal in ('STRONG_SELL','SELL') else ''}">
          <td style="padding:9px 12px;font-weight:700;font-size:14px;white-space:nowrap;">
            {ticker}{off_radar_badge}
          </td>
          <td style="padding:9px 12px;white-space:nowrap;">
            <span style="background:{news_badge_color};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">
              {cat_label}
            </span>
          </td>
          <td style="padding:9px 12px;white-space:nowrap;">{signal_cell}</td>
          <td style="padding:9px 12px;text-align:right;font-weight:600;">{price_str}</td>
          <td style="padding:9px 12px;text-align:right;color:{change_color};font-weight:600;">{change_pct:+.1f}%</td>
          <td style="padding:9px 12px;text-align:right;color:#6b7280;font-size:12px;">{vol_str}</td>
          <td style="padding:9px 12px;color:#374151;font-size:12px;">{one_liner}</td>
          <td style="padding:9px 12px;color:#9ca3af;font-size:11px;">{publisher}</td>
        </tr>"""

        # Synthesis expansion block for high-conviction signals
        if has_synthesis and combined_signal in ("STRONG_BUY", "BUY", "STRONG_SELL", "SELL", "WATCH"):
            thesis = n.get("thesis", "")
            key_level = n.get("key_level", "")
            risk = n.get("risk", "")
            conviction = n.get("conviction", "")
            sig_color, _ = _SIGNAL_BADGE.get(combined_signal, ("#6b7280", ""))

            if thesis:
                synthesis_blocks += f"""
        <div style="margin:0;padding:12px 16px;background:#f8fafc;border-left:4px solid {sig_color};margin-bottom:8px;">
          <div style="font-weight:700;font-size:13px;color:#111827;margin-bottom:4px;">
            {ticker} — {combined_signal.replace('_',' ')}
            <span style="font-weight:400;color:#6b7280;font-size:12px;margin-left:8px;">({conviction} conviction)</span>
          </div>
          <div style="font-size:13px;color:#374151;margin-bottom:4px;">{thesis}</div>
          {'<div style="font-size:12px;color:#2563eb;font-weight:600;">📍 Key level: ' + key_level + '</div>' if key_level else ''}
          {'<div style="font-size:12px;color:#dc2626;">⚠️ Risk: ' + risk + '</div>' if risk else ''}
        </div>"""

    synthesis_section = ""
    if synthesis_blocks:
        synthesis_section = f"""
    <div style="padding:14px 28px 6px;background:#f8fafc;border-top:1px solid #e5e7eb;">
      <h3 style="margin:0 0 10px;font-size:13px;font-weight:700;color:#374151;">
        🤖 Grok Signal Analysis
      </h3>
      {synthesis_blocks}
    </div>"""

    return f"""
    <!-- News Alerts Section -->
    <div style="padding:14px 28px;background:#fef9f0;border-top:2px solid #fed7aa;">
      <h2 style="margin:0;color:#9a3412;font-size:15px;font-weight:700;">
        📰 News Alerts — Stocks On The Move
      </h2>
      <p style="margin:4px 0 0;color:#c2410c;font-size:12px;">
        {len(news_alerts)} catalyst{'s' if len(news_alerts) != 1 else ''} detected &nbsp;·&nbsp;
        <span style="background:#7c3aed;color:#fff;padding:1px 5px;border-radius:3px;font-size:10px;">OFF RADAR</span>
        = not in your current universe
      </p>
    </div>
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#fff7ed;border-bottom:1px solid #fed7aa;">
            <th style="padding:8px 12px;text-align:left;color:#9a3412;font-weight:600;">Ticker</th>
            <th style="padding:8px 12px;text-align:left;color:#9a3412;font-weight:600;">Catalyst</th>
            <th style="padding:8px 12px;text-align:left;color:#9a3412;font-weight:600;">Grok Signal</th>
            <th style="padding:8px 12px;text-align:right;color:#9a3412;font-weight:600;">Price</th>
            <th style="padding:8px 12px;text-align:right;color:#9a3412;font-weight:600;">Chg%</th>
            <th style="padding:8px 12px;text-align:right;color:#9a3412;font-weight:600;">Volume</th>
            <th style="padding:8px 12px;text-align:left;color:#9a3412;font-weight:600;">What Happened</th>
            <th style="padding:8px 12px;text-align:left;color:#9a3412;font-weight:600;">Source</th>
          </tr>
        </thead>
        <tbody>{rows}
        </tbody>
      </table>
    </div>
    {synthesis_section}"""


def _build_html(results: List[dict], minutes_elapsed: int, is_market_hours: bool,
                news_alerts: Optional[List[dict]] = None) -> str:
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

        # Social buzz sub-row
        buzz = r.get("social_buzz", {})
        buzz_html = ""
        if buzz and buzz.get("summary"):
            buzz_sentiment = buzz.get("sentiment", "neutral")
            buzz_score = buzz.get("sentiment_score", 0.0)
            buzz_summary = buzz.get("summary", "")
            buzz_level = buzz.get("buzz_level", "low")
            buzz_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(buzz_sentiment, "⚪")
            buzz_level_icon = {"high": "🔥", "medium": "📢", "low": "💤"}.get(buzz_level, "")
            buzz_html = f"""
        <tr style="background:#f8fafc;border-bottom:1px solid #e5e7eb;">
          <td colspan="8" style="padding:4px 10px 8px 28px;font-size:11px;color:#6b7280;">
            {buzz_emoji} X/Social: <strong>{buzz_sentiment}</strong> ({buzz_score:+.2f}) {buzz_level_icon} — {buzz_summary}
          </td>
        </tr>"""

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
        {buzz_html}"""

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

    {_build_news_section(news_alerts or [])}

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
    news_alerts: Optional[List[dict]] = None,
) -> bool:
    """
    Format and send an intraday alert email via Resend.

    Returns True if the email was sent successfully, False otherwise.
    Fails silently if env vars are missing or both results and news are empty.
    """
    if not RESEND_API_KEY or not ALERT_EMAIL_TO:
        return False
    if not results and not news_alerts:
        return False

    try:
        import pytz
        et = pytz.timezone("America/New_York")
        time_str = datetime.now(et).strftime("%I:%M %p ET")
    except Exception:
        time_str = datetime.utcnow().strftime("%H:%M UTC")

    n = len(results)
    n_news = len(news_alerts) if news_alerts else 0
    news_suffix = f" + {n_news} news catalyst{'s' if n_news != 1 else ''}" if n_news else ""
    subject = f"Watchtower Alert — {n} setup{'s' if n != 1 else ''}{news_suffix} | {time_str}"
    html = _build_html(results, minutes_elapsed, is_market_hours, news_alerts=news_alerts)

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

        # Social buzz sub-row
        buzz = r.get("social_buzz", {})
        buzz_html = ""
        if buzz and buzz.get("summary"):
            buzz_sentiment = buzz.get("sentiment", "neutral")
            buzz_score = buzz.get("sentiment_score", 0.0)
            buzz_summary = buzz.get("summary", "")
            buzz_level = buzz.get("buzz_level", "low")
            buzz_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(buzz_sentiment, "⚪")
            buzz_level_icon = {"high": "🔥", "medium": "📢", "low": "💤"}.get(buzz_level, "")
            buzz_html = f"""
        <tr style="background:#f8fafc;border-bottom:1px solid #e5e7eb;">
          <td colspan="8" style="padding:3px 12px 10px 28px;font-size:11px;color:#6b7280;">
            {buzz_emoji} X/Social: <strong>{buzz_sentiment}</strong> ({buzz_score:+.2f}) {buzz_level_icon} — {buzz_summary}
          </td>
        </tr>"""

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
        {buzz_html}"""

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
