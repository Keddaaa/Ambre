"""Research topics and queries for markets with no specialised connector (pure)."""

from datetime import UTC, datetime, timedelta

from ambre.intelligence.planning import Gazetteer
from ambre.intelligence.queries import group_search, topic_for, topic_relevant
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
