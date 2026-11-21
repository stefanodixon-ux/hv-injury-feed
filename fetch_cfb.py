#!/usr/bin/env python3
"""College football key-injury feed for hoopvantage.com.

Sources (free, public, no logins; robots.txt allows these paths):
  * Covers.com NCAAF injury report  https://www.covers.com/sport/football/ncaaf/injuries  (one page, all teams)
  * ESPN public JSON: this week's FBS scoreboard, team rosters, season leaders (to decide who is "key").

"Key" = the team's starting QB (passing leader), RB1 (rushing leader), WR1/WR2 (top two in receiving yards),
TE1 (top tight end in receiving yards), and the top defenders (two tackle leaders, sack leader, INT leader).

Output: cfb.json  {updated, sources, teams: {espnTeamId: {team, abbr, rows:[...], reported}}}
Run every 30 min on game days by .github/workflows/cfb.yml. Stdlib only.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

UA = "hv-injury-feed/1.0 (github.com/stefanodixon-ux/hv-injury-feed)"
COVERS = "https://www.covers.com/sport/football/ncaaf/injuries"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"
RSS = [("RotoWire", "https://www.rotowire.com/rss/news.php?sport=CFB", None),
       ("RotoBaller", "https://www.rotoballer.com/player-news/feed?sport=ncaaf", "college football")]
NEWS_CACHE_DAYS = 6
HERE = os.path.dirname(os.path.abspath(__file__))
KEY_CACHE = os.path.join(HERE, "cache", "cfb-keyplayers.json")
KEY_MAX_AGE_H = 8
STALE_MINOR_DAYS = 10  # a "Questionable" listing older than this is dropped (Covers keeps old rows)


def get(url: str, as_json: bool = True, tries: int = 3):
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=30) as r:
                b = r.read()
            return json.loads(b) if as_json else b.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            if n == tries - 1:
                print(f"WARN {url}: {e}", file=sys.stderr)
                return None
            time.sleep(2 * (n + 1))


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def name_key(full: str) -> tuple[str, str]:
    """'Lance Watkins Jr.' / 'L. Watkins Jr.' -> ('l', 'watkins')"""
    parts = [p for p in re.split(r"\s+", full.replace(".", ". ").strip()) if p and norm(p)]
    if not parts:
        return ("", "")
    first = norm(parts[0])[:1]
    last = norm(" ".join(parts[1:])) if len(parts) > 1 else norm(parts[0])
    return (first, last)


POS_FAMILY = {"QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE", "OL": "OL", "OT": "OL", "OG": "OL", "C": "OL",
              "G": "OL", "T": "OL", "DL": "DL", "DE": "DL", "DT": "DL", "NT": "DL", "EDGE": "DL", "LB": "LB", "ILB": "LB",
              "OLB": "LB", "MLB": "LB", "DB": "DB", "CB": "DB", "S": "DB", "SAF": "DB", "FS": "DB", "SS": "DB", "K": "K", "P": "K"}
fam = lambda p: POS_FAMILY.get((p or "").upper(), (p or "").upper())


# ---------------------------------------------------------------- ESPN: teams this week + their key players
def week_teams() -> dict[str, dict]:
    sb = get(f"{ESPN}/scoreboard?groups=80&limit=400") or {}
    teams: dict[str, dict] = {}
    for ev in sb.get("events", []):
        for c in ev["competitions"][0]["competitors"]:
            t = c["team"]
            teams[str(t["id"])] = {"id": str(t["id"]), "name": t.get("shortDisplayName") or t.get("location"),
                                   "display": t.get("displayName"), "location": t.get("location"), "abbr": t.get("abbreviation"),
                                   "event": ev["id"], "kickoff": ev.get("date")}
    return teams


def season() -> int:
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def key_players(team_id: str) -> dict:
    """{'roster': {athleteId: {name,pos}}, 'key': {athleteId: {role, rank, star}}}"""
    roster: dict[str, dict] = {}
    r = get(f"{ESPN}/teams/{team_id}/roster") or {}
    for grp in r.get("athletes", []):
        for a in grp.get("items", []) if isinstance(grp, dict) and "items" in grp else ([grp] if isinstance(grp, dict) else []):
            if a.get("id"):
                roster[str(a["id"])] = {"name": a.get("displayName") or a.get("fullName") or "", "pos": (a.get("position") or {}).get("abbreviation")}
    lead = get(f"{CORE}/seasons/{season()}/types/2/teams/{team_id}/leaders") or {}
    cats = {c.get("name"): [re.search(r"athletes/(\d+)", (l.get("athlete") or {}).get("$ref", "")) for l in c.get("leaders", [])]
            for c in lead.get("categories", [])}
    ids = lambda cat: [m.group(1) for m in cats.get(cat, []) if m]
    key: dict[str, dict] = {}

    def put(aid: str, role: str, rank: int, star: bool = False):
        cur = key.get(aid)
        if not cur or rank < cur["rank"]:
            key[aid] = {"role": role, "rank": rank, "star": star}

    qb = [a for a in ids("passingYards") if fam(roster.get(a, {}).get("pos")) in ("QB", "")]
    if qb:
        put(qb[0], "QB", 0, True)
    rb = [a for a in ids("rushingYards") if fam(roster.get(a, {}).get("pos")) == "RB"]
    if rb:
        put(rb[0], "RB1", 1)
    rec = ids("receivingYards")
    wrs = [a for a in rec if fam(roster.get(a, {}).get("pos")) == "WR"]
    for n, a in enumerate(wrs[:2]):
        put(a, f"WR{n + 1}", 1 + n)
    tes = [a for a in rec if fam(roster.get(a, {}).get("pos")) == "TE"]
    if tes:
        put(tes[0], "TE1", 2)
    if rec and rec[0] not in key:  # the top receiver whatever the position
        put(rec[0], "Top receiver", 1)
    for n, a in enumerate(ids("totalTackles")[:2]):
        put(a, "Tackle leader" if n == 0 else "LB/DB starter", 3 + n)
    for a in ids("sacks")[:1]:
        put(a, "Sack leader", 3)
    for a in ids("interceptions")[:1]:
        put(a, "INT leader", 4)
    sched = get(f"{ESPN}/teams/{team_id}/schedule") or {}
    done = sorted(e.get("date", "")[:10] for e in sched.get("events", [])
                  if ((e.get("competitions") or [{}])[0].get("status") or {}).get("type", {}).get("completed"))
    played = {}
    for aid in key:
        played[aid] = sorted(played_dates(aid))
        time.sleep(0.2)
    return {"roster": roster, "key": key, "teamGames": done, "played": played}


def played_dates(aid: str) -> set[str]:
    g = get(f"https://site.web.api.espn.com/apis/common/v3/sports/football/college-football/athletes/{aid}/gamelog") or {}
    ids_ = {str(e.get("eventId")) for st in g.get("seasonTypes", []) for c in st.get("categories", []) for e in c.get("events", [])}
    return {((g.get("events", {}).get(e) or {}).get("gameDate") or "")[:10] for e in ids_} - {""}


def load_keys(teams: dict[str, dict]) -> dict[str, dict]:
    cache = {}
    try:
        cache = json.load(open(KEY_CACHE))
    except Exception:  # noqa: BLE001
        pass
    fresh_after = time.time() - KEY_MAX_AGE_H * 3600
    out = {}
    for tid in teams:
        c = cache.get(tid)
        if c and c.get("at", 0) > fresh_after:
            out[tid] = c
            continue
        kp = key_players(tid)
        kp["at"] = time.time()
        out[tid] = kp
        time.sleep(0.25)  # polite: ~4 requests/s
    os.makedirs(os.path.dirname(KEY_CACHE), exist_ok=True)
    cache.update(out)
    json.dump(cache, open(KEY_CACHE, "w"), separators=(",", ":"))
    return out


# ---------------------------------------------------------------- Covers
STATUS_MAP = [(r"suspen", "SUSPENDED"), (r"season|remainder of the (season|year)|season-ending|torn acl", "SEASON"),
              (r"\bout\b|inactive", "OUT"), (r"doubt", "DOUBTFUL"), (r"question|game.?time", "QUESTIONABLE")]


def status_of(head: str, note: str) -> str | None:
    h = head.lower()
    if re.search(r"suspen", h + " " + note.lower()) and "out" in h:
        return "SUSPENDED"
    if "out" in h and re.search(r"remainder of the (season|year)|season-ending|out for the season|miss the (rest|remainder) of", note.lower()):
        return "SEASON"
    for pat, st in STATUS_MAP:
        if re.search(pat, h):
            return st
    return None


def parse_covers(page: str) -> list[dict]:
    """[{team, slug, name, pos, status, detail, date, note}]"""
    rows = []
    blocks = re.split(r'<div class="covers-CoversMatchups-teamName">', page)
    year = datetime.now(timezone.utc).year
    for b in blocks[1:]:
        m = re.search(r'href="/sport/football/ncaaf/teams/main/([^"]+)">\s*([^<]+?)\s*<br><span>([^<]*)</span>', b)
        if not m:
            continue
        slug, loc, nick = m.group(1), html.unescape(m.group(2)).strip(), html.unescape(m.group(3)).strip()
        for r in re.finditer(r"<span class='player-link'>\s*(.*?)\s*</span>\s*</td>\s*<td>([^<]*)</td>\s*<td><b>([^<]*)</b><br>\(\s*([^)]*)\)</td>(.*?)covers-CoversMatchups-injuryCopy\">\s*(.*?)\s*</div>", b, re.S):
            name = html.unescape(re.sub(r"<[^>]+>", "", r.group(1))).strip()
            head, when, note = html.unescape(r.group(3)).strip(), r.group(4).strip(), html.unescape(re.sub(r"<[^>]+>", "", r.group(6))).strip()
            st, _, detail = head.partition(" - ")
            date = None
            try:
                d = datetime.strptime(f"{when.split(',', 1)[-1].strip()} {year}", "%b %d %Y")
                if d > datetime.now() + timedelta(days=30):
                    d = d.replace(year=year - 1)
                date = d.date().isoformat()
            except ValueError:
                pass
            rows.append({"team": f"{loc} {nick}".strip(), "loc": loc, "slug": slug, "name": name, "pos": r.group(2).strip(),
                         "head": head, "detail": (detail.strip().lower() or None), "date": date, "note": note})
    return rows


def team_index(teams: dict[str, dict]) -> dict[str, str]:
    idx = {}
    for tid, t in teams.items():
        for k in (t.get("display"), t.get("location"), t.get("name")):
            if k:
                idx.setdefault(norm(k), tid)
    return idx


def match_team(row: dict, idx: dict[str, str]) -> str | None:
    for k in (row["team"], row["slug"].replace("-", " "), row["loc"]):
        t = idx.get(norm(k))
        if t:
            return t
    return None


def match_player(row: dict, roster: dict[str, dict]) -> str | None:
    f, l = name_key(row["name"])
    cands = [aid for aid, a in roster.items() if name_key(a["name"]) == (f, l)]
    if len(cands) > 1:
        same = [a for a in cands if fam(roster[a]["pos"]) == fam(row["pos"])]
        cands = same or cands
    if not cands:  # last name + position only
        cands = [aid for aid, a in roster.items() if name_key(a["name"])[1] == l and fam(a["pos"]) == fam(row["pos"])]
    return cands[0] if len(cands) == 1 else None


# ---------------------------------------------------------------- RSS news (fresh; accumulated across runs)
NEWS_CACHE = os.path.join(HERE, "cache", "cfb-news.json")


def news_status(text: str) -> str | None:
    t = text.lower()
    if re.search(r"suspend", t):
        return "SUSPENDED"
    if re.search(r"season-ending|out for (the )?(rest of the |remainder of the )?(season|year)|miss the (rest|remainder) of the season|torn acl", t):
        return "SEASON"
    if re.search(r"\bdoubtful\b", t):
        return "DOUBTFUL"
    if re.search(r"game-time decision|\bquestionable\b|\bgtd\b|uncertain|iffy|in doubt", t):
        return "QUESTIONABLE"
    if re.search(r"\bout\b|ruled out|won't play|will not play|not expected to play|sidelined|inactive|unavailable|will miss|to miss|not dressing", t):
        return "OUT"
    if re.search(r"will not start|won't start|not starting|benched|loses starting", t):
        return "NOT STARTING"
    return None


def pull_news() -> list[dict]:
    try:
        cache = json.load(open(NEWS_CACHE))
    except Exception:  # noqa: BLE001
        cache = {}
    for src, url, cat in RSS:
        x = get(url, as_json=False)
        if not x:
            continue
        try:
            root = ET.fromstring(x.encode("utf-8"))
        except ET.ParseError as e:
            print(f"WARN {src} RSS: {e}", file=sys.stderr)
            continue
        for it in root.iter("item"):
            cats = " ".join((c.text or "") for c in it.findall("category")).lower()
            if cat and cat not in cats:
                continue
            title = html.unescape(it.findtext("title") or "").strip()
            desc = html.unescape(re.sub(r"<[^>]+>", "", it.findtext("description") or "")).strip()
            desc = re.sub(r"\s*Visit RotoWire\.com.*$", "", desc, flags=re.S).strip()
            pub = it.findtext("pubDate") or ""
            try:
                when = parsedate_to_datetime(re.sub(r" (\d+:\d+:\d+) (AM|PM) (PST|PDT)", lambda m: " " + datetime.strptime(m.group(1) + m.group(2), "%I:%M:%S%p").strftime("%H:%M:%S") + (" -0800" if m.group(3) == "PST" else " -0700"), pub))
            except Exception:  # noqa: BLE001
                when = datetime.now(timezone.utc)
            gid = it.findtext("guid") or it.findtext("link") or title
            # the player: RotoWire titles are "Name: headline"; RotoBaller's start with the full name
            name = title.split(":", 1)[0].strip() if ":" in title else None
            cache[f"{src}:{gid}"] = {"source": src, "title": title, "desc": desc[:400], "at": when.astimezone(timezone.utc).isoformat(), "name": name,
                                     "link": it.findtext("link")}
    cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_CACHE_DAYS)
    cache = {k: v for k, v in cache.items() if datetime.fromisoformat(v["at"]) > cutoff}
    os.makedirs(os.path.dirname(NEWS_CACHE), exist_ok=True)
    json.dump(cache, open(NEWS_CACHE, "w"), separators=(",", ":"), ensure_ascii=False)
    return sorted(cache.values(), key=lambda v: v["at"])


def roster_lookup(keys: dict[str, dict]) -> dict[str, list[tuple[str, str]]]:
    """normalised full name -> [(teamId, athleteId)]"""
    idx: dict[str, list[tuple[str, str]]] = {}
    for tid, kp in keys.items():
        for aid, a in kp.get("roster", {}).items():
            idx.setdefault(norm(a["name"]), []).append((tid, aid))
    return idx


def news_player(item: dict, idx: dict[str, list[tuple[str, str]]]) -> tuple[str, str] | None:
    names = [item["name"]] if item.get("name") else []
    # RotoBaller: "<Team> <position> Firstname Lastname (injury) ..." in the description, the title starts with the name
    words = re.findall(r"[A-Z][\w.'-]+", item["title"])
    for n in (3, 2):
        names.append(" ".join(words[:n]))
    m = re.search(r"(?:quarterback|running back|wide receiver|tight end|linebacker|cornerback|safety|edge rusher|defensive \w+|offensive \w+|kicker)\s+([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){1,2})", item["desc"])
    if m:
        names.insert(0, m.group(1))
    for n in names:
        hits = idx.get(norm(n or ""))
        if hits and len(hits) == 1:
            return hits[0]
    return None


def last_played(aid: str) -> str | None:
    """Date (YYYY-MM-DD) of the player's latest game with stats this season, from ESPN's public game log."""
    g = get(f"https://site.web.api.espn.com/apis/common/v3/sports/football/college-football/athletes/{aid}/gamelog") or {}
    played = set()
    for stype in g.get("seasonTypes", []):
        for c in stype.get("categories", []):
            for e in c.get("events", []):
                played.add(str(e.get("eventId")))
    dates = [(g.get("events", {}).get(e) or {}).get("gameDate", "")[:10] for e in played]
    dates = [d for d in dates if d]
    return max(dates) if dates else None


def main():
    now = datetime.now(timezone.utc)
    teams = week_teams()
    page = get(COVERS, as_json=False) or ""
    cov = parse_covers(page)
    print(f"ESPN week teams: {len(teams)} · Covers rows: {len(cov)}", file=sys.stderr)
    keys = load_keys(teams)
    idx = team_index(teams)
    out: dict[str, dict] = {}
    unmatched_teams = set()
    for row in cov:
        tid = match_team(row, idx)
        if not tid:
            unmatched_teams.add(row["team"])
            continue
        t = teams[tid]
        ent = out.setdefault(tid, {"team": t["name"], "abbr": t["abbr"], "event": t["event"], "reported": 0, "rows": []})
        ent["reported"] += 1
        st = status_of(row["head"], row["note"])
        if not st:
            continue
        if st in ("QUESTIONABLE", "DOUBTFUL") and row["date"] and (now.date() - datetime.fromisoformat(row["date"]).date()).days > STALE_MINOR_DAYS:
            continue
        kp = keys.get(tid) or {}
        aid = match_player(row, kp.get("roster", {}))
        role = (kp.get("key") or {}).get(aid) if aid else None
        if not role:
            continue
        full = kp["roster"][aid]["name"]
        lp = last_played(aid)  # Covers stopped updating college rows in Oct: drop anyone who has played since
        if lp and row["date"] and lp > row["date"]:
            print(f"drop (played {lp} after Covers {row['date']}): {t['name']} {full}", file=sys.stderr)
            continue
        row["note"] = (f"Has not played since {lp}. " if lp else "No game played this season. ") + row["note"]
        ent["rows"].append({"athleteId": aid, "name": full, "pos": row["pos"], "role": role["role"], "rank": role["rank"], "star": role["star"],
                            "status": st, "detail": row["detail"], "date": row["date"], "note": row["note"][:240], "source": "Covers"})
    used_gl = 0
    for tid, kp in keys.items():  # ESPN game logs: a key player who stopped appearing (no report needed)
        games = kp.get("teamGames") or []
        t = teams.get(tid)
        if not t or not games:
            continue
        ent = out.setdefault(tid, {"team": t["name"], "abbr": t["abbr"], "event": t["event"], "reported": 0, "rows": []})
        have = {r["athleteId"] for r in ent["rows"]}
        for aid, role in (kp.get("key") or {}).items():
            pd = set(kp.get("played", {}).get(aid) or [])
            if aid in have or not pd:
                continue
            missed = 0
            for d in reversed(games):
                if d in pd:
                    break
                missed += 1
            # a TE / WR can play a whole game without a catch (no game-log line), so they need a longer streak
            need = 1 if role["role"] == "QB" else 3 if role["role"] in ("TE1", "WR1", "WR2", "Top receiver") else 2
            if missed < need:
                continue
            a = kp["roster"].get(aid, {})
            if not a.get("name"):
                continue
            used_gl += 1
            ent["rows"].append({"athleteId": aid, "name": a.get("name", ""), "pos": a.get("pos"), "role": role["role"], "rank": role["rank"], "star": role["star"],
                                "status": "MISSED", "missed": missed, "detail": None, "date": max(pd),
                                "note": f"Missed the last {missed} game{'s' if missed > 1 else ''} (last played {max(pd)}); no injury report found.", "source": "ESPN game logs"})
    news = pull_news()
    ridx = roster_lookup(keys)
    used_news = 0
    for item in news:  # oldest first: a newer item replaces an older row for the same player
        hit = news_player(item, ridx)
        if not hit:
            continue
        tid, aid = hit
        t = teams[tid]
        ent = out.setdefault(tid, {"team": t["name"], "abbr": t["abbr"], "event": t["event"], "reported": 0, "rows": []})
        st = news_status(item["title"] + ". " + item["desc"])
        kp = keys[tid]
        a = kp["roster"][aid]
        role = (kp.get("key") or {}).get(aid)
        ent["rows"] = [r for r in ent["rows"] if r["athleteId"] != aid]
        if not st:  # e.g. "cleared to play" / "upgraded": the old row goes, nothing new
            continue
        if not role:
            if fam(a.get("pos")) not in ("QB", "RB", "WR", "TE"):
                continue
            role = {"role": fam(a.get("pos")), "rank": 2 if fam(a.get("pos")) == "QB" else 5, "star": False}
        m = re.search(r"\(([a-z][a-z /-]{2,30})\)", item["desc"])
        ent["reported"] += 1
        used_news += 1
        ent["rows"].append({"athleteId": aid, "name": a["name"], "pos": a.get("pos"), "role": role["role"], "rank": role["rank"], "star": role["star"],
                            "status": st, "detail": m.group(1) if m and m.group(1) != "undisclosed" else None, "date": item["at"][:10],
                            "note": (item["title"] + " — " + item["desc"])[:240], "source": item["source"], "at": item["at"]})
    for tid, t in teams.items():  # teams with no report still get an entry: "checked, nothing key"
        out.setdefault(tid, {"team": t["name"], "abbr": t["abbr"], "event": t["event"], "reported": 0, "rows": []})
    feed = {"sport": "cfb", "updated": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "sources": [{"name": n, "url": u} for n, u, _ in RSS] + [{"name": "Covers", "url": COVERS}, {"name": "ESPN game logs", "url": "https://www.espn.com/college-football/"}, {"name": "ESPN (rosters + season leaders)", "url": "https://www.espn.com/college-football/"}],
            "teams": out}
    json.dump(feed, open(os.path.join(HERE, "cfb.json"), "w"), separators=(",", ":"), ensure_ascii=False)
    key_rows = sum(len(v["rows"]) for v in out.values())
    with_rep = sum(1 for v in out.values() if v["reported"])
    print(f"game-log misses: {used_gl} · news items cached: {len(news)} · used: {used_news} · teams: {len(out)} · with a Covers report: {with_rep} · key injuries: {key_rows} · Covers teams not on this week's slate: {len(unmatched_teams)}")


if __name__ == "__main__":
    main()
