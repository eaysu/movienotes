import asyncio
import unittest

from app.enrich import EnrichedFilm
from app.main import _blend_bridge_films, _calculate_blend


def _profile(prefix: str, *, shared_features: bool, count: int = 100):
    return [
        EnrichedFilm(
            title=f"{prefix} {index}",
            slug=f"{prefix}-{index}",
            year=2020 if shared_features else (1980 if prefix == "first" else 2000),
            genres=["Drama"] if shared_features else [f"Genre {prefix}"],
            keywords=["identity"] if shared_features else [f"keyword {prefix}"],
            director="Shared Director" if shared_features else f"Director {prefix}",
        )
        for index in range(count)
    ]


class BlendCalibrationTests(unittest.TestCase):
    def test_identical_feature_profiles_score_high_with_high_confidence(self):
        first = _profile("first", shared_features=True)
        second = _profile("second", shared_features=True)

        result = _calculate_blend(first, second)

        self.assertGreaterEqual(result["score"], 75)
        self.assertEqual(result["confidence"]["level"], "high")

    def test_opposite_ratings_are_penalized(self):
        first = [
            EnrichedFilm(
                title=f"Film {i}", slug=f"film-{i}", year=2020,
                genres=["Drama"], keywords=["identity"],
                director="Shared Director", user_rating=float(i),
            )
            for i in range(1, 6)
        ]
        second = [
            EnrichedFilm(
                title=f"Film {i}", slug=f"film-{i}", year=2020,
                genres=["Drama"], keywords=["identity"],
                director="Shared Director", user_rating=float(6 - i),
            )
            for i in range(1, 6)
        ]

        result = _calculate_blend(first, second)

        self.assertLessEqual(result["score"], 25)

    def test_hated_and_loved_shared_features_do_not_score_as_a_match(self):
        hated = EnrichedFilm(
            title="Hated", slug="hated", year=2020, genres=["Drama"],
            keywords=["identity"], director="Same", user_rating=0.5,
        )
        loved = EnrichedFilm(
            title="Loved", slug="loved", year=2020, genres=["Drama"],
            keywords=["identity"], director="Same", user_rating=5.0,
        )

        result = _calculate_blend([hated], [loved])

        self.assertEqual(result["score"], 25)

    def test_disjoint_profiles_keep_a_warm_but_clearly_low_floor(self):
        first = _profile("first", shared_features=False)
        second = _profile("second", shared_features=False)

        result = _calculate_blend(first, second)

        self.assertEqual(result["score"], 25)
        self.assertEqual(result["confidence"]["level"], "high")

    def test_shared_fav4_has_a_larger_explicit_bonus(self):
        first = _profile("first", shared_features=False)
        second = _profile("second", shared_features=False)
        baseline = _calculate_blend(first, second)

        result = _calculate_blend(
            first,
            second,
            favorite_four1=["shared-love"],
            favorite_four2=["shared-love"],
        )

        self.assertEqual(result["score"], baseline["score"] + 10)
        self.assertEqual(result["favorite_matches"]["fav4"], ["shared-love"])
        self.assertEqual(result["favorite_matches"]["bonus"], 10)

    def test_small_profiles_report_low_confidence(self):
        first = _profile("first", shared_features=True, count=5)
        second = _profile("second", shared_features=True, count=5)

        result = _calculate_blend(first, second)

        self.assertEqual(result["confidence"]["level"], "low")
        self.assertEqual(result["confidence"]["sample_size"], 5)

    def test_common_films_prioritize_mutual_love_over_poster_availability(self):
        first = [
            EnrichedFilm(title="Mutual Love", slug="mutual-love", user_rating=4.5),
            EnrichedFilm(
                title="One Sided", slug="one-sided", user_rating=5.0,
                poster_url="https://image/one-sided.jpg",
            ),
        ]
        second = [
            EnrichedFilm(title="Mutual Love", slug="mutual-love", user_rating=4.5),
            EnrichedFilm(title="One Sided", slug="one-sided", user_rating=1.0),
        ]

        result = _calculate_blend(first, second, top_n=2)

        self.assertEqual([film.slug for film in result["films"]], ["mutual-love", "one-sided"])
        self.assertEqual(result["film_preferences"]["mutual-love"]["rating2"], 4.5)


def _film(title, *, year=2021, genres=("Drama",), director="Someone"):
    slug = title.lower().replace(" ", "-")
    return EnrichedFilm(
        title=title,
        slug=slug,
        year=year,
        genres=list(genres),
        keywords=["identity"],
        director=director,
    )


class BlendBridgeFilmTests(unittest.TestCase):
    def test_bridge_fills_missing_slot_without_repeating_common_films(self):
        watched1 = [_film("Seen A")]
        watched2 = [_film("Seen B")]
        common = [_film(f"Common {index}") for index in range(1, 5)]
        watchlist1 = common + [_film("Fifth Pick")]
        watchlist2 = common

        bridge = asyncio.run(
            _blend_bridge_films(
                watched1,
                watched2,
                watchlist1,
                watchlist2,
                n=1,
                exclude=common,
            )
        )

        self.assertEqual([film.title for film in bridge], ["Fifth Pick"])

    def test_bridge_pool_from_watchlists_excludes_films_either_user_saw(self):
        watched1 = [_film("Seen A"), _film("Seen B")]
        watched2 = [_film("Seen C")]
        watchlist1 = [_film("Bridge 1"), _film("Seen C"), _film("Bridge 2")]
        watchlist2 = [_film("Bridge 3"), _film("Seen A"), _film("Bridge 4"), _film("Bridge 5")]

        bridge = asyncio.run(
            _blend_bridge_films(watched1, watched2, watchlist1, watchlist2, n=5)
        )

        titles = {f.title for f in bridge}
        self.assertLessEqual(len(bridge), 5)
        self.assertNotIn("Seen A", titles)
        self.assertNotIn("Seen C", titles)
        self.assertTrue(titles.issubset({f"Bridge {i}" for i in range(1, 6)}))

    def test_bridge_dedupes_across_the_two_watchlists(self):
        watched1 = [_film("Seen A")]
        watched2 = [_film("Seen B")]
        shared = _film("Bridge Shared")
        watchlist1 = [shared, _film("Bridge X")]
        watchlist2 = [_film("bridge shared".title()), _film("Bridge Y")]

        bridge = asyncio.run(
            _blend_bridge_films(watched1, watched2, watchlist1, watchlist2, n=5)
        )

        titles = [f.title for f in bridge]
        self.assertEqual(len(titles), len(set(titles)))

    def test_bridge_returns_empty_without_candidates_or_enricher(self):
        bridge = asyncio.run(
            _blend_bridge_films([_film("Seen A")], [_film("Seen B")], [], [], n=5)
        )
        self.assertEqual(bridge, [])

    def test_bridge_widens_with_discover_pool_and_assigns_slugs(self):
        class _FakeEnricher:
            async def discover_pool(self, *, genre_names=None, limit=50):
                # Discover results arrive without a Letterboxd slug.
                return [
                    EnrichedFilm(title="The Prophecy", year=1995, slug="",
                                 genres=["Horror"], poster_url="p"),
                    EnrichedFilm(title="Carriers", year=2009, slug="",
                                 genres=["Horror"], poster_url="p"),
                ]

        watched1 = [_film("Seen A", genres=("Horror",))]
        watched2 = [_film("Seen B", genres=("Horror",))]
        # One thin watchlist film each → pool below n, discover widens it.
        bridge = asyncio.run(
            _blend_bridge_films(
                watched1, watched2,
                [_film("Only WL", genres=("Horror",))], [],
                enricher=_FakeEnricher(), n=5,
            )
        )
        by_title = {f.title: f for f in bridge}
        self.assertIn("The Prophecy", by_title)
        self.assertEqual(by_title["The Prophecy"].slug, "the-prophecy")
        self.assertTrue(all(f.slug for f in bridge))


if __name__ == "__main__":
    unittest.main()
