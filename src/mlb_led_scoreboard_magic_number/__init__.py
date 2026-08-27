from __future__ import annotations

from dataclasses import dataclass
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
    win_pct: float


@dataclass(frozen=True)
class MagicNumber:
    team: TeamRecord
    number: int
    cutoff_team: Optional[TeamRecord]


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


def calculate_magic_numbers(records: list[TeamRecord]) -> list[MagicNumber]:
    """Calculate playoff magic numbers for every team.

    The cutoff is the best current non-playoff team in the target's league.
    """
    playoff, cutoffs = _playoff_field(records)
    cutoff_by_league = {t.league_id: t for t in cutoffs}
    result: list[MagicNumber] = []

    for team in records:
        cutoff = cutoff_by_league.get(team.league_id)
        if cutoff is None:
            number = 0
        else:
            number = max(0, 163 - team.wins - cutoff.losses)
        result.append(MagicNumber(team, number, cutoff))
    return result


class Config(api.PluginConfig):
    def __init__(self, base: api.MLBConfig) -> None:
        plugin = base.plugin_config or {}
        self.team_query = str(plugin.get("team") or "").strip()
        self.refresh_seconds = max(30.0, float(plugin.get("refresh_seconds", 300)))
        # Time each standings page remains on screen in automatic mode.
        # Keep seconds_per_team as a backwards-compatible alias.
        self.page_duration = max(1.0, float(plugin.get("page_duration", plugin.get("seconds_per_team", 4))))
        self.team_abbreviation = bool(plugin.get("team_abbreviation", False))


class Data(api.PluginData):
    def __init__(self, config: Config) -> None:
        self.config = config
        self.magic_numbers: list[MagicNumber] = []
        self.error = False
        self._last_update = 0.0

    def update(self, force: bool = False) -> api.UpdateStatus:
        now = time.monotonic()
        if not force and now - self._last_update < self.config.refresh_seconds:
            return api.UpdateStatus.DEFERRED

        try:
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
                league_id = int(group["league"]["id"])
                for record in group["teamRecords"]:
                    record = dict(record)
                    record["league"] = {"id": league_id}
                    records.append(_record_from_api(record))

            numbers = calculate_magic_numbers(records)

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
            return api.UpdateStatus.SUCCESS
        except Exception as exc:
            LOGGER.warning("Magic-number update failed: %s", exc)
            self.error = True
            return api.UpdateStatus.FAILURE


class Renderer(api.PluginRenderer):
    """Render a compact, baseball-scoreboard-style magic-number display.

    Automatic mode uses four pages, matching the layout of the reference
    renderer: AL/NL division leaders followed by AL/NL wild-card leaders.
    Each page contains up to three teams.
    """

    def __init__(self, config: Config, layout: api.Layout, colors: api.Color) -> None:
        self.config = config
        self.colors = colors
        self.font = layout.font("standings.font")
        self.width = 64
        self.current_page_idx = 0
        self.last_page_switch = time.monotonic()
        self._is_complete = False

    def wait_time(self) -> float:
        return 0.25

    def reset(self) -> None:
        self.current_page_idx = 0
        self.last_page_switch = time.monotonic()
        self._is_complete = False

    def is_complete(self) -> bool:
        # A configured team is one screen and is therefore immediately
        # complete. Automatic mode completes after all four pages have shown.
        return bool(self.config.team_query) or self._is_complete

    @staticmethod
    def _fit(text: str, width: int) -> str:
        return text if len(text) <= width else text[:width]

    @staticmethod
    def _league_name(league_id: int) -> str:
        return "AL" if league_id == 103 else "NL" if league_id == 104 else "MLB"

    def _color(self, graphics, r: int, g: int, b: int):
        return graphics.Color(r, g, b)

    def render(
        self,
        data: Data,
        canvas: "Canvas",
        graphics: api.renderer.graphics,
        scrolling_text_pos: int,
    ) -> None:
        canvas.Fill(0, 0, 0)

        if data.error:
            graphics.DrawText(
                canvas, self.font["font"], 1, 16,
                self._color(graphics, 255, 80, 80), "DATA ERROR",
            )
            return

        if not data.magic_numbers:
            graphics.DrawText(
                canvas, self.font["font"], 1, 16,
                self._color(graphics, 255, 255, 255), "NO DATA",
            )
            return

        if self.config.team_query:
            self._render_single_team(data.magic_numbers[0], canvas, graphics)
        else:
            self._render_standings_page(data.magic_numbers, canvas, graphics)

    def _render_single_team(self, item: MagicNumber, canvas: "Canvas", graphics) -> None:
        team = item.team
        white = self._color(graphics, 255, 255, 255)
        yellow = self._color(graphics, 255, 215, 0)
        cyan = self._color(graphics, 0, 220, 255)
        gray = self._color(graphics, 160, 160, 160)
        dim_gray = self._color(graphics, 60, 60, 60)

        abbrev = (team.abbreviation or team.short_name[:3]).upper()[:3]
        record = f"({team.wins}-{team.losses})"

        # Header: team abbreviation and record, matching the compact reference.
        graphics.DrawText(canvas, self.font["font"], 2, 8, yellow, abbrev)
        graphics.DrawText(canvas, self.font["font"], 22, 8, white, record)
        graphics.DrawLine(canvas, 0, 10, self.width - 1, 10, dim_gray)

        graphics.DrawText(canvas, self.font["font"], 2, 18, cyan, "PLAYOFF:")
        graphics.DrawText(canvas, self.font["font"], 38, 18, yellow, f"M# {item.number}")

        # The plugin calculates a playoff-berth number, not a separate
        # division-winning number, so avoid presenting the same value twice.
        graphics.DrawText(canvas, self.font["font"], 2, 28, cyan, "CUTOFF:")
        cutoff = item.cutoff_team
        cutoff_text = (
            f"{cutoff.abbreviation[:3].upper()} {cutoff.wins}-{cutoff.losses}"
            if cutoff is not None
            else "N/A"
        )
        graphics.DrawText(canvas, self.font["font"], 38, 28, gray, cutoff_text)

    def _build_pages(self, numbers: list[MagicNumber]):
        by_id = {n.team.team_id: n for n in numbers}
        records = [n.team for n in numbers]

        pages = []
        for league_id, league_label in ((103, "AL"), (104, "NL")):
            league = [t for t in records if t.league_id == league_id]
            division_leaders = sorted(
                [t for t in league if t.division_rank == 1],
                key=lambda t: (-t.win_pct, t.name),
            )[:3]
            if division_leaders:
                pages.append((f"{league_label} DIV LEADERS", [by_id[t.team_id] for t in division_leaders]))

            div_ids = {t.team_id for t in division_leaders}
            wild_cards = sorted(
                [t for t in league if t.team_id not in div_ids],
                key=lambda t: (-t.win_pct, -t.wins, t.losses, t.name),
            )[:3]
            if wild_cards:
                pages.append((f"{league_label} WILD CARD", [by_id[t.team_id] for t in wild_cards]))

        return pages

    def _render_standings_page(self, numbers: list[MagicNumber], canvas: "Canvas", graphics) -> None:
        pages = self._build_pages(numbers)
        if not pages:
            graphics.DrawText(
                canvas, self.font["font"], 1, 16,
                self._color(graphics, 255, 255, 255), "NO STANDINGS",
            )
            return

        now = time.monotonic()
        if now - self.last_page_switch >= self.config.page_duration:
            self.current_page_idx += 1
            self.last_page_switch = now
            if self.current_page_idx >= len(pages):
                self.current_page_idx = 0
                self._is_complete = True

        title, teams = pages[self.current_page_idx % len(pages)]

        white = self._color(graphics, 255, 255, 255)
        yellow = self._color(graphics, 255, 215, 0)
        cyan = self._color(graphics, 0, 220, 255)
        gray = self._color(graphics, 160, 160, 160)
        dim_gray = self._color(graphics, 60, 60, 60)
        green = self._color(graphics, 50, 220, 50)

        graphics.DrawText(canvas, self.font["font"], 2, 6, cyan, title)
        graphics.DrawLine(canvas, 0, 8, self.width - 1, 8, dim_gray)

        for i, item in enumerate(teams[:3]):
            y = (15, 23, 31)[i]
            team = item.team
            abbrev = (team.abbreviation or team.short_name[:3]).upper()[:3]

            graphics.DrawText(canvas, self.font["font"], 1, y, white, abbrev)
            graphics.DrawText(canvas, self.font["font"], 18, y, gray, f"{team.wins}-{team.losses}")

            if item.number == 0:
                graphics.DrawText(canvas, self.font["font"], 42, y, green, "CLN")
            else:
                graphics.DrawText(canvas, self.font["font"], 42, y, yellow, f"M:{item.number:>2}")

def load() -> api.PLUGIN_DEFINITION:
    return Config, Data, Renderer
