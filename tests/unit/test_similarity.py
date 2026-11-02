from ambre.domain.similarity import cluster_texts, content_tokens, jaccard


def test_tokens_drop_stop_words_and_single_characters_and_ignore_case() -> None:
    assert content_tokens("Will the SpaceX Starship reach orbit in 2027?") == frozenset(
        {"spacex", "starship", "reach", "orbit", "2027"}
    )
    assert content_tokens("") == frozenset()


def test_jaccard_is_symmetric_and_zero_when_a_side_is_empty() -> None:
    a, b = frozenset({"x", "y", "z"}), frozenset({"y", "z", "w"})
    assert jaccard(a, b) == jaccard(b, a) == 0.5
    assert jaccard(a, frozenset()) == 0.0 and jaccard(frozenset(), frozenset()) == 0.0


def test_near_identical_questions_share_a_cluster_and_others_do_not() -> None:
    texts = [
        "Will the Fed cut rates in December 2026?",
        "Will the Fed cut interest rates in December 2026?",
        "Will Bitcoin close above 100k in 2026?",
    ]
    ids = cluster_texts(texts)
    assert ids[0] == ids[1] != ids[2]


def test_the_cluster_id_is_the_first_member_so_it_is_stable() -> None:
    texts = ["alpha beta gamma delta", "unrelated words here", "alpha beta gamma delta epsilon"]
    assert cluster_texts(texts) == [0, 1, 0]


def test_no_text_no_cluster() -> None:
    assert cluster_texts([]) == []


def test_a_higher_threshold_splits_what_a_lower_one_joins() -> None:
    texts = ["alpha beta gamma delta", "alpha beta gamma epsilon"]  # overlap 3 of 5 = 0.6
    assert cluster_texts(texts, threshold=0.5) == [0, 0]
    assert cluster_texts(texts, threshold=0.9) == [0, 1]
