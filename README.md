# hv-injury-feed

Public JSON feed of **college football key injuries** for hoopvantage.com, rebuilt by GitHub Actions
(`.github/workflows/cfb.yml`: every 15 min on Saturdays, 30 min Tue–Fri, hourly Sun/Mon).

Feed: `https://raw.githubusercontent.com/stefanodixon-ux/hv-injury-feed/main/cfb.json`

```
{ "updated": "...Z", "sources": [...],
  "teams": { "<ESPN team id>": { "team": "Florida", "abbr": "FLA", "event": "<ESPN event id>", "reported": 4,
      "rows": [ { "athleteId", "name", "pos", "role": "QB|RB1|WR1|WR2|TE1|Tackle leader|…", "rank", "star",
                  "status": "OUT|DOUBTFUL|QUESTIONABLE|SUSPENDED|SEASON|NOT STARTING|MISSED", "missed"?, "detail",
                  "date", "note", "source" } ] } } }
```

Sources (free, public, robots-allowed, polite rate):
1. **RotoWire CFB news RSS** and **RotoBaller player-news RSS** (fresh game-week news; items kept 6 days in `cache/cfb-news.json`).
2. **ESPN game logs**: a key player who has not appeared in the team's last game (QB) or last 2 games (others) → `MISSED`.
3. **Covers.com NCAAF injuries** page: rows are dropped when ESPN shows the player has played since the listing date.

"Key" comes from ESPN season leaders + rosters: passing leader (QB), rushing leader (RB1), top two WRs and top TE by receiving
yards, two tackle leaders, sack leader, INT leader.
