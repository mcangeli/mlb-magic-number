import sys, types, unittest

# Keep calculation tests independent of the scoreboard runtime dependencies.
statsapi = types.ModuleType("statsapi")
bullpen = types.ModuleType("bullpen")
bullpen_api = types.ModuleType("bullpen.api")
bullpen_logging = types.ModuleType("bullpen.logging")
class Dummy: pass
bullpen_api.PluginConfig = Dummy
bullpen_api.PluginData = Dummy
bullpen_api.PluginRenderer = Dummy
bullpen_api.MLBConfig = Dummy
bullpen_api.Layout = Dummy
bullpen_api.Color = Dummy
bullpen_api.PLUGIN_DEFINITION = tuple
bullpen_api.UpdateStatus = Dummy
bullpen_api.renderer = types.SimpleNamespace(graphics=object)
bullpen_logging.LOGGER = Dummy()
sys.modules.update({"statsapi": statsapi, "bullpen": bullpen, "bullpen.api": bullpen_api, "bullpen.logging": bullpen_logging})

from mlb_led_scoreboard_magic_number import TeamRecord, _head_to_head, calculate_magic_numbers


def team(i, league=103, division=1, wins=80, losses=60, rank=1, wc=1, name=None):
    return TeamRecord(i, name or f"Team {i}", name or f"Team {i}", f"T{i}", league, division, "Test", wins, losses, rank, wc, wins/(wins+losses))


class MagicNumberTests(unittest.TestCase):
    def test_h2h_advantage_reduces_magic_number(self):
        records = [
            team(1, wins=90, losses=50, rank=1, wc=99),
            team(2, division=1, wins=80, losses=60, rank=2, wc=1),
            team(3, division=2, wins=89, losses=51, rank=1, wc=99),
            team(4, division=3, wins=88, losses=52, rank=1, wc=99),
            team(5, division=1, wins=76, losses=64, rank=3, wc=2),
            team(6, division=2, wins=75, losses=65, rank=2, wc=3),
            team(7, division=3, wins=74, losses=66, rank=2, wc=4),
            team(8, division=1, wins=73, losses=67, rank=4, wc=5),
        ]
        games = [{"status":{"abstractGameState":"Final"}, "teams":{"home":{"team":{"id":1},"isWinner":True},"away":{"team":{"id":7},"isWinner":False}}}]
        numbers = {n.team.team_id:n for n in calculate_magic_numbers(records, games)}
        self.assertEqual(numbers[1].number, 163-90-66-1)
        self.assertTrue(numbers[1].tiebreak.favors_team)

    def test_h2h_tied_does_not_reduce(self):
        games = []
        for home_winner in (True, False):
            games.append({"status":{"abstractGameState":"Final"},"teams":{"home":{"team":{"id":1},"isWinner":home_winner},"away":{"team":{"id":2},"isWinner":not home_winner}}})
        tb = _head_to_head(games, 1, 2)
        self.assertFalse(tb.decided)
        self.assertFalse(tb.favors_team)

    def test_partial_h2h_lead_is_not_treated_as_decided(self):
        games = [{"status":{"abstractGameState":"Final"},"teams":{"home":{"team":{"id":1},"isWinner":True},"away":{"team":{"id":2},"isWinner":False}}}, {"status":{"abstractGameState":"Preview"},"teams":{"home":{"team":{"id":1}},"away":{"team":{"id":2}}}}]
        tb = _head_to_head(games, 1, 2)
        self.assertFalse(tb.decided)


if __name__ == "__main__":
    unittest.main()
