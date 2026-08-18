"""
Watchtower → Discord notification pipe (2026-08-18).

Discord is the delivery channel, not a bot: each stream posts to a plain
channel webhook. Design rules:

- A missing webhook is a CONFIGURED-OFF state, not an error. Every
  caller no-ops cheaply (one info line per process per channel) so the
  whole feature ships dark and lights up the moment the env var lands.
- Delivery is at-most-once per (kind, ref): discord_notify_log claims
  the row before posting, so two scheduler containers can't double-post
  (same family as scheduler_job_claims). A failed post stays in the log
  with delivered=false — a lost alert is visible, never silent.
- Alert text is data for a human mid-session: stamp times in ET, name
  the levels and the reference, keep the direction. House rules apply
  to alerts too.

Env:
  DISCORD_WEBHOOK_GAMMA  — #gamma-drift stream
  DISCORD_WEBHOOK_DESK   — #desk (paper desk fills/exits/settles)
  DISCORD_WEBHOOK_URL    — fallback for any channel without its own
"""
import logging
import os

log = logging.getLogger("watchtower.discord")

CHANNELS = {
    "gamma": "DISCORD_WEBHOOK_GAMMA",
    "desk": "DISCORD_WEBHOOK_DESK",
}
FALLBACK_ENV = "DISCORD_WEBHOOK_URL"

# Discord hard-caps content at 2000 chars; leave room for the marker.
MAX_LEN = 1900

_warned_off = set()


def webhook_for(channel: str):
    """The webhook URL for a channel, or None when unconfigured."""
    url = os.environ.get(CHANNELS.get(channel, ""), "").strip()
    if not url:
        url = os.environ.get(FALLBACK_ENV, "").strip()
    return url or None


def is_configured(channel: str) -> bool:
    return webhook_for(channel) is not None


def post_discord(channel: str, content: str) -> bool:
    """POST one message to the channel's webhook. True on 2xx.
    Returns False (never raises) when unconfigured or on any failure."""
    url = webhook_for(channel)
    if not url:
        if channel not in _warned_off:
            _warned_off.add(channel)
            log.info(f"[discord] channel '{channel}' not configured — stream off.")
        return False
    if len(content) > MAX_LEN:
        content = content[: MAX_LEN - 30] + "\n… (truncated — full text in logs)"
    try:
        import requests
        r = requests.post(url, json={"content": content}, timeout=10)
        if r.status_code in (200, 204):
            return True
        # Keep >=300 chars of the body: error text must explain itself.
        log.warning(f"[discord] '{channel}' post failed HTTP {r.status_code}: "
                    f"{r.text[:400]}")
        return False
    except Exception as e:
        log.warning(f"[discord] '{channel}' post error: {e}")
        return False


def claim_and_send(kind: str, ref: str, channel: str, content: str,
                   conn=None) -> str:
    """At-most-once delivery: claim (kind, ref) in discord_notify_log,
    then post. Returns 'sent' | 'duplicate' | 'failed' | 'off'.

    Claim-before-post means a crash between claim and post loses that
    alert rather than duplicating it — for a phone stream, a rare
    missing ping beats a double ping, and the delivered=false row keeps
    the loss visible.
    """
    if not is_configured(channel):
        return "off"
    own_conn = conn is None
    if own_conn:
        from screen.reversal_screen import _conn
        conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO discord_notify_log (kind, ref, channel)
                VALUES (%s, %s, %s)
                ON CONFLICT (kind, ref) DO NOTHING
                RETURNING kind
                """,
                (kind, ref, channel),
            )
            claimed = cur.fetchone() is not None
        conn.commit()
        if not claimed:
            return "duplicate"
        ok = post_discord(channel, content)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE discord_notify_log SET delivered=%s, sent_at=now() "
                "WHERE kind=%s AND ref=%s",
                (ok, kind, ref),
            )
        conn.commit()
        return "sent" if ok else "failed"
    finally:
        if own_conn:
            conn.close()
