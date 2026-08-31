# Reference: the BANKS framework guides

Eric's member copies of the BANKS Bot / BANKS Scanner guides (2026-08-31),
kept here as the SPEC the Watchtower Tape indicators were built from —
private reference for this desk only, not for redistribution.

The Watchtower Tape Bot and Tape Scanner are an independent implementation
of the same reading framework (CONTROL > LEVEL > TREND > SETUP > MOMENTUM,
confirmed closes only), extended with desk-specific features. Feature map
as of Bot v2.3 / Scanner v1.5 vs the guides (Bot v3.1 / Scanner v1.0):

## Parity (both have it)
- Bot dashboard: 8/21 CONTROL, KEY LEVEL, TREND, ACTIVE SETUP, MOMENTUM —
  same states, same wick-rule discipline, same direction-vs-strength split.
- Scanner: 7-symbol table, MAG 7 / CUSTOM 7, 5M/15M scan, independent
  15/30M ORB, columns SYMBOL / 8/21 / KEY LEVEL / ORB / RETEST / MOMENTUM /
  STATUS, READY > BUILDING > NO SETUP ranking, rows-to-show, New READY alert.
- Automatic levels: PWH/PWL, PDH/PDL, PMH/PML, completed-ORB H/L.

## Ours beyond the guides
- Level-break alerts on EVERY level (confirmed close), Discord-formatted;
  control-flip and fresh-setup alerts. (Guides document only Scanner READY.)
- Gamma board slots as live levels (CW/PW/GF/FVT/FVB) — fed by Watchtower.
- Futures: 24H Globex voting + ONH/ONL overnight range; crypto sessions;
  stock overnight-range toggle.
- All-timeframe level consistency (PM/ON levels from a 15m feed above 15m;
  Bot v2.3) and weekend/boundary-proof level clocks (v2.0).
- Scanner v1.5 two-state retest: TEST 8/21 (live, this bar) vs resolved
  8/21 BULL/BEAR (just-closed bar) — Eric's spec.

## Gaps vs the guides (queued)
1. KEY LEVEL statuses RECLAIMED / LOST (guides have 4 states; ours shows
   ABOVE/BELOW only — the transitions exist in the setup machine, not on
   the row). -> Bot v2.4 candidate.
2. C1-C4 free custom levels (ours: one free C1 beside the gamma slots).
3. "STRUCTURE WAIT BULL/BEAR" active-setup state (their v3.2 wording).

The playbook notes their live product moved to Bot V3.2 / Scanner V1.1;
these guides trail their own software. Ours cannot drift that way — the
repo copy IS the product.
