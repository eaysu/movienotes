"""Layer 3 — watchlist'i zevk profiline göre sırala.

Önceki katalog tabanlı yaklaşımın yerini aldı.
Artık harici bir katalog yok — kullanıcının kendi watchlist'i aday havuzu,
izlediği filmler zevk profilinin kaynağı.

Akış:
  watched_films + watchlist_films  →  TF-IDF fit (birleşik korpus)
  watched vektörlerinin ortalaması  →  zevk profili
  her watchlist filmi × zevk profili  →  cosine benzerlik skoru
  en yüksek skorlu N film  →  LLM katmanına geçer
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .semantic import weighted_profile_scores

if TYPE_CHECKING:
    from .enrich import EnrichedFilm


def _mmr_indices(scores, watchlist_matrix, n: int, relevance_weight: float = 0.72):
    """Select a relevant but non-repetitive shortlist with Maximal Marginal Relevance."""
    if n <= 0 or len(scores) == 0:
        return []

    # Diversity work is bounded even for large watchlists. The best 4×N items
    # remain eligible, so weak candidates cannot enter merely by being different.
    pool_size = min(len(scores), max(n * 4, n))
    pool = [int(i) for i in np.argsort(-scores)[:pool_size]]
    pairwise = cosine_similarity(watchlist_matrix[pool])
    selected_positions: list[int] = []

    while pool and len(selected_positions) < min(n, pool_size):
        if not selected_positions:
            best_position = 0
        else:
            selected_set = set(selected_positions)
            best_position = max(
                (position for position in range(pool_size) if position not in selected_set),
                key=lambda position: (
                    relevance_weight * float(scores[pool[position]])
                    - (1.0 - relevance_weight)
                    * max(float(pairwise[position, chosen]) for chosen in selected_positions),
                    float(scores[pool[position]]),
                    -position,
                ),
            )
        selected_positions.append(best_position)

    return [pool[position] for position in selected_positions]


# Sıralayıcının kendi yazdığı gerekçeler. LLM açıkken üzerine yazılır; kapalıyken
# ya da LLM düşünce kullanıcının okuduğu metin bunlar, dolayısıyla dile uymalı.
_FALLBACK_REASONS: dict[str, dict[str, str]] = {
    "no_history": {
        "tr": "İzleme geçmişi bulunamadı; sıralama yapılamadı.",
        "en": "No viewing history found, so nothing could be ranked.",
    },
    "similar": {
        "tr": "Sevdiğin filmlerin temalarına ve anlatım tarzına yakın olduğu için öne çıktı.",
        "en": "It stands out for sitting close to the themes and storytelling of the films you love.",
    },
}


def _reason(key: str, locale: str) -> str:
    return _FALLBACK_REASONS[key]["en" if locale == "en" else "tr"]


def rank_watchlist(
    watched: list[EnrichedFilm],
    watchlist: list[EnrichedFilm],
    n: int = 8,
    favorite_directors: list[str] | None = None,
    director_boost: float = 0.08,
    favorite_four_slugs: list[str] | set[str] | None = None,
    locale: str = "tr",
) -> list[EnrichedFilm]:
    """Watchlist filmlerini izleme geçmişine benzerliğe göre sırala.

    Args:
        watched:   Kullanıcının daha önce izlediği filmler (zevk profili kaynağı).
        watchlist: İzlemek istediği filmler (aday havuzu).
        n:         Döndürülecek film sayısı.
        locale:    Fallback ``reason`` metninin dili. LLM devredeyse üzerine
                   yazar; devre dışıysa kullanıcının gördüğü metin budur.

    Returns:
        Watchlist'ten seçilmiş, benzerlik skoruna göre sıralanmış n film.
        Her filmin .similarity ve .reason alanları doldurulmuş olur.
    """
    if not watchlist:
        return []

    if not watched:
        # İzleme geçmişi yoksa watchlist'in ilk n filmini döndür
        for f in watchlist[:n]:
            f.similarity = 0.0
            f.reason = _reason("no_history", locale)
        return watchlist[:n]

    # Tüm filmlerin metin bloblarını birleştir (TF-IDF birleşik korpusta fit olsun)
    all_films = watched + watchlist
    blobs = [f.text_blob for f in all_films]

    vec = TfidfVectorizer(max_features=10_000, sublinear_tf=True, min_df=1)
    matrix = vec.fit_transform(blobs)  # sparse (n_films × n_features)

    n_watched = len(watched)
    watched_matrix = matrix[:n_watched]
    watchlist_matrix = matrix[n_watched:]

    # Zevk profili: puansız kayıtlar pozitif implicit sinyal; puanlı kayıtlarda
    # 3–5 yıldız giderek güçlenen pozitif, 0.5–2 yıldız negatif sinyaldir.
    ratings = np.array([
        np.nan if film.user_rating is None else float(film.user_rating)
        for film in watched
    ])
    positive_weights = np.where(
        np.isnan(ratings),
        1.0,
        np.clip((ratings - 2.5) / 2.5, 0.0, 1.0),
    )
    negative_weights = np.where(
        np.isnan(ratings),
        0.0,
        np.clip((2.5 - ratings) / 2.5, 0.0, 1.0),
    )

    # Fav 4 is an explicit preference signal stronger than a passive watch.
    favorite_four_set = {slug for slug in (favorite_four_slugs or []) if slug}
    for index, film in enumerate(watched):
        if film.slug in favorite_four_set:
            positive_weights[index] = max(positive_weights[index], 1.0) * 2.0
            negative_weights[index] = 0.0

    if positive_weights.sum() > 0:
        taste = np.asarray(
            watched_matrix.multiply(positive_weights[:, None]).sum(axis=0)
            / positive_weights.sum()
        )
        scores = cosine_similarity(taste, watchlist_matrix)[0]
    else:
        scores = np.zeros(len(watchlist), dtype=float)

    if negative_weights.sum() > 0:
        negative_taste = np.asarray(
            watched_matrix.multiply(negative_weights[:, None]).sum(axis=0)
            / negative_weights.sum()
        )
        scores -= 0.6 * cosine_similarity(negative_taste, watchlist_matrix)[0]

    # The lexical score keeps explicit genres, directors and keywords legible.
    # A local latent-semantic score adds synopsis/theme proximity without an
    # external embedding API or a new per-film bill.
    semantic_scores, semantic_coverage = weighted_profile_scores(
        watched,
        watchlist,
        positive_weights=positive_weights,
        negative_weights=negative_weights,
    )
    if semantic_scores is not None and semantic_coverage >= 0.20:
        scores = 0.72 * scores + 0.28 * semantic_scores

    # Add a bounded direct affinity bonus. This keeps the recent history as
    # the profile base while allowing Fav 4 to decide close calls.
    favorite_indices = [
        index for index, film in enumerate(watched) if film.slug in favorite_four_set
    ]
    favorite_four_indices = [
        index for index, film in enumerate(watched)
        if film.slug in favorite_four_set
    ]
    if favorite_indices:
        favorite_similarity = cosine_similarity(
            watched_matrix[favorite_indices], watchlist_matrix
        )
        scores += 0.10 * np.asarray(favorite_similarity.max(axis=0)).ravel()
    if favorite_four_indices:
        favorite_four_similarity = cosine_similarity(
            watched_matrix[favorite_four_indices], watchlist_matrix
        )
        scores += 0.18 * np.asarray(favorite_four_similarity.max(axis=0)).ravel()

    # Favorite-director affinity is deliberately a bounded secondary signal.
    # It can break close calls, but cannot dominate a poor content match.
    favorite_rank = {
        name.strip().casefold(): rank
        for rank, name in enumerate((favorite_directors or [])[:3])
        if name and name.strip()
    }
    for index, film in enumerate(watchlist):
        rank = favorite_rank.get((film.director or "").strip().casefold())
        if rank is not None:
            scores[index] += max(0.0, float(director_boost)) * (1.0 - rank * 0.3)

    ranked_idx = _mmr_indices(scores, watchlist_matrix, n)

    results: list[EnrichedFilm] = []
    for idx in ranked_idx:
        film = watchlist[int(idx)]
        film.similarity = round(float(scores[idx]), 4)
        # Kısa fallback reason — LLM varsa zaten üzerine yazar
        film.reason = _reason("similar", locale)
        results.append(film)

    return results
