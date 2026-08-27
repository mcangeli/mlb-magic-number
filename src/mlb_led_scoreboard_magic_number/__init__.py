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
    def __init__(self, config: Config, layout: api.Layout, colors: api.Color) -> None:
        self.config = config
        self.colors = colors
        self.font = layout.font("magic_number.font")
        self.width = 64

    def wait_time(self) -> float:
        return 0.5

    @staticmethod
    def _fit(text: str, width: int) -> str:
        return text if len(text) <= width else text[:width]

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
                canvas,
                self.font["font"],
                0,
                10,
                graphics.Color(255, 0, 0),
                "DATA ERROR",
            )
            return

        item = data.current
        if item is None:
            graphics.DrawText(
                canvas,
                self.font["font"],
                0,
                10,
                graphics.Color(255, 255, 255),
                "NO DATA",
            )
            return

        team = item.team
        team_name = team.abbreviation if self.config.team_abbreviation and team.abbreviation else team.short_name
        if len(team_name) > 10:
            team_name = team_name[:10]

        graphics.DrawText(
            canvas,
            self.font["font"],
            0,
            9,
            graphics.Color(255, 255, 255),
            "PLAYOFF",
        )
        graphics.DrawText(
            canvas,
            self.font["font"],
            0,
            19,
            graphics.Color(255, 255, 255),
            self._fit(team_name, 10),
        )
        graphics.DrawText(
            canvas,
            self.font["font"],
            0,
            31,
            graphics.Color(255, 255, 0),
            f"MAGIC {item.number}",
        )


def load() -> api.PLUGIN_DEFINITION:
    return Config, Data, Renderer
