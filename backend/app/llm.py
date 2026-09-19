"""Layer 4 — LLM ile watchlist sıralaması.

Benzerlik katmanı en iyi N watchlist filmini seçti.
GPT bu filmleri kullanıcının izleme geçmişine göre yeniden sıralar,
her biri için kişiselleştirilmiş gerekçe ve genel zevk analizi üretir.

OpenAI key yoksa benzerlik sıralaması fallback olarak döner.
"""

import json

from .config import Settings
from .enrich import EnrichedFilm

# Kısa alias → tam model ID eşlemesi
_MODEL_ALIASES: dict[str, str] = {
    "gpt-5-mini": "gpt-5-mini-2025-08-07",
}


def _resolve_model(name: str) -> str:
    return _MODEL_ALIASES.get(name, name)


def _film_label(f: EnrichedFilm) -> str:
    label = f.title
    if f.year:
        label += f" ({f.year})"
    if f.director:
        label += f", yön. {f.director}"
    if f.user_rating is not None:
        label += f", kullanıcı puanı {float(f.user_rating):g}/5"
    return label


def _taste_references(watched: list[EnrichedFilm], limit: int = 40) -> tuple[list[EnrichedFilm], str]:
    """Prefer explicit positive ratings; never describe low-rated films as taste."""
    liked = [
        (index, film)
        for index, film in enumerate(watched)
        if film.user_rating is not None and float(film.user_rating) >= 3.5
    ]
    if liked:
        liked.sort(key=lambda item: (-float(item[1].user_rating), item[0]))
        return [film for _, film in liked[:limit]], "rated_likes"

    if any(film.user_rating is not None for film in watched):
        return [], "no_positive_ratings"

    # Profiles without rating coverage still need a bounded implicit signal. The
    # prompt names this honestly as viewing history, not as a list of favourites.
    return watched[: min(limit, 20)], "unrated_history"


def _build_prompt(
    watched: list[EnrichedFilm],
    candidates: list[EnrichedFilm],
    n: int,
    favorite_four_slugs: list[str] | None = None,
    locale: str = "tr",
) -> str:
    references, reference_mode = _taste_references(watched)
    watched_block = "; ".join(_film_label(f) for f in references)
    if reference_mode == "rated_likes":
        reference_heading = "Kullanıcının yüksek puan verdiği filmler"
    elif reference_mode == "unrated_history":
        reference_heading = "Puan verisi olmadığı için yakın dönem izleme geçmişi"
    else:
        reference_heading = "Yeterli pozitif puan sinyali bulunamadı"
        watched_block = "(pozitif referans yok)"

    lines = []
    for i, c in enumerate(candidates, start=1):
        overview = (c.overview or "")[:240]
        genres = ", ".join(c.genres or [])
        lines.append(
            f"[{i}] {c.title} ({c.year or '?'}) "
            f"— yön. {c.director or 'bilinmiyor'} "
            f"— türler: {genres or 'n/a'}\n    {overview}"
        )
    candidate_block = "\n".join(lines)
    favorite_four_set = set(favorite_four_slugs or [])
    fav_four_block = "; ".join(
        _film_label(film) for film in watched if film.slug in favorite_four_set
    ) or "(seçim yapılmamış)"

    prompt = (
        "Sen deneyimli bir film öneri uzmanısın.\n\n"
        f"{reference_heading}:\n{watched_block}\n\n"
        f"Letterboxd Favori 4 (en güçlü tercih sinyali):\n{fav_four_block}\n\n"
        "Aşağıdaki filmler kullanıcının watchlist'inden seçilmiş adaylardır "
        "(izleme geçmişine benzerliğe göre ön filtrelendi):\n"
        f"{candidate_block}\n\n"
        f"Bu kullanıcıya en uygun {n} filmi seç ve sırala. "
        "Her film için 'reason' alanına, kullanıcının bu öneriyi benimsemesini "
        "sağlayacak 2-3 cümlelik sıcak bir paragraf yaz (Türkçe): 'sen' diliyle "
        "konuş, izleme geçmişindeki SOMUT filmlere / yönetmenlere / temalara "
        "atıfta bulun ve bu filmin ona neden dokunacağını anlat. "
        "Ayrıca 'taste_summary' alanına 2-3 cümlelik genel zevk analizi yaz "
        "(Türkçe) — kaç film önerdiğini YAZMA, sayı verme. "
        "Önemli: film isimleri, yönetmen adları ve diğer özel isimler orijinal dilinde kalmalı, çevrilmemeli.\n\n"
        "SADECE aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:\n"
        '{"taste_summary": "...", '
        '"picks": [{"index": <yukarıdaki liste numarası>, "reason": "..."}]}'
    )
    if locale == "en":
        prompt += (
            "\n\nOUTPUT LANGUAGE OVERRIDE: Return every value in the JSON, including "
            "taste_summary and reason, in natural English. Keep film titles and "
            "proper names in their original form."
        )
    return prompt


def _fallback(candidates: list[EnrichedFilm], n: int, *, locale: str = "tr") -> dict:
    """LLM yokken benzerlik sıralamasını döndür."""
    picks = []
    for c in candidates[:n]:
        picks.append({
            **c.to_dict(),
            "reason": c.reason or (
                "It stands out because it is close to the themes and storytelling styles in the films you love."
                if locale == "en" else
                "Sevdiğin filmlerdeki temalara ve anlatım tarzına yakın olduğu için öne çıktı."
            ),
        })
    return {
        "taste_summary": (
            "I ranked these picks by their proximity to the themes, directors, and recent films you enjoy."
            if locale == "en" else
            "Seçimleri sevdiğin temalara, yönetmenlere ve son izlediklerine yakınlıklarına göre sıraladım."
        ),
        "recommendations": picks,
        "llm_used": False,
    }


async def rank_candidates(
    settings: Settings,
    watched: list[EnrichedFilm],
    candidates: list[EnrichedFilm],
    *,
    favorite_four_slugs: list[str] | None = None,
    locale: str = "tr",
) -> dict:
    """Aday watchlist filmlerini LLM ile sırala ve gerekçelendir."""
    n = settings.num_recommendations
    if not candidates:
        return {"taste_summary": "", "recommendations": [], "llm_used": False}

    if not settings.has_openai:
        return _fallback(candidates, n, locale=locale)

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        model_id = _resolve_model(settings.openai_model)
        # Reasoning modelleri (gpt-5-mini, o-serisi):
        #   max_completion_tokens = reasoning_tokens + output_tokens
        #   Karmaşık prompt için reasoning ~4000+ token tüketebilir;
        #   output JSON ~1500 token → toplam 8000 yeterince güvenli.
        # Eski modeller (gpt-4o-mini vb.) max_tokens kullanır.
        _REASONING_MODELS = {"gpt-5-mini-2025-08-07", "o3-mini", "o4-mini", "o1-mini", "o1", "o3"}
        if model_id in _REASONING_MODELS:
            token_kwargs = {"max_completion_tokens": 8000}
        else:
            token_kwargs = {"max_tokens": 1500}

        response = await client.chat.completions.create(
            model=model_id,
            **token_kwargs,
            messages=[{
                "role": "user",
                "content": _build_prompt(
                    watched,
                    candidates,
                    n,
                    favorite_four_slugs=favorite_four_slugs,
                    locale=locale,
                ),
            }],
        )
        raw = response.choices[0].message.content or ""
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        parsed = json.loads(raw.strip())

        recommendations = []
        for pick in parsed.get("picks", []):
            idx = int(pick.get("index", 0)) - 1
            if 0 <= idx < len(candidates):
                c = candidates[idx]
                c.reason = pick.get("reason", "")
                recommendations.append(c.to_dict())

        return {
            "taste_summary": parsed.get("taste_summary", ""),
            "recommendations": recommendations or _fallback(candidates, n, locale=locale)["recommendations"],
            "llm_used": True,
        }
    except Exception as exc:  # noqa: BLE001
        result = _fallback(candidates, n, locale=locale)
        result["taste_summary"] = (
            "This time I ranked the picks directly by their proximity to the themes and directors you enjoy."
            if locale == "en" else
            "Bu kez seçimleri doğrudan sevdiğin temalara ve yönetmenlere yakınlıklarına göre sıraladım."
        )
        return result


_ANALYSIS_REASONING_MODELS = {
    "gpt-5-mini-2025-08-07", "gpt-5", "gpt-5.6-terra",
    "o3-mini", "o4-mini", "o1-mini", "o1", "o3",
}


def _genre_histogram(films: list[EnrichedFilm], top: int = 8) -> str:
    counts: dict[str, int] = {}
    for film in films:
        for genre in film.genres or []:
            counts[genre] = counts.get(genre, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return ", ".join(f"{g} ({c})" for g, c in ranked) or "belirsiz"


def _decade_histogram(films: list[EnrichedFilm]) -> str:
    counts: dict[int, int] = {}
    for film in films:
        if film.year:
            decade = (int(film.year) // 10) * 10
            counts[decade] = counts.get(decade, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:4]
    return ", ".join(f"{d}'ler ({c})" for d, c in ranked) or "belirsiz"


def _taste_analysis_prompt(
    watched: list[EnrichedFilm], favorites: list[EnrichedFilm], locale: str = "tr"
) -> str:
    liked, _mode = _taste_references(watched, limit=45)
    sample = liked or watched[:30]
    watched_lines = "\n".join(f"- {_film_label(f)}" for f in sample[:45])
    fav_lines = "\n".join(
        f"- {_film_label(f)}" for f in (favorites or [])[:4] if f.title
    ) or "- (belirtilmemiş)"
    rated = [float(f.user_rating) for f in watched if f.user_rating is not None]
    rating_note = (
        f"ortalama puan {sum(rated)/len(rated):.1f}/5, {len(rated)} puanlı film"
        if rated
        else "puan verisi az"
    )
    prompt = (
        "Bir sinefilin izleme verisinden analiz üret. Türkçe, akıcı, doğal bir dille "
        "yaz — madde işareti gibi kesik cümleler değil, birbirine bağlanan cümleler. "
        "KLİŞE YASAK: 'sinema tutkunu', 'geniş bir yelpaze', 'her türden hoşlanıyor', "
        "'gerçek bir sinefil' gibi ifadeler kullanma. Film ADI SAYMA, tür ADI "
        "LİSTELEME; bunun yerine örüntüyü yorumla.\n\n"
        f"Tür dağılımı (film sayısıyla): {_genre_histogram(watched)}\n"
        f"Dönem dağılımı: {_decade_histogram(watched)}\n"
        f"Puanlama: {rating_note}\n\n"
        f"Sevdiği filmlerden örnek:\n{watched_lines}\n\n"
        f"Letterboxd Favori 4:\n{fav_lines}\n\n"
        "SADECE şu JSON'u döndür:\n"
        '{\n'
        '  "analysis": ["Sevdiği türlerin BİLEŞİMİNDEN yola çıkan 3-4 cümlelik, tek '
        'paragraf gibi okunan bir zevk analizi. Hangi tonları aradığı, türler '
        'arası gerilimi, dönem eğilimi ve puanlama karakteri tek tek DEĞİL, '
        'birbirine dokunan gözlemler olarak. Her dizi elemanı bir cümle."],\n'
        '  "personality": "Favori 4 filmden çıkarımla, KİŞİNİN mizacı/dünya görüşü '
        'üzerine 2-3 cümle. Filmleri ya da yönetmenleri tekrar İSİMLENDİRME; onların '
        'ortak ne söylediğini insana dair bir okumaya çevir."\n'
        '}'
    )
    if locale == "en":
        prompt += "\n\nOUTPUT LANGUAGE OVERRIDE: Write every JSON value in natural English."
    return prompt


async def analyze_taste(
    settings: Settings,
    watched: list[EnrichedFilm],
    favorites: list[EnrichedFilm] | None = None,
    *,
    locale: str = "tr",
) -> dict:
    """LLM ile ayrıntılı zevk analizi + Fav 4 kişilik okuması.

    LLM yoksa veya hata olursa {} döner; çağıran deterministik metni korur.
    """
    if not settings.has_openai or not watched:
        return {}
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        model_id = _resolve_model(
            (settings.openai_analysis_model or settings.openai_model).strip()
        )
        if model_id in _ANALYSIS_REASONING_MODELS:
            token_kwargs = {"max_completion_tokens": 4000}
        else:
            token_kwargs = {"max_tokens": 900}
        response = await client.chat.completions.create(
            model=model_id,
            **token_kwargs,
            messages=[
                {
                    "role": "user",
                    "content": _taste_analysis_prompt(watched, favorites or [], locale),
                }
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
        analysis = [
            line.strip()
            for line in parsed.get("analysis", [])
            if isinstance(line, str) and line.strip()
        ][:5]
        personality = str(parsed.get("personality", "")).strip()
        return {"analysis": analysis, "personality": personality}
    except Exception:  # noqa: BLE001 — deterministic fallback stays in place
        return {}
