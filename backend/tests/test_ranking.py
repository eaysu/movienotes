import inspect
import unittest

from app.enrich import EnrichedFilm
from app.recommender import rank_watchlist


class FallbackReasonLocaleTests(unittest.TestCase):
    """Reported: "zevkime göre öner" failed with an unexpected error.

    The pipeline asked for a locale so the self-written reasons would follow
    the member's language, but the ranker never grew the argument, so every
    uncached run raised TypeError at the ranking stage. The SSE handler turned
    that into "Beklenmeyen bir hata oluştu." and no recommendation ever landed.
    The integration tests missed it because they patched the ranker out.
    """

    def test_ranker_accepts_every_argument_the_pipeline_passes(self):
        signature = inspect.signature(rank_watchlist)
        signature.bind(
            [],
            [],
            n=8,
            favorite_directors=[],
            director_boost=0.08,
            favorite_four_slugs=[],
            locale="tr",
        )

    def test_self_written_reasons_follow_the_requested_language(self):
        # The ranker writes onto the films it is handed, so each run needs its
        # own objects or the second call rewrites the first one's reason.
        def watchlist():
            return [EnrichedFilm(title="Space One", slug="space-one", genres=["Science Fiction"], keywords=["space"])]

        watched = [EnrichedFilm(title="Space Love", genres=["Science Fiction"], keywords=["space"])]

        turkish = rank_watchlist(watched, watchlist(), n=1, locale="tr")
        english = rank_watchlist(watched, watchlist(), n=1, locale="en")

        self.assertIn("Sevdiğin filmlerin", turkish[0].reason)
        self.assertIn("films you love", english[0].reason)

    def test_the_empty_history_reason_is_localised_too(self):
        english = rank_watchlist([], [EnrichedFilm(title="Space One", slug="space-one")], n=1, locale="en")

        self.assertIn("No viewing history", english[0].reason)


class RatingAwareRankingTests(unittest.TestCase):
    def test_low_rating_is_negative_signal(self):
        watched = [
            EnrichedFilm(
                title="Loved Space Film",
                slug="loved-space",
                genres=["Science Fiction"],
                keywords=["space", "astronaut"],
                user_rating=5.0,
            ),
            EnrichedFilm(
                title="Disliked Slasher",
                slug="disliked-slasher",
                genres=["Horror"],
                keywords=["slasher", "gore"],
                user_rating=1.0,
            ),
        ]
        watchlist = [
            EnrichedFilm(
                title="Another Slasher",
                slug="another-slasher",
                genres=["Horror"],
                keywords=["slasher", "gore"],
            ),
            EnrichedFilm(
                title="Another Space Journey",
                slug="space-journey",
                genres=["Science Fiction"],
                keywords=["space", "astronaut"],
            ),
        ]

        ranked = rank_watchlist(watched, watchlist, n=2)

        self.assertEqual(ranked[0].slug, "space-journey")
        self.assertGreater(ranked[0].similarity, ranked[1].similarity)

    def test_mmr_avoids_near_duplicate_shortlist(self):
        watched = [
            EnrichedFilm(title="Space Love", genres=["Science Fiction"], keywords=["space"]),
            EnrichedFilm(title="Courtroom Love", genres=["Drama"], keywords=["courtroom"]),
        ]
        watchlist = [
            EnrichedFilm(title="Space One", slug="space-one", genres=["Science Fiction"], keywords=["space"]),
            EnrichedFilm(title="Space Two", slug="space-two", genres=["Science Fiction"], keywords=["space"]),
            EnrichedFilm(title="Courtroom", slug="courtroom", genres=["Drama"], keywords=["courtroom"]),
        ]

        ranked = rank_watchlist(watched, watchlist, n=2)

        self.assertEqual(len(ranked), 2)
        self.assertIn("courtroom", {film.slug for film in ranked})

    def test_favorite_director_is_a_bounded_tiebreaker(self):
        watched = [
            EnrichedFilm(title="Quiet Drama", genres=["Drama"], keywords=["family"])
        ]
        watchlist = [
            EnrichedFilm(
                title="Close Match",
                slug="close",
                genres=["Drama"],
                keywords=["family"],
                director="Other Director",
            ),
            EnrichedFilm(
                title="Auteur Match",
                slug="auteur",
                genres=["Drama"],
                keywords=["family"],
                director="Favorite Director",
            ),
        ]

        ranked = rank_watchlist(
            watched,
            watchlist,
            n=2,
            favorite_directors=["Favorite Director"],
            director_boost=0.08,
        )

        self.assertEqual(ranked[0].slug, "auteur")
        self.assertLess(ranked[0].similarity - ranked[1].similarity, 0.1)

    def test_explicit_fav4_outweighs_an_equally_common_passive_taste(self):
        watched = [
            EnrichedFilm(
                title="Favorite Space Film", slug="favorite-space",
                genres=["Science Fiction"], keywords=["space", "astronaut"],
            ),
            EnrichedFilm(
                title="Family Drama", slug="family-drama",
                genres=["Drama"], keywords=["family", "home"],
            ),
        ]
        watchlist = [
            EnrichedFilm(
                title="New Family Drama", slug="new-family",
                genres=["Drama"], keywords=["family", "home"],
            ),
            EnrichedFilm(
                title="New Space Film", slug="new-space",
                genres=["Science Fiction"], keywords=["space", "astronaut"],
            ),
        ]

        ranked = rank_watchlist(
            watched,
            watchlist,
            n=2,
            favorite_four_slugs=["favorite-space"],
        )

        self.assertEqual(ranked[0].slug, "new-space")
        self.assertGreater(ranked[0].similarity, ranked[1].similarity)


if __name__ == "__main__":
    unittest.main()
