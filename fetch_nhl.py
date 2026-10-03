#!/usr/bin/env python3
"""Pull the CURRENT NHL regular season from the public NHL APIs and embed it in index.html.

    python fetch_nhl.py                  # rewrites ./index.html
    python fetch_nhl.py --html docs/index.html
    python fetch_nhl.py --xg             # also build expected goals from every shot (slower the first time)

Current season only: regular-season games and stats for the season the NHL is playing right now
(no preseason, playoffs, or earlier seasons). If the API still reports a previous season, the
script stops instead of embedding old data.

Needs Python 3.9+ and no third-party packages. Makes roughly 70 requests (about 20 seconds).

Sources (no API key needed):
    api-web.nhle.com/v1/standings/now
    api-web.nhle.com/v1/club-schedule-season/{team}/{season}
    api-web.nhle.com/v1/club-stats/{team}/{season}/2
    api.nhle.com/stats/rest/en/team/summary
"""
import argparse, datetime as dt, json, math, re, sys, time, unicodedata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"
ET = ZoneInfo("America/New_York")

# Display colors only. Keyed by NHL team abbreviation.
COLORS = {
    "ANA": "#f47a38", "BOS": "#f2b01e", "BUF": "#3f78d4", "CAR": "#cc0000", "CBJ": "#3a6fc4", "CGY": "#d2001c",
    "CHI": "#cf0a2c", "COL": "#8f2f4f", "DAL": "#00875a", "DET": "#d6283b", "EDM": "#ff6a1f", "FLA": "#c8102e",
    "LAK": "#a2aaad", "MIN": "#2e8b57", "MTL": "#a6192e", "NJD": "#e0243a", "NSH": "#f2b01e", "NYI": "#2b6fb8",
    "NYR": "#1f55c9", "OTT": "#e03a4b", "PHI": "#f74902", "PIT": "#e8b312", "SEA": "#68b8c4", "SJS": "#00868f",
    "STL": "#2f6bd0", "TBL": "#2352a8", "TOR": "#5b8dd9", "UTA": "#6cace4", "VAN": "#00a050", "VGK": "#b4975a",
    "WPG": "#46629c", "WSH": "#d02b3c",
}


def get_json(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = Request(url, headers={"User-Agent": "nhl-division-dashboard/1.0"})
            with urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            time.sleep(0.12)
            return data
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("Request failed after %d tries: %s (%s)" % (tries, url, last))


def default_season(today=None):
    today = today or dt.datetime.now(ET).date()
    y = today.year if today.month >= 9 else today.year - 1
    return int("%d%d" % (y, y + 1))


def norm(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def dflt(x):
    """The API wraps most strings as {"default": "..."}."""
    return x.get("default", "") if isinstance(x, dict) else (x or "")


def pct(v):
    """Team summary percentages arrive as fractions (0.215); the dashboard wants 21.5."""
    if v is None:
        return None
    return round(v * 100 if v <= 1 else v, 1)



# ---------------------------------------------------------------- expected goals
# Model: probability that a shot on goal becomes a goal, as a logistic function of distance, angle, and shot type,
# fitted (ridge-regularised) on every shot on goal in this season's feed so far. Empty-net shots, shootouts, and shots
# outside the offensive zone are excluded. Team xG is the sum of those probabilities.
SHOT_TYPES = ["snap", "slap", "backhand", "tip", "wrap", "other"]  # "wrist" is the baseline


def shot_type(raw):
    r = (raw or "").lower()
    if r in ("wrist",): return None
    if r in ("snap", "slap", "backhand"): return r
    if r in ("tip-in", "deflected"): return "tip"
    if r == "wrap-around": return "wrap"
    return "other"


def extract_shots(pbp):
    """Return [(shooter_abbrev, defender_abbrev, dist_ft, angle_deg, type, is_goal)] for one game's play-by-play."""
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    ab = {home["id"]: dflt(home.get("abbrev")), away["id"]: dflt(away.get("abbrev"))}
    out = []
    for p in pbp.get("plays", []):
        if p.get("typeDescKey") not in ("shot-on-goal", "goal"):
            continue
        if (p.get("periodDescriptor") or {}).get("periodType") == "SO":
            continue
        d = p.get("details") or {}
        x, y, owner = d.get("xCoord"), d.get("yCoord"), d.get("eventOwnerTeamId")
        if x is None or y is None or owner not in ab or d.get("zoneCode") not in (None, "O"):
            continue
        sit = str(p.get("situationCode", ""))  # [away goalie][away skaters][home skaters][home goalie]
        if len(sit) == 4:
            defending_goalie = sit[3] if owner == away["id"] else sit[0]
            if defending_goalie == "0":
                continue  # empty net
        ax = min(abs(x), 100)
        out.append((ab[owner], ab[home["id"] if owner == away["id"] else away["id"]], math.hypot(89 - ax, y),
                    math.degrees(math.atan2(abs(y), 89 - ax)), shot_type(d.get("shotType")), 1 if p["typeDescKey"] == "goal" else 0))
    return out


def _row(dist, ang, typ):
    return [1.0, dist / 10.0, ang / 30.0] + [1.0 if typ == t else 0.0 for t in SHOT_TYPES]


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * bb for a, bb in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def fit_xg(shots, ridge=1.0, iters=25):
    """Newton-Raphson logistic regression. Returns coefficient list (intercept first)."""
    k = 3 + len(SHOT_TYPES)
    X = [_row(s[2], s[3], s[4]) for s in shots]
    y = [s[5] for s in shots]
    base = (sum(y) + 1.0) / (len(y) + 10.0)  # start at the league shooting percentage
    w = [math.log(base / (1 - base))] + [0.0] * (k - 1)
    for _ in range(iters):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for xi, yi in zip(X, y):
            z = sum(a * b for a, b in zip(w, xi))
            p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            for i in range(k):
                g[i] += (yi - p) * xi[i]
                for j in range(k):
                    H[i][j] += p * (1 - p) * xi[i] * xi[j]
        for i in range(1, k):  # ridge on everything except the intercept
            g[i] -= ridge * w[i]
            H[i][i] += ridge
        H[0][0] += 1e-9
        step = _solve(H, g)
        w = [a + b for a, b in zip(w, step)]
        if max(abs(v) for v in step) < 1e-7:
            break
    return w


def xg_prob(w, dist, ang, typ):
    z = sum(a * b for a, b in zip(w, _row(dist, ang, typ)))
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))


def build_xg(games, fetch=get_json, log=print, cache_path=None):
    cache = {}
    if cache_path and Path(cache_path).exists():
        cache = json.loads(Path(cache_path).read_text())
    finals = [g for g in games if g["st"] == "F" and "id" in g]
    new = 0
    for g in finals:
        if str(g["id"]) not in cache:
            cache[str(g["id"])] = extract_shots(fetch("%s/gamecenter/%s/play-by-play" % (WEB, g["id"])))
            new += 1
    if cache_path and new:
        Path(cache_path).write_text(json.dumps(cache))
    shots = [tuple(s) for g in finals for s in cache.get(str(g["id"]), [])]
    shots = [(s[0], s[1], s[2], s[3], s[4], s[5]) for s in shots]
    log("expected goals: %d shots from %d games (%d fetched this run)" % (len(shots), len(finals), new))
    if len(shots) < 50:
        return None
    w = fit_xg(shots)
    X = {}
    for sh, df, dist, ang, typ, goal in shots:
        p = xg_prob(w, dist, ang, typ)
        for team, f in ((sh, "f"), (df, "a")):
            t = X.setdefault(team, dict(xgf=0.0, xga=0.0, sf=0, sa=0, gf=0, ga=0))
            t["xg" + f] += p
            t["s" + f] += 1
            t["g" + f] += goal
    return {a: dict(xgf=round(t["xgf"], 3), xga=round(t["xga"], 3), sf=t["sf"], sa=t["sa"], gf=t["gf"], ga=t["ga"]) for a, t in X.items()}


def build_data(fetch=get_json, log=print, today=None, xg=False, xg_cache=None):
    today = today or dt.datetime.now(ET).date()
    season = default_season(today)

    # --- standings (also gives us the 32 teams and their divisions)
    st = fetch("%s/standings/now" % WEB)
    rows = st.get("standings", [])
    if not rows:
        raise RuntimeError("Standings came back empty.")
    sid = rows[0].get("seasonId")
    if sid and int(sid) != int(season):
        raise RuntimeError("The NHL API is still reporting season %s, but the current season is %s. "
                           "Nothing was written. Try again once the %s season has started." % (sid, season, season))

    teams = {}
    for r in rows:
        ab = dflt(r["teamAbbrev"])
        gp = r.get("gamesPlayed", 0)
        pts = r.get("points", 0)
        l10 = "%d-%d-%d" % (r.get("l10Wins", 0), r.get("l10Losses", 0), r.get("l10OtLosses", 0))
        streak = "%s%s" % (r.get("streakCode", ""), r.get("streakCount", "")) if r.get("streakCode") else ""
        teams[ab] = {
            "name": dflt(r.get("teamName")), "division": r.get("divisionName", ""), "conference": r.get("conferenceName", ""),
            "color": COLORS.get(ab, "#6b7a90"),
            "gp": gp, "w": r.get("wins", 0), "l": r.get("losses", 0), "otl": r.get("otLosses", 0), "pts": pts,
            "ptsPct": round(r.get("pointPctg", (pts / (2 * gp)) if gp else 0), 4),
            "rw": r.get("regulationWins", 0), "row": r.get("regulationPlusOtWins", r.get("wins", 0)), "gf": r.get("goalFor", 0), "ga": r.get("goalAgainst", 0),
            "diff": r.get("goalDifferential", r.get("goalFor", 0) - r.get("goalAgainst", 0)),
            "l10": l10, "streak": streak,
            "sfpg": None, "sapg": None, "ppPct": None, "pkPct": None, "foPct": None,
        }
    log("standings: %d teams, season %s" % (len(teams), season))

    # --- special teams / shots / faceoffs
    try:
        q = quote("seasonId=%s and gameTypeId=2" % season, safe="=")
        summ = fetch("%s/team/summary?cayenneExp=%s" % (STATS, q)).get("data", [])
        by_name = {norm(t["name"]): a for a, t in teams.items()}
        hit = 0
        for s in summ:
            a = by_name.get(norm(s.get("teamFullName")))
            if not a:
                continue
            hit += 1
            teams[a].update(sfpg=round(s.get("shotsForPerGame"), 2) if s.get("shotsForPerGame") is not None else None,
                            sapg=round(s.get("shotsAgainstPerGame"), 2) if s.get("shotsAgainstPerGame") is not None else None,
                            ppPct=pct(s.get("powerPlayPct")), pkPct=pct(s.get("penaltyKillPct")), foPct=pct(s.get("faceoffWinPct")))
        log("team summary: matched %d of %d teams" % (hit, len(teams)))
    except RuntimeError as e:
        log("WARNING: team summary unavailable, special-teams panels will be blank (%s)" % e)

    # --- schedule + results
    games, seen = [], set()
    for a in teams:
        sched = fetch("%s/club-schedule-season/%s/%s" % (WEB, a, season))
        for g in sched.get("games", []):
            if g.get("gameType") != 2 or g["id"] in seen:
                continue
            h, aw = dflt(g["homeTeam"].get("abbrev")), dflt(g["awayTeam"].get("abbrev"))
            if h not in teams or aw not in teams:
                continue
            seen.add(g["id"])
            final = g.get("gameState") in ("FINAL", "OFF")
            start = g.get("startTimeUTC")
            when = ""
            if start:
                t = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ET)
                when = t.strftime("%-I:%M %p") + " ET" if sys.platform != "win32" else t.strftime("%#I:%M %p") + " ET"
            period = (g.get("gameOutcome") or {}).get("lastPeriodType", "REG")
            games.append({"id": g["id"], "d": g["gameDate"], "h": h, "a": aw,
                          "hs": g["homeTeam"].get("score") if final else None,
                          "as": g["awayTeam"].get("score") if final else None,
                          "ot": period if (final and period in ("OT", "SO")) else "",
                          "st": "F" if final else "U", "t": "" if final else when})
    log("schedule: %d regular-season games" % len(games))

    # --- skaters and goalies
    skaters, goalies = [], []
    for a in teams:
        cs = fetch("%s/club-stats/%s/%s/2" % (WEB, a, season))
        for s in cs.get("skaters", []):
            if not s.get("gamesPlayed"):
                continue
            toi = s.get("avgTimeOnIcePerGame") or s.get("avgToi") or 0
            skaters.append({
                "id": s["playerId"], "name": ("%s %s" % (dflt(s.get("firstName")), dflt(s.get("lastName")))).strip(),
                "team": a, "pos": s.get("positionCode", ""), "gp": s.get("gamesPlayed", 0),
                "g": s.get("goals", 0), "a": s.get("assists", 0), "p": s.get("points", 0), "pm": s.get("plusMinus", 0),
                "pim": s.get("penaltyMinutes", 0), "ppg": s.get("powerPlayGoals", 0), "sog": s.get("shots", 0),
                "toi": round(toi / 60, 1) if toi > 60 else round(toi, 1)})
        for g in cs.get("goalies", []):
            if not g.get("gamesPlayed"):
                continue
            sa = g.get("shotsAgainst", 0)
            sv = g.get("saves", 0)
            goalies.append({
                "id": g["playerId"], "name": ("%s %s" % (dflt(g.get("firstName")), dflt(g.get("lastName")))).strip(),
                "team": a, "gp": g.get("gamesPlayed", 0), "w": g.get("wins", 0), "l": g.get("losses", 0),
                "otl": g.get("overtimeLosses", 0), "sa": sa, "sv": sv,
                "svPct": round(g.get("savePercentage") if g.get("savePercentage") is not None else (sv / sa if sa else 0), 4),
                "gaa": round(g.get("goalsAgainstAverage") or 0, 2), "so": g.get("shutouts", 0)})
    log("players: %d skaters, %d goalies" % (len(skaters), len(goalies)))

    s = str(season)
    stamp = today.isoformat()
    xgd = build_xg(games, fetch, log, xg_cache) if xg else None
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"sample": False, "seasonLabel": "%s-%s" % (s[:4], s[6:]), "asOf": stamp, "generated": stamp, "updatedAt": updated,
            "seasonGames": 84, "teams": teams, "skaters": skaters, "goalies": goalies, "games": games}
    if xgd:
        out["xg"] = xgd
        out["xgNote"] = ("Expected goals (xG) come from a model fitted on this season's shots on goal: the chance a shot becomes a goal "
                         "depends on distance, angle, and shot type. Empty-net shots and shootouts are excluded. 'Goals vs. xG' uses the same shots.")
    return out


def embed(html_path, data):
    p = Path(html_path)
    html = p.read_text(encoding="utf-8")
    pat = re.compile(r"/\*DATA_START\*/.*?/\*DATA_END\*/", re.S)
    if not pat.search(html):
        sys.exit("Could not find the /*DATA_START*/ ... /*DATA_END*/ markers in %s" % p)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    p.write_text(pat.sub(lambda m: "/*DATA_START*/const DATA = %s;/*DATA_END*/" % blob, html, count=1), encoding="utf-8")
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", default="index.html", help="dashboard file to update")
    ap.add_argument("--xg", action="store_true", help="also build expected goals (fetches every finished game's play-by-play; cached)")
    ap.add_argument("--xg-cache", default="xg_cache.json", help="where to keep per-game shot data so later runs only fetch new games")
    a = ap.parse_args()
    data = build_data(xg=a.xg, xg_cache=a.xg_cache)
    p = embed(a.html, data)
    print("Updated %s (%d KB). Season %s, %d regular-season games." % (p, p.stat().st_size // 1024, data["seasonLabel"], len(data["games"])))


if __name__ == "__main__":
    main()
