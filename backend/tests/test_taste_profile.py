import unittest

from app.enrich import EnrichedFilm
from app.scraper import ScrapedFilm, ScrapedProfile
from app.taste_profile import build_taste_profile, taste_analysis_signal, taste_source_fingerprint


class TasteProfileTests(unittest.TestCase):
    def test_favorite_director_prefers_explicitly_loved_films(self):
        watched = [
            EnrichedFilm(
                title="Loved One",
                director="Loved Director",
                genres=["Drama"],
                keywords=["memory"],
                user_rating=5.0,
            ),
            EnrichedFilm(
                title="Loved Two",
                director="Loved Director",
                genres=["Drama"],
                user_rating=4.5,
            ),
            EnrichedFilm(
                title="Disliked One",
                director="Frequent But Disliked",
                genres=["Horror"],
                user_rating=1.0,
            ),
            EnrichedFilm(
                title="Disliked Two",
                director="Frequent But Disliked",
                genres=["Horror"],
                user_rating=1.5,
            ),
            EnrichedFilm(
                title="Disliked Three",
                director="Frequent But Disliked",
                genres=["Horror"],
                user_rating=2.0,
            ),
        ]

        profile = build_taste_profile(watched)

        self.assertEqual(profile.favorite_director, "Loved Director")
        self.assertEqual(profile.top_directors, ["Loved Director"])
        self.assertEqual(profile.top_genres[0], "Drama")
        self.assertIn("Loved Director", profile.summary)
        self.assertEqual(profile.rated_count, 5)

    def test_unrated_profile_uses_frequency_and_reports_low_confidence(self):
        watched = [
            EnrichedFilm(title=f"Film {index}", director="Same Director")
            for index in range(3)
        ]

        profile = build_taste_profile(watched)

        self.assertEqual(profile.favorite_director, "Same Director")
        self.assertEqual(profile.top_directors, ["Same Director"])
        self.assertEqual(profile.confidence_level, "low")
        self.assertEqual(profile.sample_size, 3)

    def test_empty_profile_is_valid(self):
        profile = build_taste_profile([])

        self.assertEqual(profile.sample_size, 0)
        self.assertEqual(profile.confidence_score, 0)

    def test_top_three_directors_keep_rating_aware_order(self):
        # Equal watch counts (2 each) → the tie-break is the user's own average.
        watched = [
            EnrichedFilm(title="A1", director="First", user_rating=5.0),
            EnrichedFilm(title="A2", director="First", user_rating=4.6),
            EnrichedFilm(title="B1", director="Second", user_rating=4.4),
            EnrichedFilm(title="B2", director="Second", user_rating=4.2),
            EnrichedFilm(title="C1", director="Third", user_rating=4.0),
            EnrichedFilm(title="C2", director="Third", user_rating=3.6),
            EnrichedFilm(title="D", director="Once Only", user_rating=5.0),
        ]

        profile = build_taste_profile(watched)

        self.assertEqual(profile.top_directors[:3], ["First", "Second", "Third"])
        self.assertEqual(profile.favorite_director, "First")
        # A single watched film is not a "favorite director".
        self.assertNotIn("Once Only", profile.top_directors)
        self.assertEqual(profile.algorithm_version, "taste-v5-fav4-top5-directors")

    def test_fav4_and_the_five_most_watched_directors_define_the_analysis_signal(self):
        # Six directors, so the sixth is genuinely outside the top five.
        watched = [
            EnrichedFilm(title="A1", slug="a1", director="Frequent A", genres=["Drama"]),
            EnrichedFilm(title="A2", slug="a2", director="Frequent A", genres=["Drama"]),
            EnrichedFilm(title="A3", slug="a3", director="Frequent A", genres=["Drama"]),
            EnrichedFilm(title="B1", slug="b1", director="Frequent B", genres=["Mystery"]),
            EnrichedFilm(title="B2", slug="b2", director="Frequent B", genres=["Mystery"]),
            EnrichedFilm(title="C1", slug="c1", director="Frequent C", genres=["Crime"]),
            EnrichedFilm(title="C2", slug="c2", director="Frequent C", genres=["Crime"]),
            EnrichedFilm(title="D1", slug="d1", director="Frequent D", genres=["Western"]),
            EnrichedFilm(title="D2", slug="d2", director="Frequent D", genres=["Western"]),
            EnrichedFilm(title="E1", slug="e1", director="Frequent E", genres=["Comedy"]),
            EnrichedFilm(title="E2", slug="e2", director="Frequent E", genres=["Comedy"]),
            EnrichedFilm(title="Incidental", slug="incidental", director="One-Off", genres=["Horror"]),
        ]
        favorites = [
            EnrichedFilm(title="Favorite", slug="favorite", director="Favorite Director", genres=["Romance"]),
        ]

        signal = taste_analysis_signal(watched, favorites)
        self.assertEqual(
            {film.slug for film in signal},
            {"favorite", "a1", "a2", "a3", "b1", "b2", "c1", "c2", "d1", "d2", "e1", "e2"},
        )
        # The single incidental watch stays out of the prose's source.
        self.assertNotIn("incidental", {film.slug for film in signal})

        profile = build_taste_profile(watched, favorites)
        # The incidental watch shapes neither the genres nor the prose…
        self.assertNotIn("Horror", profile.top_genres)
        # …but the archive, not the focused signal, still owns the counts.
        self.assertEqual(profile.sample_size, len(watched))

    def test_directors_ranked_by_watch_count_then_average_rating(self):
        watched = (
            [EnrichedFilm(title=f"a{i}", slug=f"a{i}", director="Most Watched", user_rating=3.5) for i in range(9)]
            + [EnrichedFilm(title=f"b{i}", slug=f"b{i}", director="Seven Low", user_rating=3.2) for i in range(7)]
            + [EnrichedFilm(title=f"c{i}", slug=f"c{i}", director="Seven High", user_rating=4.8) for i in range(7)]
        )
        profile = build_taste_profile(watched)
        # 9 beats 7; among the 7s, the higher average wins the tie-break.
        self.assertEqual(
            profile.top_directors[:3], ["Most Watched", "Seven High", "Seven Low"]
        )

    def test_full_history_count_crowns_nolan_over_three_jackson_films(self):
        watched = (
            [
                EnrichedFilm(
                    title=f"Nolan {index}", slug=f"nolan-{index}",
                    director="Christopher Nolan",
                )
                for index in range(11)
            ]
            + [
                EnrichedFilm(
                    title=f"Jackson {index}", slug=f"jackson-{index}",
                    director="Peter Jackson",
                )
                for index in range(3)
            ]
        )

        profile = build_taste_profile(watched)

        self.assertEqual(profile.favorite_director, "Christopher Nolan")
        self.assertEqual(profile.top_directors[:2], ["Christopher Nolan", "Peter Jackson"])
        self.assertEqual(profile.top_directors_detail[0]["count"], 11)

    def test_top_directors_returns_up_to_ten_ranked(self):
        watched = [
            EnrichedFilm(title=f"f{i}", slug=f"f{i}", director=f"D{i:02d}", user_rating=4.0)
            for i in range(14)
        ]
        profile = build_taste_profile(watched)
        self.assertEqual(len(profile.top_directors), 10)
        self.assertEqual(len(profile.top_directors_detail), 10)

    def test_top_directors_detail_lists_the_directors_watched_films(self):
        watched = [
            EnrichedFilm(title="The Shining", slug="the-shining", year=1980,
                         director="Stanley Kubrick", user_rating=5.0,
                         poster_url="https://img/shining.jpg"),
            EnrichedFilm(title="Barry Lyndon", slug="barry-lyndon", year=1975,
                         director="Stanley Kubrick", user_rating=4.5),
            EnrichedFilm(title="Solaris", slug="solaris", year=1972,
                         director="Andrei Tarkovsky", user_rating=4.0),
        ]

        profile = build_taste_profile(watched)
        detail = {d["name"]: d for d in profile.top_directors_detail}

        self.assertEqual(detail["Stanley Kubrick"]["count"], 2)
        self.assertEqual(detail["Stanley Kubrick"]["avg_rating"], 4.75)
        slugs = [f["slug"] for f in detail["Stanley Kubrick"]["films"]]
        self.assertEqual(slugs, ["the-shining", "barry-lyndon"])
        self.assertEqual(
            detail["Stanley Kubrick"]["films"][0]["poster_url"],
            "https://img/shining.jpg",
        )

    def test_personality_from_favorites_names_no_films_or_people(self):
        from app.taste_profile import personality_from_favorites

        self.assertEqual(personality_from_favorites([]), "")
        favs = [
            EnrichedFilm(title="2001", genres=["Science Fiction", "Drama"], director="Stanley Kubrick"),
            EnrichedFilm(title="A Clockwork Orange", genres=["Science Fiction", "Crime"], director="Stanley Kubrick"),
        ]
        text = personality_from_favorites(favs)
        self.assertTrue(text.endswith("."))
        self.assertNotIn("2001", text)
        self.assertNotIn("Stanley Kubrick", text)
        self.assertNotIn("Science Fiction", text)
        # Single shared director → the "one director's world" phrasing.
        self.assertIn("tek bir yönetmenin", text)

    def test_analysis_is_a_multi_line_deterministic_read(self):
        watched = [
            EnrichedFilm(title=f"f{i}", slug=f"f{i}", year=1970 + (i % 3),
                         director="D1" if i % 2 else "D2",
                         genres=["Drama", "Mystery"], keywords=["memory", "grief"],
                         user_rating=4.0)
            for i in range(30)
        ]
        profile = build_taste_profile(watched)
        self.assertGreaterEqual(len(profile.analysis), 2)
        self.assertTrue(all(isinstance(line, str) for line in profile.analysis))

    def test_recency_decay_favours_recently_watched_over_equal_older_cluster(self):
        from app.taste_profile import _recency_weight

        self.assertEqual(_recency_weight(0), 1.0)
        self.assertAlmostEqual(_recency_weight(400), 0.5, places=6)
        self.assertLess(_recency_weight(1200), _recency_weight(400))

        # Equal-sized clusters for "New" and "Old", but "Old" sits ~800 films
        # deeper in the history. Exponential decay must crown "New".
        watched = (
            [EnrichedFilm(title=f"new-{i}", director="New", user_rating=4.0) for i in range(40)]
            + [EnrichedFilm(title=f"mid-{i}", director=f"Mid{i}", user_rating=4.0) for i in range(800)]
            + [EnrichedFilm(title=f"old-{i}", director="Old", user_rating=4.0) for i in range(40)]
        )

        profile = build_taste_profile(watched)

        self.assertEqual(profile.favorite_director, "New")

    def test_source_fingerprint_changes_with_rating(self):
        profile = ScrapedProfile(
            username="film_fan",
            display_name="Film Fan",
            avatar_url="https://example.com/avatar.jpg",
            bio="",
            favorite_films=[ScrapedFilm("Favorite", 2001, "favorite")],
        )
        watched = [
            EnrichedFilm(title="Watched", slug="watched", user_rating=4.0)
        ]
        first = taste_source_fingerprint(profile, watched)
        self.assertEqual(first, taste_source_fingerprint(profile, watched))

        watched[0].user_rating = 4.5
        self.assertNotEqual(first, taste_source_fingerprint(profile, watched))


if __name__ == "__main__":
    unittest.main()
