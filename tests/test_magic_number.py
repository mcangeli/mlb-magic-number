import unittest

from mlb_led_scoreboard_magic_number import TeamRecord, calculate_magic_numbers


def team(i, league=103, division=1, wins=80, losses=60, rank=1, name=None):
    return TeamRecord(
        team_id=i,
        name=name or f"Team {i}",
        short_name=name or f"Team {i}",
        abbreviation=f"T{i}",
        league_id=league,
        division_id=division,
        division_name="Test",
        wins=wins,
        losses=losses,
        division_rank=rank,
        win_pct=wins / (wins + losses),
    )


class MagicNumberTests(unittest.TestCase):
    def test_division_winners_and_three_wildcards_form_field(self):
        records = [
            team(1, division=1, wins=90, losses=50, rank=1),
            team(2, division=1, wins=80, losses=60, rank=2),
            team(3, division=2, wins=89, losses=51, rank=1),
            team(4, division=2, wins=78, losses=62, rank=2),
            team(5, division=3, wins=88, losses=52, rank=1),
            team(6, division=3, wins=77, losses=63, rank=2),
            team(7, division=1, wins=76, losses=64, rank=3),
            team(8, division=2, wins=75, losses=65, rank=3),
            team(9, division=3, wins=74, losses=66, rank=3),
            team(10, division=1, wins=73, losses=67, rank=4),
        ]
        numbers = calculate_magic_numbers(records)
        by_id = {n.team.team_id: n for n in numbers}
        self.assertEqual(by_id[1].cutoff_team.team_id, 10)
        self.assertEqual(by_id[1].number, 163 - 90 - 67)
        self.assertEqual(by_id[10].number, 163 - 73 - 67)

    def test_already_clinched_is_zero(self):
        records = [
            team(1, division=1, wins=100, losses=40, rank=1),
            team(2, division=1, wins=70, losses=70, rank=2),
            team(3, division=2, wins=70, losses=70, rank=1),
            team(4, division=3, wins=69, losses=71, rank=1),
            team(5, division=1, wins=68, losses=72, rank=3),
        ]
        numbers = calculate_magic_numbers(records)
        self.assertEqual(next(n.number for n in numbers if n.team.team_id == 1), 0)


if __name__ == "__main__":
    unittest.main()
