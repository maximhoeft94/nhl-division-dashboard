# Data audit: NHL Division Dashboard

Snapshot audited: Oct 1, 2026 (league standings re-read at 17:31 UTC).

## What was checked, and the result

| Layer | Check | Result |
|---|---|---|
| Standings | All 32 teams (GP, W, L, OTL, PTS, RW, GF, GA, streak, last 10, division, conference, name) compared field by field against a second, independent read of the league's standings feed | 0 differences |
| Standings | Points = 2 x wins + OT losses; goal differential = GF - GA; league GF = GA and wins = losses + OT losses | Pass |
| Games | All 8 final scores re-derive every team's W, L, OTL, regulation wins, GF, GA, streak, and last 10 | Pass |
| Order | Division and league order match the league's own division and league sequence numbers for all 32 teams | Pass |
| Schedule | 47 upcoming games (Oct 1-7) compared to a second read: teams, home/away, date, Eastern start time; daily counts match the league's 8, 5, 13, 5, 4, 9, 3 | 0 differences |
| Players | For each of the 5 box scores: skater goals, assists, plus/minus, shots, faceoffs, penalty minutes, and hits equal the printed team totals | Pass |
| Players | Skater goals equal the final score; goalie shots against equal opponent shots (empty-net goals explain the gaps); each goalie's W-L-OTL matches his team's record | Pass |
| Page | 359 values the page displays (every standings cell on every tab, charts, team leaders, all 12 point-leader sorts x 3 position filters, all 6 goalie sorts, top cards) recomputed independently | 0 differences |
| Page | 773 UI states (every tab, sort, filter, and dropdown) scanned for "undefined", "NaN", or "null" in visible text | 0 found |

| Insights | All ten views, on all five tabs, recomputed independently (1,315 checks): last-5 strips, home/road/one-goal/overtime/division records, goals-for/against points, points-race series, scoring leaders and shares, the 4x4 division matrix, schedule-ahead counts and back-to-backs | 0 differences |
| Insights | Playoff spot for all 32 teams (division 1-3, wild card 1-2, out) checked against the league's own published division and wild-card sequences | 0 differences |
| Full feed | `fetch_nhl.py` run end to end on mocked API responses; shot share, PDO, special-teams, and expected-goals views checked on that output (1,525 checks) | 0 differences |
| Expected goals | Model fitted on synthetic shots with known scoring odds recovers them (distance -0.73 vs -0.75 true); team xG for = xG against, shots and goals reconcile; empty-net, shootout, and own-zone shots excluded; per-game cache works | Pass |

## Problems found and fixed during the audit

1. Tied teams were ordered alphabetically instead of by the league's tiebreakers (points, fewer games, regulation wins, wins, goal differential, goals for). Fixed; matches the league for all 32.
2. Tied players and teams in the leaderboards had no defined order. Now: teams by standings order; players by points, goals, fewer games, then name.
3. Two players are both named Elias Pettersson (VAN, center and defenseman). Dropdowns now show position.
4. A display bug introduced during the audit (goalie dropdown showed "undefined") was caught by the scan and fixed.

## Known limits (not errors)

- Player stats cover the five Sept 29 games only. The three Sept 30 games (PIT-PHI, LAK-COL, NYI-TOR) have no box scores available yet, so point and goalie leaders are not league-wide. Toronto players show 1 GP against the team's 2.
- Scores and OT status for PIT-PHI (7-0) and LAK-COL (8-4) come from the league's standings splits, which they match exactly; they are not from a box score.
- Team shot, power-play, penalty-kill, and faceoff stats are not loaded, so the Special teams and Shot share and PDO views, and Expected goals, show a "not loaded yet" message until `fetch_nhl.py` is run. The expected-goals code could not be run against the live play-by-play feed from here; it was tested on mocked responses shaped like the feed.
- Early-season views (points pace, playoff line, opponents' points %) are accurate but volatile: teams have played 0 to 2 games.
- Player names follow the source site's spelling.

Running `python fetch_nhl.py` replaces all of the above with the league's full feed.
