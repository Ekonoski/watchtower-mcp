# Trading a level — break-and-retest and coming into a range edge

Compiled 2026-09-09 at Eric's request ("research and give me the best
technique for trading a range or coming to a level — either a break-
through retest or coming down to a range level"). Same rule as the
options note: the literature is a frame, the desk's own record is the
grade. Every claim below says which it is.

## 1. Why a level does anything at all (the physical model)

The academic answer is order clustering, not geometry. Osler (2003, J.
Finance) showed that take-profit orders cluster AT round numbers and
stop-loss orders cluster just BEYOND them. Take-profits at the level
produce bounces on the first approach; stops beyond it produce cascades
once it breaks. Both halves of "support/resistance" follow from where
the resting orders sit. Osler (2000, NY Fed) tested published S/R levels
on intraday FX and found bounce rates above 60%, persisting for about
five business days, varying by firm and pair.

Implications the desk already lives by:

- A level is a place where OTHER people's orders are, so the trade is a
  bet on who is defending it. That is why the defense study reads
  volume at the touch and why the gamma walls (open interest at a
  strike) out-trade the re-priced "wall walk" (frozen targets +2.24R vs
  walk_toward −3.54R on the same trades).
- The first approach is where the take-profit cluster sits. Repeated
  tests eat it; practitioner evidence (and the multiple-tests debate)
  says a level tested three or four times with diminishing volume is
  more likely to break than to hold.
- Once broken, the cascade is the continuation. That is the mechanism
  behind "buy the retest of a level that has already broken and held"
  — the trapped stops have already fired, the take-profits have moved.

## 2. What the record says about each way to trade a level

**Every-touch level machines are coin flips.** The Tape Bot's own
break-retest state machine graded at PDH/PDL on the SPY/QQQ 15m record
(21,419 signals): retest longs 51.6–52.2% win, average within a few bps
of zero, MFE ≈ MAE; retest shorts 44%. On 11 single names (tape-entry
study) the PDH-retest long is 50.6% / +0.5 bps and the ORB-retest long
50.1% / −7.8 bps — neither beats its chase control (52.2% / +1.9 bps;
51.5% / +2.1). A level touched is not a trade; it is a fight being
narrated.

**Breakout-close entries lose to their own control.** The Darvas box
study (13,436 breakouts, 3,110 names): buying the close of the breakout
bar +0.29R post-2016 vs +0.59R for random same-stop entries on the same
names. Bulkowski's pattern census points the same way: failure rates
rose across bull markets and roughly half of breakouts throw back to the
level. The desk buys retests, never breakout closes.

**The retest that grades is the one that comes late, first, and in the
direction of acceptance.** Day-bias study (5,443 SPY days): open above
PDH closes above the prior close 79.5%; the PDH retest arriving AFTER
10:30 wins ~69%, +27 bps to the close, MFE ~2× MAE, era-stable (73/66%);
the same retest at 9:45 is chop (52–56%, MAE > MFE). QQQ replicates the
direction at a third of the magnitude. At the index, 15m-close
confirmation does NOT pay (82.5% of touches confirm at a 21 bps premium;
the shallow knives cost less than the premium) — buy the touch. The
audition book trades exactly this; in its first 12 sessions it stood
aside 9 times, cancelled 3 early touches, and has not filled once. A
thin, graded edge that shows up a few times a month, not daily.

**On single names, the retest wants proof.** The defense study
(historical retest episodes, 15m tape, v1): defended touches 58.0% 1R /
+0.05R (n=2,511) vs no-defense 53.2% / −0.15R (n=5,035) vs knives
(closed through the stop before any defense) 31.2% / −1.21R (n=715).
The cost side is real: "missed" (ripped off the level without the
signature) 60.8% / +0.10R (n=3,135). Live, small-n: confirmed touches
−0.28R (n=16) vs no-confirm −1.14R, 0 wins (n=7). Opposite of the
index, and both are true: index retests are bought on the touch,
single-name retests on a close back through the level with buyers
visible.

**The short side of a level does not grade at the index.** Blind PDL
fade 49.8%; confirmed PDL breakdown 46.1% / −1.7 bps; the late PDL fade
(59% aggregate) fails the era split and QQQ replication. Downside BIAS
is real (open below PDL closes below 76%); the retest-short ENTRY is
not. Structure shorts on single names: 728 episodes net negative in
every regime.

**The day decides whether any of this works.** Day-type study (9,298
SPY/QQQ days): yesterday's range < 0.5 ATR and a first 15m bar < 0.25
ATR → chop 78–82%, trend 7–9%; wide × wide → trend 48–60%. A range day
is one where the level breaks fail by construction. Opens within 0.3%
of the gamma flip: QQQ 8 of 8 chop; every day with 6+ flip crosses was
chop. And on a slippery board the morning levels are mostly not reached
at all: wall-touch prior, levels within 3% of spot, slippery regime —
call wall touched 20% of days (n=36), flip 15% (n=37), put wall 23%
(n=37). "Coming to a level" is a rarer event than the chart suggests.

**Exits at levels pay when the level is real.** Exit-shape study on 446
GO entries, option frame: take-profit at PDH (+$33/contract, 57% hit),
premarket high (+$24, 69%), ORB high (+$17, 68%) are the only exits
positive in both halves; option strikes (−$6, 44% hit) and the HOD at
entry (−$12) are not resistance.

**Stops at levels want the wick rule.** Touch-based structural stops
whipsaw 86% on single names; the same level on a 5m close through
nearly doubles leader-day expectancy; a 1% disaster on touch whipsaws
8%. The index disaster stop is on 15m closes.

## 3. The technique, assembled from the graded parts

This is the desk's rule set for a level, in order. Nothing here is new;
it is what the record has cleared, sequenced.

1. **Day first.** Read the 📐 line at 9:46. RANGE LIKELY: no level
   breakout trades in premium; the only level trade on a range day is
   the failure test (section 4), and it is ungraded here. TRAVEL
   LIKELY: retests can be bought. UNDECIDED: shares or time, wait for
   10:30.
2. **Direction from acceptance, not from the level.** The open relative
   to yesterday's range sets the bias (79.5% / 76%). Trade retests only
   in that direction. No retest-shorts at the index.
3. **Let the level prove itself.** The first touch after 10:30 is the
   graded entry; a touch before 10:30 cancels the day at the index
   (the opening flush is chop). A level crossed on closes both ways is a
   blender, not a setup — stand down after the second cross.
4. **Proof depends on the instrument.** Index: buy the touch, resting
   limit at the level. Single name: buy the first completed 5m/15m
   close back through the level with defense visible (contracting red
   volume into the touch, a green uptick off it) — expect to miss some
   runners; that is the cost of not catching knives.
5. **Stop is a close, not a touch.** Under the retest bar, exit on a
   completed 5m close through (15m at the index); a 1% disaster on
   touch as the outer wall. Size the unit on the disaster, not the
   pullback bar, when the instrument is a 0DTE.
6. **Target is the next real level.** PDH / premarket high / ORB high /
   the far side of the range. Bank there; never a strike, never the
   HOD-at-entry. Runner under the ratcheted structure if the day is
   TRAVEL LIKELY.
7. **One trade per level per day.** The graded index trade is one fill;
   the second attempt at the same level is the coin flip the machine
   already measured.

## 4. The range trade proper — the failure test — is NOT graded here yet

Auction-market practice (Dalton's "look above and fail", Grimes's
failure test) says the highest-conviction trade at a range EDGE is the
failed break: price probes beyond the prior high / ORB high / range
extreme on light participation, then closes back inside; enter on the
first close back inside, target the far side. The desk has never graded
it. Its nearest graded cousins say: fading PDL at the index fails
(section 2); the early-touch-reclaim variant of the day-bias trade is
queued, ungraded; the tapebot "flip" (a crossing close re-arming the
machine the other way) was a signal family that graded as chop.

Pre-registrable (not built): on RANGE LIKELY days only, a bar closes
beyond ORB-high / PDH (or below ORB-low / PDL) and the next 15m bar
closes back inside — enter at that close against the break, stop beyond
the probe's extreme on a 15m close, target the opposite range edge, exit
at the close if neither. Both eras, SPY and QQQ replication, the usual
bar. Until it grades, a range day is a stand-aside day for premium, per
Eric's 2026-09-08 ruling.

## 5. Literature notes (frame, not rules)

- Osler, "Support for Resistance" (NY Fed EPR 2000): published S/R
  levels bounce >60% on intraday FX, effect lasts ~5 days, varies by
  firm/pair.
- Osler, "Currency Orders and Exchange Rate Dynamics" (J. Finance 2003):
  take-profit clustering at round numbers → bounces; stop-loss clustering
  beyond → cascades; a mechanism for both halves of technical analysis.
- Brock, Lakonishok, LeBaron (1992): trading-range breakout rules on the
  Dow 1897–1986 beat four null models; Sullivan, Timmermann, White
  (1999) found the best rule survives a data-snooping check in sample
  but the effect does not persist out of sample.
- Zarattini & Aziz (2023): 5-minute ORB on QQQ 2016–2023, 33%
  annualized alpha net of commissions; Zarattini, Barbon, Aziz (2024):
  on 7,000+ stocks the plain ORB is weak and SELECTION (Stocks in Play by
  opening relative volume) does the work — the same finding as the
  desk's frequency night: entries are commodities, selection is the edge.
- Bulkowski (Encyclopedia of Chart Patterns): failure rates rose from the
  1990s to the 2000s bull; measured targets hit 60–70%; throwbacks and
  pullbacks to the broken level are the norm, not the exception.
- Grimes (The Art and Science of Technical Analysis): four trades —
  trend continuation, trend termination, S/R holding, S/R failing; the
  failure test needs a probe of an ATR or more, not half; pullback
  failure rises when the trading timeframe disagrees with the higher one.
- The multiple-tests debate: practitioner consensus has moved from
  "more tests = stronger" to "tests with diminishing volume = a level
  being eaten"; a clean first touch holds better than a fourth.
