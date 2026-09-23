"""Deterministic, persistable taste-profile snapshots.

The snapshot is available immediately after enrichment and does not require an
LLM call. A later LLM pass may improve the prose without changing the measured
favorite director, feature coverage or confidence fields.
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from .enrich import EnrichedFilm

TASTE_PROFILE_VERSION = "taste-v6-bilingual-narratives"

# Recency half-life in "films watched ago". With the full watched history now
# feeding the profile, a flat linear taper is meaningless across thousands of
# entries — an exponential decay keeps the profile anchored on what the user
# has been watching lately while still letting the back catalogue contribute.
RECENCY_HALFLIFE_FILMS = 400.0


@dataclass
class TasteProfileSnapshot:
    summary: str
    favorite_director: str = ""
    top_directors: list[str] = field(default_factory=list)
    top_directors_detail: list[dict] = field(default_factory=list)
    top_genres: list[str] = field(default_factory=list)
    top_keywords: list[str] = field(default_factory=list)
    analysis: list[str] = field(default_factory=list)
    # "llm" once a model has written the prose, "local" while it is the
    # deterministic fallback. Without it a silent LLM failure is invisible.
    analysis_source: str = "local"
    personality: str = ""
    # Editorial prose is held per language. Numeric taste signals remain
    # language-neutral, while the UI can select a complete narrative without
    # translating Turkish text in the browser.
    localized_narratives: dict = field(default_factory=dict)
    sample_size: int = 0
    rated_count: int = 0
    metadata_coverage: int = 0
    confidence_level: str = "low"
    confidence_score: int = 0
    algorithm_version: str = TASTE_PROFILE_VERSION
    source_fingerprint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _recency_weight(index: int) -> float:
    """Exponential decay by position in the recency-ordered watch list."""
    return 0.5 ** (index / RECENCY_HALFLIFE_FILMS)


def _positive_weight(film: EnrichedFilm, index: int, total: int) -> float:
    """Weight explicit likes strongly and unrated watches as a weaker signal."""
    recency = _recency_weight(index)
    if film.user_rating is None:
        return 0.35 * recency
    rating = float(film.user_rating)
    if rating < 3.0:
        return 0.0
    return max(0.1, (rating - 2.5) / 2.5) * recency


def _top_weighted(values: dict[str, float], limit: int) -> list[str]:
    return [
        value
        for value, _score in sorted(
            values.items(), key=lambda item: (-item[1], item[0].lower())
        )[:limit]
    ]


def _director_detail(
    watched: list[EnrichedFilm], names: list[str], *, per_director: int = 6
) -> list[dict]:
    """Compact preview for each top director.

    Films are ordered by the user's own rating first (their vote is what
    matters), then by recency; `watched` is already recency-ordered.
    """
    detail: list[dict] = []
    for name in names:
        films = [
            {
                "title": film.title,
                "slug": film.slug,
                "year": film.year,
                "poster_url": film.poster_url or "",
                "user_rating": film.user_rating,
            }
            for film in watched
            if film.director == name
        ]
        if not films:
            continue
        # Stable sort: rated films first (highest vote first), then unrated in
        # recency order.
        films.sort(
            key=lambda f: -(f["user_rating"] if f["user_rating"] is not None else -1.0)
        )
        ratings = [f["user_rating"] for f in films if f["user_rating"] is not None]
        detail.append(
            {
                "name": name,
                "photo_url": "",  # filled by the caller via TMDb person search
                "count": len(films),
                "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "films": films[:per_director],
                "has_more": len(films) > per_director,
            }
        )
    return detail


# Dominant-genre → a human disposition it tends to signal (no genre names surfaced).
_GENRE_TRAIT = {
    "Drama": "insan ilişkilerinin ağırlığını ve duygusal dürüstlüğü",
    "Science Fiction": "spekülatif fikirleri ve 'ya olsaydı' sorusunu",
    "Comedy": "zekâ kıvraklığını ve tonun hafifliğini",
    "Thriller": "gerilimi, tempoyu ve tetikte kalmayı",
    "Crime": "ahlaki griliği ve suçun ardındaki psikolojiyi",
    "Horror": "rahatsız edici olanla yüzleşmeyi",
    "Romance": "duygusal içtenliği ve yakınlığı",
    "Animation": "biçimsel yaratıcılığı ve el işçiliğini",
    "Documentary": "gerçeğin kendisini, kurgusuz olanı",
    "Mystery": "çözülecek bir bilmeceyi",
    "Adventure": "geniş ölçekli, yol alan anlatıları",
    "Fantasy": "kurulmuş dünyaların iç mantığını",
    "War": "çatışmanın bireysel bedelini",
    "History": "geçmişin dikkatle yeniden kurulmasını",
    "Action": "kinetik enerjiyi ve fiziksel jestleri",
    "Family": "kuşaklar arası bağı",
    "Music": "ritmi ve sahne enerjisini",
    "Western": "mekânın ve mitin ağırlığını",
}


def personality_from_favorites(favorites) -> str:
    """Deterministic Fav-4 read — the LLM overrides this. Names no films or people."""
    picks = [f for f in (favorites or [])[:4] if getattr(f, "title", "")]
    if len(picks) < 2:
        return ""
    counts: dict[str, int] = defaultdict(int)
    directors: list[str] = []
    for film in picks:
        for genre in getattr(film, "genres", None) or []:
            counts[genre] += 1
        if getattr(film, "director", ""):
            directors.append(film.director)
    traits = [
        _GENRE_TRAIT[g]
        for g, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        if g in _GENRE_TRAIT
    ][:2]
    if traits:
        sentence = (
            "Favori seçkin, bir filmde " + " ve ".join(traits) + " önemseyen birini gösteriyor"
        )
    else:
        sentence = "Favori seçkin belirgin bir ortak damar taşıyor"
    unique = set(directors)
    if len(unique) == 1:
        sentence += "; ilgin dağınık değil, tek bir yönetmenin dünyasına derinlemesine dönük"
    elif len(unique) >= 3:
        sentence += "; bağlılığın bir isme değil, güçlü yönetmen seslerinin geneline"
    return sentence.strip() + "."


def _deterministic_analysis(
    watched: list[EnrichedFilm],
    *,
    top_genres: list[str],
    top_directors: list[str],
    top_keywords: list[str],
    director_scores: dict[str, float],
) -> list[str]:
    """A multi-part deterministic read used until the LLM prose lands."""
    lines: list[str] = []

    decades: dict[int, float] = defaultdict(float)
    for index, film in enumerate(watched):
        if film.year:
            decades[(int(film.year) // 10) * 10] += _recency_weight(index)
    if decades:
        ranked = sorted(decades.items(), key=lambda item: -item[1])[:2]
        labels = [f"{decade}'ler" for decade, _ in ranked]
        lines.append(
            f"En sık geri döndüğün dönem {' ve '.join(labels)}; "
            "son izlediklerin de bu yıllara doğru kayıyor."
        )

    rated = [float(f.user_rating) for f in watched if f.user_rating is not None]
    if len(rated) >= 8:
        avg = sum(rated) / len(rated)
        weak = sum(1 for r in rated if r <= 2.5) / len(rated)
        if avg >= 3.9:
            tone = "cömert bir puanlayıcısın"
        elif avg <= 3.1:
            tone = "sert bir puanlayıcısın"
        else:
            tone = "dengeli puan veriyorsun"
        tail = (
            f"; izlediklerinin %{round(weak * 100)}'ini zayıf buluyorsun."
            if weak >= 0.15
            else "."
        )
        lines.append(f"Puan ortalaman {avg:.1f}/5 — {tone}{tail}")

    distinct = {g for f in watched for g in (f.genres or [])}
    if len(top_genres) >= 2:
        if len(distinct) >= 12:
            spread = "Tür yelpazen geniş"
        elif len(distinct) <= 6:
            spread = "Birkaç türe sadıksın"
        else:
            spread = "Tür tercihlerin odaklı"
        lines.append(f"{spread}; en baskın damarlar {', '.join(top_genres[:3])}.")

    if director_scores and top_directors:
        total = sum(director_scores.values())
        share = round(
            sum(director_scores.get(d, 0.0) for d in top_directors[:3]) / total * 100
        ) if total else 0
        if share >= 20:
            lines.append(
                f"Yönetmen bağlılığın belirgin: sinyalin ~%{share}'i "
                f"{top_directors[0]} başta olmak üzere üç isimden geliyor."
            )
        elif share and share <= 8:
            lines.append(
                "Tek bir yönetmene bağlanmadan geniş bir auteur yelpazesi izliyorsun."
            )

    if len(top_keywords) >= 3:
        lines.append(f"Tekrar eden temalar: {', '.join(top_keywords[:4])}.")

    return lines[:4]


def _build_taste_profile(watched: list[EnrichedFilm]) -> TasteProfileSnapshot:
    """Build a stable taste summary and rating-aware favorite director."""
    if not watched:
        return TasteProfileSnapshot(
            summary="Zevk analizi için henüz yeterli izleme verisi bulunmuyor."
        )

    director_scores: dict[str, float] = defaultdict(float)
    genre_scores: dict[str, float] = defaultdict(float)
    keyword_scores: dict[str, float] = defaultdict(float)
    metadata_points = 0.0
    rated_count = 0

    for index, film in enumerate(watched):
        weight = _positive_weight(film, index, len(watched))
        if film.user_rating is not None:
            rated_count += 1
        if film.director:
            metadata_points += 0.40
            if weight > 0:
                director_scores[film.director] += weight
        if film.genres:
            metadata_points += 0.35
            for genre in set(film.genres):
                if weight > 0:
                    genre_scores[genre] += weight
        if film.keywords:
            metadata_points += 0.25
            for keyword in set(film.keywords):
                if weight > 0:
                    keyword_scores[keyword] += weight

    # A profile with only low ratings still needs a neutral frequency fallback;
    # disliked films are never promoted when any positive signal exists.
    if not director_scores:
        for index, film in enumerate(watched):
            if film.director:
                director_scores[film.director] += _recency_weight(index)

    # Directors are ranked by how many of their films the user has watched
    # (not counting the ones they actively disliked); ties break on how many
    # total films of theirs the user saw, then on the user's average rating.
    director_positive: dict[str, int] = defaultdict(int)
    director_counts: dict[str, int] = defaultdict(int)
    director_rating_sum: dict[str, float] = defaultdict(float)
    director_rating_n: dict[str, int] = defaultdict(int)
    for film in watched:
        if not film.director:
            continue
        director_counts[film.director] += 1
        if film.user_rating is None or float(film.user_rating) >= 3.0:
            director_positive[film.director] += 1
        if film.user_rating is not None:
            director_rating_sum[film.director] += float(film.user_rating)
            director_rating_n[film.director] += 1

    def _dir_avg(name: str) -> float:
        n = director_rating_n.get(name, 0)
        return director_rating_sum[name] / n if n else 0.0

    # A director seen only once is noise (often a mis-credit on the sparse
    # provisional sample); require at least two watched films to be a "favorite".
    eligible = [
        d
        for d in director_counts
        if director_positive[d] > 0 and director_counts[d] >= 2
    ]
    if not eligible:  # tiny/new profile — fall back to any positively-seen director
        eligible = [d for d in director_counts if director_positive[d] > 0]
    top_directors = sorted(
        eligible,
        key=lambda d: (
            -director_positive[d],
            -director_counts[d],
            -_dir_avg(d),
            d.lower(),
        ),
    )[:10]
    top_genres = _top_weighted(genre_scores, 3)
    top_keywords = _top_weighted(keyword_scores, 5)
    metadata_coverage = round(metadata_points / len(watched) * 100)
    rating_coverage = rated_count / len(watched)
    confidence = (
        min(len(watched) / 50.0, 1.0) * 0.50
        + (metadata_coverage / 100.0) * 0.35
        + rating_coverage * 0.15
    )
    confidence_score = round(min(confidence, 1.0) * 100)
    confidence_level = (
        "high" if confidence_score >= 75 else "medium" if confidence_score >= 45 else "low"
    )

    genre_phrase = ", ".join(top_genres[:2])
    director = top_directors[0] if top_directors else ""
    if genre_phrase and director:
        summary = (
            f"{genre_phrase} ağırlıklı; {director} filmlerine belirgin yakınlık "
            "gösteren bir zevk profili."
        )
    elif genre_phrase:
        summary = f"{genre_phrase} ağırlıklı bir izleme zevki öne çıkıyor."
    elif director:
        summary = f"{director} filmlerine yakınlık gösteren bir zevk profili."
    else:
        summary = (
            "İzleme geçmişin kaydedildi; film bilgileri tamamlandıkça zevk "
            "analizin daha ayrıntılı hâle gelecek."
        )

    return TasteProfileSnapshot(
        summary=summary,
        favorite_director=director,
        top_directors=top_directors,
        top_directors_detail=_director_detail(watched, top_directors),
        top_genres=top_genres,
        top_keywords=top_keywords,
        analysis=_deterministic_analysis(
            watched,
            top_genres=top_genres,
            top_directors=top_directors,
            top_keywords=top_keywords,
            director_scores=director_scores,
        ),
        sample_size=len(watched),
        rated_count=rated_count,
        metadata_coverage=metadata_coverage,
        confidence_level=confidence_level,
        confidence_score=confidence_score,
    )


TASTE_SIGNAL_DIRECTORS = 5


def taste_analysis_signal(
    watched: list[EnrichedFilm], favorites: list[EnrichedFilm] | None = None
) -> list[EnrichedFilm]:
    """Use explicit Fav 4 plus films by the most-watched directors for prose.

    The full archive remains the source for counts and the director ranking.
    Editorial taste text, however, should be anchored in the films a member
    explicitly chose and the filmmakers they return to most often—not diluted
    by every incidental diary entry.
    """
    favorites = [film for film in (favorites or [])[:4] if getattr(film, "title", "")]
    if not watched:
        return favorites

    counts: dict[str, int] = defaultdict(int)
    for film in watched:
        if film.director:
            counts[film.director] += 1
    directors = {
        name
        for name, _count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0].casefold())
        )[:TASTE_SIGNAL_DIRECTORS]
    }
    selected: list[EnrichedFilm] = []
    seen: set[str] = set()
    for film in [*favorites, *watched]:
        key = film.slug or f"{film.title.casefold()}:{film.year or ''}"
        if key in seen:
            continue
        if film in favorites or film.director in directors:
            selected.append(film)
            seen.add(key)
    return selected or watched


def build_taste_profile(
    watched: list[EnrichedFilm], favorites: list[EnrichedFilm] | None = None
) -> TasteProfileSnapshot:
    """Build durable archive metrics and a Fav 4/director-led taste reading."""
    profile = _build_taste_profile(watched)
    if not favorites:
        return profile

    # Keep the full archive's sample size, rating coverage and top-director
    # deck. Only the user-facing genre/theme prose is intentionally focused.
    signal = taste_analysis_signal(watched, favorites)
    focused = _build_taste_profile(signal)
    profile.summary = focused.summary
    profile.top_genres = focused.top_genres
    profile.top_keywords = focused.top_keywords
    profile.analysis = focused.analysis
    return profile


def taste_source_fingerprint(profile, watched: list[EnrichedFilm]) -> str:
    """Hash only public inputs whose change should invalidate a taste snapshot."""
    payload = {
        "version": "taste-source-v1",
        "display_name": profile.display_name,
        "avatar_url": profile.avatar_url or "",
        "favorites": [film.slug for film in profile.favorite_films[:4]],
        "watched": [(film.slug, film.user_rating) for film in watched],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
