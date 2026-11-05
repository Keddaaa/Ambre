"""Research topics and queries for markets with no specialised connector (pure)."""

from datetime import UTC, datetime, timedelta

from ambre.intelligence.planning import Gazetteer
from ambre.intelligence.queries import Topic, group_search, topic_for, topic_relevant
from ambre.intelligence.resolution import MarketDomain, analyze_market, load_rules

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
RULES = load_rules()
GAZ = Gazetteer([])


def topic(question: str):
    a = analyze_market("m", question, NOW + timedelta(days=60), NOW, GAZ, RULES)
    return a, topic_for(a)


def test_a_technology_market_gets_an_entity_centred_topic() -> None:
    a, t = topic("Will OpenAI release a new model before 2027?")
    assert a.domain is MarketDomain.TECHNOLOGY
    assert t is not None and t.primary == "OpenAI"
    assert t.key == "technology|openai"
    assert "release" in t.terms and "model" in t.terms
    assert "Will" not in t.terms and "a" not in t.terms  # stop words never become search terms


def test_related_markets_about_the_same_entity_share_one_topic_key() -> None:
    _, release = topic("Will OpenAI release a new model before 2027?")
    _, announce = topic("Will OpenAI announce a model by December 31, 2026?")
    _, other_company = topic("Will Anthropic release a new model before 2027?")
    assert release is not None and announce is not None and other_company is not None
    assert release.key == announce.key  # the information is shared...
    assert release.key != other_company.key
    assert release.terms != announce.terms  # ...but each market keeps its own terms


def test_the_same_entity_in_another_domain_is_not_merged() -> None:
    _, tech = topic("Will OpenAI release a new model before 2027?")
    _, finance = topic("Will OpenAI stock shares trade above $100 by December 31, 2026?")
    assert tech is not None and finance is not None and tech.key != finance.key


def test_group_search_uses_the_entity_and_only_terms_every_market_shares() -> None:
    _, a = topic("Will OpenAI release a new model before 2027?")
    _, b = topic("Will OpenAI announce a model by December 31, 2026?")
    _, c = topic("Will OpenAI release GPT-6 in 2026?")
    assert a is not None and b is not None and c is not None
    assert group_search([a, b]) == "OpenAI model"
    assert group_search([a, c]) == "OpenAI release"
    assert group_search([a]) == "OpenAI release model"


def test_a_multiword_entity_is_quoted() -> None:
    _, t = topic("Will Federal Reserve cut the interest rate in 2026?")
    assert t is not None
    assert group_search([t]).startswith('"Federal Reserve"')


def test_a_market_without_an_entity_falls_back_to_its_significant_words() -> None:
    a, t = topic("will the majority of russians cease supporting the actions in 2026?")
    assert a.entities == ()
    assert t is not None and t.primary is None
    assert t.key.startswith("general|") and len(t.terms) >= 2
    assert all(len(w) >= 5 for w in t.terms)


def test_a_question_with_nothing_searchable_has_no_topic() -> None:
    _, t = topic("Will it be?")
    assert t is None


def test_the_topic_is_deterministic() -> None:
    q = "Will OpenAI release a new model before 2027?"
    assert topic(q)[1] == topic(q)[1]


def test_relevance_needs_the_entity_and_one_of_the_market_s_own_terms() -> None:
    _, t = topic("Will OpenAI release a new model before 2027?")
    assert t is not None
    assert topic_relevant("OpenAI releases a new model for developers", t)
    assert topic_relevant("OpenAI to release GPT-6 next month", t)
    assert not topic_relevant("Anthropic releases a new model", t)  # other entity
    assert not topic_relevant("OpenAI hires a new chief of staff", t)  # entity only
    assert not topic_relevant("", t)


def test_entityless_relevance_needs_at_least_two_terms() -> None:
    _, t = topic("will the majority of russians cease supporting the actions in 2026?")
    assert t is not None and len(t.terms) >= 2
    first, second = t.terms[0], t.terms[1]
    assert topic_relevant(f"{first} and {second} in the news", t)
    assert not topic_relevant(f"only {first} here", t)


# ------------------------------------------------------------------ generic terms never decide


def _topic(primary: str | None, *terms: str) -> Topic:
    return Topic(key="t|x", domain=MarketDomain.TECHNOLOGY, primary=primary, terms=terms)


def test_a_generic_word_alone_does_not_make_an_article_relevant() -> None:
    """Observed on real data: 'SpaceX stock crash and close below $100' was linked to a Falcon 9
    launch story only because the title says 'close out October'."""
    t = _topic("SpaceX", "stock", "crash", "close", "below", "anyday")
    launch = "Live coverage: SpaceX to close out October with Halloween eve Falcon 9 launch"
    assert not topic_relevant(launch, t)
    assert topic_relevant("SpaceX stock crashes after the launch failure", t)
    assert topic_relevant("SpaceX stock slides, analysts see a crash", t)


def test_specific_terms_still_match_next_to_generic_ones() -> None:
    t = _topic("Falcon", "launch", "times")
    assert topic_relevant("SpaceX Falcon 9 launch carries 24 satellites", t)
    assert not topic_relevant("Falcon sightings doubled several times this year", t)


def test_a_topic_made_only_of_generic_terms_keeps_its_old_rule() -> None:
    """Dropping every term would let the entity alone decide, which is looser, not stricter."""
    t = _topic("Starship", "reach")
    assert topic_relevant("Starship will reach orbit on the next flight", t)
    assert not topic_relevant("Starship completes a static fire", t)


def test_entityless_topics_count_only_specific_terms() -> None:
    t = _topic(None, "rocket", "close", "engine")
    assert topic_relevant("new rocket engine passes a test", t)
    assert not topic_relevant("rocket maker will close its plant", t)  # 'rocket' + generic 'close'


def test_a_sentence_opening_word_is_not_an_entity() -> None:
    _, t = topic("This market resolves YES when an artificial agent joins a board of directors")
    assert t is not None
    assert t.primary not in ("This", "YES")


def test_a_second_actor_of_the_market_is_kept_as_a_companion() -> None:
    _, t = topic("Will China launch a full-scale invasion of Taiwan by the end of 2028?")
    assert t is not None and t.primary == "China"
    assert t.companions == ("Taiwan",)


def test_a_short_place_name_does_not_become_a_required_companion() -> None:
    _, t = topic("Will China land men on the moon before the USA?")
    assert t is not None and t.companions == ()
    assert topic_relevant("China releases timeline for a landing on the Moon", t)


def test_the_primary_entity_alone_is_not_enough_when_the_market_names_another() -> None:
    _, t = topic("Will China buy the H200 from NVIDIA")
    assert t is not None
    assert not topic_relevant("China releases timeline for lunar landing on the Moon", t)
    assert topic_relevant("China approves imports of Nvidia chips", t)


def test_a_shared_verb_does_not_tie_two_different_stories() -> None:
    _, t = topic("Will China launch a full-scale invasion of Taiwan by the end of 2028?")
    assert t is not None
    assert not topic_relevant("Falcon 9 and China to launch new missions this week", t)
    assert topic_relevant("Taiwan drills as China masses ships in the strait", t)


def test_two_of_the_market_s_terms_can_stand_in_for_the_companion() -> None:
    _, t = topic("Will Kevin Kiley win the House race in California?")
    assert t is not None and t.primary == "Kevin Kiley"
    assert topic_relevant("Kevin Kiley set to win tight race", t)
    assert not topic_relevant("Kevin Kiley will win an award", t)


def test_a_shared_release_verb_does_not_tie_ai_rules_to_a_moon_timeline() -> None:
    _, t = topic("Will China restrict the open release of frontier AI models above a threshold?")
    assert t is not None
    assert not topic_relevant("China releases timeline for lunar landing on the Moon", t)
    assert topic_relevant("China to restrict frontier AI models under new rules", t)


def test_a_place_called_space_center_is_not_a_data_center_in_space() -> None:
    _, t = topic("Will there be a functional 100MW data center in space before 2030?")
    assert t is not None
    assert not topic_relevant("Happy 65 Years: NASA's Johnson Space Center Then and Now", t)
