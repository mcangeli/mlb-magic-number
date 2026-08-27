from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
from datetime import date
import time

import statsapi
import bullpen.api as api
from bullpen.logging import LOGGER

if TYPE_CHECKING:
    from RGBMatrixEmulator.emulation.canvas import Canvas


@dataclass(frozen=True)
class TeamRecord:
    team_id: int
    name: str
    short_name: str
    abbreviation: str
    league_id: int
    division_id: int
    division_name: str
    wins: int
    losses: int
    division_rank: int
    win_pct: float


@dataclass(frozen=True)
class MagicNumber:
    team: TeamRecord
    number: int
    cutoff_team: Optional[TeamRecord]
    tiebreak_note: str = ""


def _record_from_api(record: dict[str, Any]) -> TeamRecord:
    team = record["team"]
    division = team.get("division", {})
    wins = int(record["wins"])
    losses = int(record["losses"])
    return TeamRecord(
        team_id=int(team["id"]),
        name=team["name"],
        short_name=team.get("teamName", team["name"]),
        abbreviation=team.get("abbreviation", team.get("teamCode", "")),
        league_id=int(record.get("league", {}).get("id", 0)),
        division_id=int(division.get("id", 0)),
        division_name=division.get("name", ""),
        wins=wins,
        losses=losses,
        division_rank=int(record.get("divisionRank", 99)),
        win_pct=wins / max(1, wins + losses),
    )


def _playoff_field(records: list[TeamRecord]) -> tuple[list[TeamRecord], list[TeamRecord]]:
    """Return (playoff teams, cutoff teams) grouped by league.

    MLB's current format is three division winners plus three wild cards per
    league. Division winners are selected first; the wild cards are the best
    three remaining records in the league.
    """
    by_league: dict[int, list[TeamRecord]] = {}
    for team in records:
        by_league.setdefault(team.league_id, []).append(team)

    playoff: list[TeamRecord] = []
    cutoffs: list[TeamRecord] = []

    for teams in by_league.values():
        divisions: dict[int, list[TeamRecord]] = {}
        for team in teams:
            divisions.setdefault(team.division_id, []).append(team)

        division_winners = [
            sorted(group, key=lambda t: (-t.win_pct, -t.wins, t.losses, t.name))[0]
            for group in divisions.values()
        ]
        division_ids = {t.team_id for t in division_winners}
        wildcards = sorted(
            [t for t in teams if t.team_id not in division_ids],
            key=lambda t: (-t.win_pct, -t.wins, t.losses, t.name),
        )

        playoff.extend(division_winners)
        playoff.extend(wildcards[:3])
        if len(wildcards) > 3:
            cutoffs.append(wildcards[3])

    return playoff, cutoffs


def _build_head_to_head(games: list[dict[str, Any]]) -> dict[tuple[int, int], tuple[int, int]]:
    """Return completed regular-season head-to-head records.

    Values are keyed as (team_a, team_b) -> (team_a_wins, team_a_losses).
    MLB's current postseason tiebreak procedure starts with head-to-head
    record when applicable, so this is kept separate from overall standings.
    """
    h2h: dict[tuple[int, int], list[int]] = {}
    for game in games:
        if str(game.get("game_type", "")) not in ("R", "Regular season"):
            continue
        if str(game.get("status", "")).lower() not in ("final", "completed early"):
            continue
        try:
            away = int(game["away_id"])
            home = int(game["home_id"])
        except (KeyError, TypeError, ValueError):
            continue
        winner = str(game.get("winning_team", ""))
        if not winner:
            continue
        key = (away, home)
        if key not in h2h:
            h2h[key] = [0, 0]
        if winner == game.get("away_name"):
            h2h[key][0] += 1
        elif winner == game.get("home_name"):
            h2h[key][1] += 1
        else:
            # Some API responses can omit the exact winner name. Fall back to
            # the final score when available.
            try:
                if int(game.get("away_score", -1)) > int(game.get("home_score", -1)):
                    h2h[key][0] += 1
                elif int(game.get("home_score", -1)) > int(game.get("away_score", -1)):
                    h2h[key][1] += 1
            except (TypeError, ValueError):
                pass
    return {k: (v[0], v[1]) for k, v in h2h.items()}


def _build_intradivision(
    games: list[dict[str, Any]], records: list[TeamRecord]
) -> dict[tuple[int, int], tuple[int, int]]:
    """Build intradivision records used after a tied H2H series."""
    division_by_team = {t.team_id: t.division_id for t in records}
    result: dict[tuple[int, int], list[int]] = {}
    for game in games:
        if str(game.get("game_type", "")) not in ("R", "Regular season"):
            continue
        if str(game.get("status", "")).lower() not in ("final", "completed early"):
            continue
        try:
            away = int(game["away_id"]); home = int(game["home_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if division_by_team.get(away) != division_by_team.get(home):
            continue
        if away not in division_by_team or home not in division_by_team:
            continue
        key = (away, home)
        result.setdefault(key, [0, 0])
        winner = str(game.get("winning_team", ""))
        if winner == game.get("away_name"):
            result[key][0] += 1
        elif winner == game.get("home_name"):
            result[key][1] += 1
        else:
            try:
                away_score = int(game.get("away_score", -1)); home_score = int(game.get("home_score", -1))
                if away_score > home_score:
                    result[key][0] += 1
                elif home_score > away_score:
                    result[key][1] += 1
            except (TypeError, ValueError):
                pass
    return {k: (v[0], v[1]) for k, v in result.items()}


def _head_to_head_result(a: TeamRecord, b: TeamRecord, h2h: dict[tuple[int, int], tuple[int, int]]) -> int:
    """Return 1 if a leads H2H, -1 if b leads, 0 if tied/unknown."""
    wins, losses = h2h.get((a.team_id, b.team_id), (0, 0))
    if (a.team_id, b.team_id) not in h2h:
        reverse_wins, reverse_losses = h2h.get((b.team_id, a.team_id), (0, 0))
        wins, losses = reverse_losses, reverse_wins
    if wins > losses:
        return 1
    if losses > wins:
        return -1
    return 0


def calculate_magic_numbers(
    records: list[TeamRecord],
    head_to_head: Optional[dict[tuple[int, int], tuple[int, int]]] = None,
    intradivision: Optional[dict[tuple[int, int], tuple[int, int]]] = None,
) -> list[MagicNumber]:
    """Calculate playoff magic numbers, accounting for H2H tiebreaks.

    The current fourth-wild-card team remains the practical playoff cutoff.
    Normally the target must finish one game ahead of that club, producing:

        163 - target_wins - cutoff_losses

    If the target currently owns the head-to-head tiebreaker over the cutoff,
    a tied final record is sufficient, so the number is reduced by one:

        162 - target_wins - cutoff_losses

    If the head-to-head series is tied or the target trails, the calculation
    remains conservative. MLB's tiebreak hierarchy begins with head-to-head,
    followed by intradivision record when applicable. We deliberately do not
    infer an advantage from an incomplete/tied H2H series.
    """
    h2h = head_to_head or {}
    division = intradivision or {}
    playoff, cutoffs = _playoff_field(records)
    cutoff_by_league = {t.league_id: t for t in cutoffs}
    result: list[MagicNumber] = []

    for team in records:
        cutoff = cutoff_by_league.get(team.league_id)
        if cutoff is None:
            result.append(MagicNumber(team, 0, None, "CLINCHED"))
            continue

        h2h_result = _head_to_head_result(team, cutoff, h2h)
        tiebreak_result = h2h_result
        tiebreak_label = "H2H"
        if tiebreak_result == 0 and team.division_id == cutoff.division_id:
            tiebreak_result = _head_to_head_result(team, cutoff, division)
            tiebreak_label = "DIV"
        advantage = tiebreak_result == 1
        number = max(0, 163 - team.wins - cutoff.losses - (1 if advantage else 0))
        if number == 0:
            note = "CLINCHED"
        elif advantage:
            note = f"{tiebreak_label}+"
        elif tiebreak_result == -1:
            note = f"{tiebreak_label}-"
        else:
            note = "H2H TIED" if (team.team_id, cutoff.team_id) in h2h or (cutoff.team_id, team.team_id) in h2h else ""
        result.append(MagicNumber(team, number, cutoff, note))
    return result


class Config(api.PluginConfig):
    def __init__(self, base: api.MLBConfig) -> None:
        plugin = base.plugin_config or {}
        self.team_query = str(plugin.get("team") or "").strip()
        self.refresh_seconds = max(30.0, float(plugin.get("refresh_seconds", 300)))
        self.seconds_per_team = max(1.0, float(plugin.get("seconds_per_team", 4)))
        self.team_abbreviation = bool(plugin.get("team_abbreviation", False))


class Data(api.PluginData):
    def __init__(self, config: Config) -> None:
        self.config = config
        self.magic_numbers: list[MagicNumber] = []
        self.error = False
        self._last_update = 0.0
        self._selected_index = 0
        self._last_selection = 0.0

    def update(self, force: bool = False) -> api.UpdateStatus:
        now = time.monotonic()
        if not force and now - self._last_update < self.config.refresh_seconds:
            return api.UpdateStatus.DEFERRED

        try:
            divisions = statsapi.standings_data(
                leagueId="103,104",
                division="all",
                include_wildcard=True,
            )
            records: list[TeamRecord] = []
            for division in divisions.values():
                for raw in division["teams"]:
                    # standings_data returns a normalized record but does not
                    # retain all of the raw API fields needed by the plugin.
                    records.append(
                        TeamRecord(
                            team_id=int(raw["team_id"]),
                            name=raw["name"],
                            short_name=raw["name"].split()[-1],
                            abbreviation="",
                            league_id=103 if str(raw.get("league_rank", "")).startswith("1") else 104,
                            division_id=0,
                            division_name=division["div_name"],
                            wins=int(raw["w"]),
                            losses=int(raw["l"]),
                            division_rank=int(raw["div_rank"]),
                            win_pct=int(raw["w"]) / max(1, int(raw["w"]) + int(raw["l"])),
                        )
                    )

            # standings_data intentionally hides league/division IDs in its
            # public normalized result. Fetch the same endpoint through the
            # lower-level StatsAPI call so the league is unambiguous.
            raw = statsapi.get(
                "standings",
                {
                    "leagueId": "103,104",
                    "season": time.strftime("%Y"),
                    "standingsTypes": "regularSeason",
                    "hydrate": "team(division)",
                },
            )
            records = []
            for group in raw.get("records", []):
                league_id = int(group["league"]["id"])
                for record in group["teamRecords"]:
                    record = dict(record)
                    record["league"] = {"id": league_id}
                    records.append(_record_from_api(record))

            # Pull the completed regular-season schedule once per refresh so
            # the magic number can account for MLB's head-to-head tiebreak.
            season_start = f"{time.strftime('%Y')}-03-01"
            today = date.today().isoformat()
            games = statsapi.schedule(
                start_date=season_start,
                end_date=today,
                season=time.strftime("%Y"),
                include_series_status=False,
            )
            head_to_head = _build_head_to_head(games)
            intradivision = _build_intradivision(games, records)
            numbers = calculate_magic_numbers(records, head_to_head, intradivision)

            if self.config.team_query:
                matches = statsapi.lookup_team(self.config.team_query)
                if not matches:
                    raise ValueError(f"MLB team not found: {self.config.team_query}")
                team_id = int(matches[0]["id"])
                numbers = [n for n in numbers if n.team.team_id == team_id]
                if not numbers:
                    raise ValueError(f"No standings record found for: {self.config.team_query}")
            else:
                playoff, _ = _playoff_field(records)
                playoff_ids = {t.team_id for t in playoff}
                numbers = [n for n in numbers if n.team.team_id in playoff_ids]
                numbers.sort(
                    key=lambda n: (
                        n.team.league_id,
                        n.team.division_rank != 1,
                        -n.team.win_pct,
                        n.team.name,
                    )
                )

            self.magic_numbers = numbers
            self.error = False
            self._last_update = now
            self._selected_index = 0
            self._last_selection = now
            return api.UpdateStatus.SUCCESS
        except Exception as exc:
            LOGGER.warning("Magic-number update failed: %s", exc)
            self.error = True
            return api.UpdateStatus.FAILURE

    @property
    def current(self) -> Optional[MagicNumber]:
        if not self.magic_numbers:
            return None
        now = time.monotonic()
        if len(self.magic_numbers) > 1 and now - self._last_selection >= self.config.seconds_per_team:
            self._selected_index = (self._selected_index + 1) % len(self.magic_numbers)
            self._last_selection = now
        return self.magic_numbers[self._selected_index]


class Renderer(api.PluginRenderer):
    """Compact 64x32 playoff display using the scoreboard standings font."""

    def __init__(self, config: Config, layout: api.Layout, colors: api.Color) -> None:
        self.config = config
        self.colors = colors
        self.font = layout.font("standings.font")

    def wait_time(self) -> float:
        return 0.5

    @staticmethod
    def _fit(text: str, width: int) -> str:
        return text if len(text) <= width else text[:width]

    def _text(self, canvas, graphics, x, y, text, color):
        graphics.DrawText(canvas, self.font["font"], x, y, color, text)

    def render(self, data: Data, canvas: "Canvas", graphics: api.renderer.graphics, scrolling_text_pos: int) -> None:
        canvas.Fill(0, 0, 0)
        white = graphics.Color(235, 235, 235)
        yellow = graphics.Color(255, 215, 0)
        cyan = graphics.Color(0, 210, 230)
        green = graphics.Color(60, 220, 80)
        gray = graphics.Color(145, 145, 145)
        dim = graphics.Color(55, 55, 55)
        red = graphics.Color(255, 70, 70)

        if data.error:
            self._text(canvas, graphics, 2, 11, "MLB DATA", red)
            self._text(canvas, graphics, 2, 22, "UNAVAILABLE", red)
            return

        item = data.current
        if item is None:
            self._text(canvas, graphics, 2, 17, "NO STANDINGS", white)
            return

        team = item.team
        label = team.abbreviation or team.short_name.upper()[:3]
        record = f"{team.wins}-{team.losses}"
        cutoff = item.cutoff_team

        # Header: team + record.
        self._text(canvas, graphics, 1, 8, self._fit(label.upper(), 4), cyan)
        self._text(canvas, graphics, 21, 8, record, gray)
        self._text(canvas, graphics, 44, 8, "PLAYOFF", white)
        graphics.DrawLine(canvas, 0, 10, 63, 10, dim)

        if item.number == 0:
            self._text(canvas, graphics, 2, 20, "CLINCHED", green)
            self._text(canvas, graphics, 2, 30, "PLAYOFF SPOT", white)
            return

        # Main number gets the visual emphasis.
        self._text(canvas, graphics, 2, 21, "MAGIC", white)
        self._text(canvas, graphics, 30, 21, str(item.number), yellow)

        if cutoff is not None:
            cutoff_label = cutoff.abbreviation or cutoff.short_name.upper()[:3]
            self._text(canvas, graphics, 2, 30, "CUTOFF", gray)
            self._text(canvas, graphics, 28, 30, f"{cutoff_label} {cutoff.wins}-{cutoff.losses}", white)
            if item.tiebreak_note in ("H2H+", "DIV+"):
                self._text(canvas, graphics, 54, 30, item.tiebreak_note[:3], cyan)
            elif item.tiebreak_note in ("H2H-", "DIV-"):
                self._text(canvas, graphics, 54, 30, item.tiebreak_note[:3], red)


def load() -> api.PLUGIN_DEFINITION:
    return Config, Data, Renderer
