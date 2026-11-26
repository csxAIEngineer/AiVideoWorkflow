# dp-* fixtures (DP packer regeneration)

The original `feed-attempt-*` / `feed.md` records were packed by main's greedy
fill-to-15s algorithm (groups `[4,5,4],[3,3,4,4],[4,4,4],[4,4,2]`). dev-server
replaced that with the DP packer (`c4f50d8`, `b8e8cb1`) and moved the h3 lock to
30s (`cc9f410`). The four collapse/repair cases in
`tests/test_feed_packing_collapse.py` were regenerated against the DP packer at
the 15s Seedance 2.0 clip limit, whose groups are
`[4,5,4],[3,3,4],[4,4,4],[4,4,4,2]` (durations 13/10/12/14, total 49s).

These files are independent of the shared golden records (which other cases still
read unchanged). Shot bodies and dialogue are carried over verbatim from
`feed-attempt-3-plan.txt`; only unit boundaries, local shot numbers, cut times and
per-unit subject numbering were recomputed. Invariants checked before writing:

- every unit ≤ 15s, total duration conserved (49s);
- local shot numbers continuous, cuts = sum of earlier shots in the unit;
- all storyboard dialogue preserved verbatim;
- `shot_skeleton_failures` empty on the complete draft; `validate_h3` clean on
  units 1–3 and only the real narrator `(S2)` H7 issue on unit 4.

Files:

- `dp-complete-plan.txt` — complete four-unit draft (with program 资产 lines).
- `dp-complete-extract.txt` — same draft without 资产 lines (pre-plan extract).
- `dp-collapsed-plan.txt` — shot headings kept, bodies empty, subjects only on
  unit 1, soundscapes empty (fails body/subject/soundscape gates).
- `dp-collapsed-late.txt` — prose only under each unit's [Shot 1], later heads
  empty, subjects only on unit 1.
- `dp-collapsed-early.txt` — unit 1 fully written, units 2–4 empty skeleton.
