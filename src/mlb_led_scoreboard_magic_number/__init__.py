from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Optional
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
    wild_card_rank: int
    win_pct: float


@dataclass(frozen=True)
class TiebreakInfo:
    opponent_id: int
    wins: int
    losses: int
    decided: bool
    favors_team: bool
    label: str


@dataclass(frozen=True)
class MagicNumber:
    team: TeamRecord
    number: int
    cutoff_team: Optional[TeamRecord]
    tiebreak: Optional[TiebreakInfo] = None
    clinched: bool = False


def _record_from_api(record: dict[str, Any]) -> TeamRecord:
    team = record["team"]
    division = team.get("division", {})
    league = record.get("league", {})
    wins = int(record.get("wins", 0))
    losses = int(record.get("losses", 0))
    return TeamRecord(
        team_id=int(team["id"]),
        name=team.get("name", "Unknown Team"),
        short_name=team.get("teamName", team.get("name", "Unknown")),
        abbreviation=team.get("abbreviation", team.get("teamCode", "")),
        league_id=int(league.get("id", 0)),
        division_id=int(division.get("id", 0)),
        division_name=division.get("name", ""),
        wins=wins,
        losses=losses,
        division_rank=int(record.get("divisionRank", 99) or 99),
        wild_card_rank=int(record.get("wildCardRank", 99) or 99),
        win_pct=wins / max(1, wins + losses),
    )


def _sort_key(team: TeamRecord) -> tuple:
    return (-team.win_pct, -team.wins, team.losses, team.name)


def _playoff_field(records: list[TeamRecord]) -> tuple[list[TeamRecord], dict[int, TeamRecord]]:
    """Return playoff teams and the current fourth-wild-card cutoff per league."""
    by_league: dict[int, list[TeamRecord]] = {}
    for team in records:
        by_league.setdefault(team.league_id, []).append(team)

    playoff: list[TeamRecord] = []
    cutoffs: dict[int, TeamRecord] = {}
    for league_id, teams in by_league.items():
        divisions: dict[int, list[TeamRecord]] = {}
        for team in teams:
            divisions.setdefault(team.division_id, []).append(team)

        # StatsAPI's divisionRank is preferred; sort is only a fallback for
        # malformed/incomplete records.
        winners = []
        for group in divisions.values():
            ranked = sorted(group, key=lambda t: (t.division_rank, *_sort_key(t)))
            winners.append(ranked[0])
        winner_ids = {t.team_id for t in winners}
        remaining = [t for t in teams if t.team_id not in winner_ids]
        remaining.sort(key=lambda t: (t.wild_card_rank if t.wild_card_rank < 99 else 99, *_sort_key(t)))
        playoff.extend(winners)
        playoff.extend(remaining[:3])
        if len(remaining) >= 4:
            cutoffs[league_id] = remaining[3]

    return playoff, cutoffs


def _head_to_head(games: list[dict[str, Any]], team_id: int, opponent_id: int) -> TiebreakInfo:
    wins = losses = 0
    completed = 0
    scheduled = 0
    for game in games:
        status = game.get("status", {}).get("abstractGameState")
        teams = game.get("teams", {})
        home = teams.get("home", {}).get("team", {}).get("id")
        away = teams.get("away", {}).get("team", {}).get("id")
        if {home, away} != {team_id, opponent_id}:
            continue
        scheduled += 1
        if status != "Final":
            continue
        completed += 1
        winner = teams.get("home", {}).get("isWinner")
        if home == team_id:
            if winner:
                wins += 1
            else:
                losses += 1
        else:
            if winner:
                losses += 1
            else:
                wins += 1

    # A season-series tiebreak is useful only when the target has enough of
    # a lead that the remaining scheduled games cannot overturn it.
    remaining = max(0, scheduled - completed)
    decided = completed > 0 and (wins > losses + remaining or losses > wins + remaining)
    return TiebreakInfo(
        opponent_id=opponent_id,
        wins=wins,
        losses=losses,
        decided=decided,
        favors_team=decided and wins > losses,
        label="H2H+" if decided and wins > losses else ("H2H-" if decided else "H2H"),
    )


def calculate_magic_numbers(records: list[TeamRecord], games: Optional[list[dict[str, Any]]] = None) -> list[MagicNumber]:
    playoff, cutoffs = _playoff_field(records)
    playoff_ids = {t.team_id for t in playoff}
    result: list[MagicNumber] = []

    for team in records:
        cutoff = cutoffs.get(team.league_id)
        if cutoff is None:
            result.append(MagicNumber(team, 0, None, None, True))
            continue

        # 163 - wins - cutoff losses is the ordinary magic number for beating
        # the cutoff outright. If the target has already clinched the head-to-
        # head tiebreak over that cutoff, a tied record is enough, reducing
        # the number by one. We deliberately do not infer an advantage from a
        # partially completed season series.
        tb = _head_to_head(games or [], team.team_id, cutoff.team_id) if games is not None else None
        adjustment = 1 if tb and tb.favors_team else 0
        number = max(0, 163 - team.wins - cutoff.losses - adjustment)
        result.append(MagicNumber(team, number, cutoff, tb, team.team_id in playoff_ids and number == 0))

    return result


def _fetch_standings() -> list[TeamRecord]:
    raw = statsapi.get(
        "standings",
        {
            "leagueId": "103,104",
            "season": time.strftime("%Y"),
            "standingsTypes": "regularSeason",
            "hydrate": "team(division)",
        },
    )
    records: list[TeamRecord] = []
    for group in raw.get("records", []):
        league_id = int(group.get("league", {}).get("id", 0))
        for record in group.get("teamRecords", []):
            item = dict(record)
            item["league"] = {"id": league_id}
            records.append(_record_from_api(item))
    return records


def _fetch_season_games() -> list[dict[str, Any]]:
    year = time.strftime("%Y")
    raw = statsapi.get(
        "schedule",
        {
            "sportId": 1,
            "season": year,
            "gameTypes": "R",
            "startDate": f"{year}-03-01",
            "endDate": f"{year}-11-30",
            "hydrate": "team",
        },
    )
    games: list[dict[str, Any]] = []
    for day in raw.get("dates", []):
        games.extend(day.get("games", []))
    return games


class Config(api.PluginConfig):
    def __init__(self, base: api.MLBConfig) -> None:
        plugin = base.plugin_config or {}
        self.team_query = str(plugin.get("team") or "").strip()
        self.refresh_seconds = max(60.0, float(plugin.get("refresh_seconds", 300)))
        self.seconds_per_team = max(2.0, float(plugin.get("seconds_per_team", 6)))
        self.team_abbreviation = bool(plugin.get("team_abbreviation", False))
        self.show_cutoff = bool(plugin.get("show_cutoff", True))


class Data(api.PluginData):
    def __init__(self, config: Config) -> None:
        self.config = config
        self.magic_numbers: list[MagicNumber] = []
        self.error = False
        self.error_message = ""
        self._last_update = 0.0
        self._selected_index = 0
        self._last_selection = 0.0

    def update(self, force: bool = False) -> api.UpdateStatus:
        now = time.monotonic()
        if not force and now - self._last_update < self.config.refresh_seconds:
            return api.UpdateStatus.DEFERRED
        try:
            records = _fetch_standings()
            games = _fetch_season_games()
            numbers = calculate_magic_numbers(records, games)

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
                numbers.sort(key=lambda n: (n.team.league_id, n.team.division_rank != 1, -n.team.win_pct, n.team.name))

            self.magic_numbers = numbers
            self.error = False
            self.error_message = ""
            self._last_update = now
            self._selected_index = 0
            self._last_selection = now
            LOGGER.debug("Magic-number data updated: %d teams", len(numbers))
            return api.UpdateStatus.SUCCESS
        except Exception as exc:
            self.error = True
            self.error_message = str(exc)
            LOGGER.warning("Magic-number update failed: %s", exc)
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
    def __init__(self, config: Config, layout: api.Layout, colors: api.Color) -> None:
        self.config = config
        self.colors = colors
        # Use the scoreboard's existing standings font. No plugin-specific font
        # configuration is required.
        self.font = layout.font("standings.font")["font"]
        self.width = 64
        self.height = 32
        self.scroll_gap = 10

    def wait_time(self) -> float:
        # The framework's scrolling position advances between render calls.
        return 0.12

    def can_render(self, data: Data) -> bool:
        return data.error or bool(data.magic_numbers)

    @staticmethod
    def _text_width(graphics: api.renderer.graphics, canvas: "Canvas", font: Any, text: str) -> int:
        # Draw off-screen solely to obtain the renderer's actual pixel width.
        return int(graphics.DrawText(canvas, font, -1000, 0, graphics.Color(0, 0, 0), text))

    def _draw_text(self, data: Data, canvas: "Canvas", graphics: api.renderer.graphics, text: str,
                   y: int, color: Any, scrolling_text_pos: int, center: bool = False) -> int:
        width = self._text_width(graphics, canvas, self.font, text)
        if width <= self.width:
            x = max(0, (self.width - width) // 2) if center else 0
            graphics.DrawText(canvas, self.font, x, y, color, text)
            return scrolling_text_pos

        cycle = width + self.scroll_gap
        offset = scrolling_text_pos % cycle
        x = self.width - offset
        graphics.DrawText(canvas, self.font, x, y, color, text)
        if x + width < self.width:
            graphics.DrawText(canvas, self.font, x + cycle, y, color, text)
        return scrolling_text_pos

    def render(self, data: Data, canvas: "Canvas", graphics: api.renderer.graphics, scrolling_text_pos: int) -> None:
        canvas.Fill(0, 0, 0)
        white = graphics.Color(255, 255, 255)
        yellow = graphics.Color(255, 215, 0)
        cyan = graphics.Color(0, 220, 255)
        green = graphics.Color(50, 220, 50)
        orange = graphics.Color(255, 140, 0)
        gray = graphics.Color(150, 150, 150)
        dim = graphics.Color(60, 60, 60)

        if data.error:
            graphics.DrawText(canvas, self.font, 2, 9, orange, "MAGIC NUMBER")
            graphics.DrawText(canvas, self.font, 2, 19, white, "DATA ERROR")
            return

        item = data.current
        if item is None:
            graphics.DrawText(canvas, self.font, 2, 16, white, "NO DATA")
            return

        team = item.team
        name = team.abbreviation if self.config.team_abbreviation and team.abbreviation else team.short_name
        record = f"{team.wins}-{team.losses}"

        # Header: team + record, with long names scrolling rather than clipping.
        header = f"{name}  {record}"
        self._draw_text(data, canvas, graphics, header, 8, cyan, scrolling_text_pos, center=True)
        graphics.DrawLine(canvas, 0, 10, self.width - 1, 10, dim)

        if item.clinched or item.number == 0:
            graphics.DrawText(canvas, self.font, 2, 20, green, "CLINCHED")
        else:
            graphics.DrawText(canvas, self.font, 2, 20, white, "PLAYOFF")
            graphics.DrawText(canvas, self.font, 42, 20, yellow, f"M#{item.number}")

        if self.config.show_cutoff and item.cutoff_team:
            cutoff = item.cutoff_team
            tb = item.tiebreak.label if item.tiebreak else ""
            cutoff_text = f"CUT {cutoff.abbreviation or cutoff.short_name} {cutoff.wins}-{cutoff.losses} {tb}".strip()
            self._draw_text(data, canvas, graphics, cutoff_text, 30, gray, scrolling_text_pos, center=False)
        elif item.tiebreak and item.tiebreak.label:
            graphics.DrawText(canvas, self.font, 2, 30, gray, item.tiebreak.label)


def load() -> api.PLUGIN_DEFINITION:
    return Config, Data, Renderer
