# ============================================================
# SCORE NORMALIZER
# ============================================================

class ScoreNormalizer:

    def normalize_scores(
        self,
        results,
        score_key,
    ):
        """
        Normalize the scores in the given results dictionary.

        Args:
            results (dict): A dictionary containing the results to be normalized.
            score_key (str): The key in the results dictionary that contains the scores to be normalized.

        Returns:
            dict: The normalized results dictionary.
        """

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