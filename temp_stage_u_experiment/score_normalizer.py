# ============================================================
# SCORE NORMALIZER
# ============================================================

class ScoreNormalizer:

    def normalize_scores(
        self,
        results,
        score_key,
    ):
        # AI-DOCS: Add comprehensive docstring for normalize_scores explaining min-max score normalization, empty result handling, and dictionary score key updates

        if not results:
            return results

        scores = [
            result.get(
                score_key,
                0,
            )
            for result in results
        ]

        max_score = max(scores)

        min_score = min(scores)

        if max_score == min_score:

            for result in results:

                result[
                    f"{score_key}_normalized"
                ] = 1.0

            return results

        for result in results:

            value = result.get(
                score_key,
                0,
            )

            normalized = (
                value - min_score
            ) / (
                max_score - min_score
            )

            result[
                f"{score_key}_normalized"
            ] = normalized

        return results