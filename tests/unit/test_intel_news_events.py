from datetime import UTC, datetime, timedelta

from ambre.intelligence.news_events import NewsItem, cluster_items, title_tokens

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


def item(oid: str, title: str, source: str, hours: float = 0, url: str | None = None) -> NewsItem:
    return NewsItem(
        oid, url or f"https://{source}.example/{oid}", title, source, T0 + timedelta(hours=hours)
    )


def test_two_outlets_reporting_one_story_are_one_event_with_two_sources() -> None:
    clusters = cluster_items(
        [
            item("a", "SpaceX Starship completes first orbital refueling test", "sfn", 0),
            item(
                "b",
                "SpaceX Starship completes first orbital refueling test, company says",
                "nsf",
                2.5,
            ),
            item("c", "Blue Origin New Glenn launches second mission", "sn", 8),
        ]
    )
    assert len(clusters) == 2
    story = next(c for c in clusters if "a" in c.members)
    assert story.members == ("a", "b") and story.sources == ("nsf", "sfn")
    assert story.first_at == T0 and story.last_at == T0 + timedelta(hours=2.5)


def test_same_entity_different_story_is_not_merged() -> None:
    clusters = cluster_items(
        [
            item("a", "SpaceX Starship completes orbital refueling test", "x"),
            item("b", "SpaceX Starship grounded after anomaly investigation", "y", 1),
        ]
    )
    assert len(clusters) == 2


def test_the_same_url_is_one_event_even_with_different_titles_and_tracking_noise() -> None:
    clusters = cluster_items(
        [
            item(
                "a", "Parliament passes the budget", "p", url="https://n.example/story?utm_source=a"
            ),
            item("b", "Late vote approves spending plan", "q", 30, url="https://N.example/story/"),
        ]
    )
    assert len(clusters) == 1 and clusters[0].members == ("a", "b")


def test_far_apart_in_time_means_a_new_event_even_with_the_same_words() -> None:
    clusters = cluster_items(
        [
            item("a", "Central bank holds rates steady", "x", 0),
            item("b", "Central bank holds rates steady", "y", 24 * 10),
        ]
    )
    assert len(clusters) == 2


def test_result_does_not_depend_on_input_order_and_ids_are_stable() -> None:
    items = [
        item("a", "Storm closes ports along the coast", "x", 0),
        item("b", "Storm closes ports along the coast today", "y", 1),
        item("c", "Election results due Friday", "z", 2),
    ]
    forward = cluster_items(items)
    backward = cluster_items(list(reversed(items)))
    assert forward == backward
    assert [c.event_id for c in forward] == ["a", "c"]


def test_no_items_no_clusters_and_stop_words_are_ignored() -> None:
    assert cluster_items([]) == []
    assert title_tokens("The new rates, after the vote") == frozenset({"rates", "vote"})
