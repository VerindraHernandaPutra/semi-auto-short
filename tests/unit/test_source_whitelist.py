import pytest

from shorts_engine.config import get_config
from shorts_engine.providers.search.tiers import classify_domain, source_independence_domain


@pytest.fixture
def whitelist():
    return get_config().source_whitelist


@pytest.mark.parametrize(
    ("host", "tier"),
    [
        ("pubmed.ncbi.nlm.nih.gov", "tier_a"),  # seen in a live search; it is NIH
        ("tsapps.nist.gov", "tier_a"),  # seen in a live search; it is NIST
        ("ntrs.nasa.gov", "tier_a"),
        ("pateklab.biology.duke.edu", "tier_a"),
        ("bio.ox.ac.uk", "tier_a"),
        ("academic.oup.com", "tier_a"),
        ("www.science.org", "tier_a"),
        ("pnas.org", "tier_a"),
        ("pbs.org", "tier_b"),
        ("en.wikipedia.org", "tier_c"),
    ],
)
def test_reputable_domains_get_their_tier(whitelist, host, tier):
    assert classify_domain(host, whitelist) == tier


@pytest.mark.parametrize(
    "host",
    [
        "facebook.com",
        "www.youtube.com",
        "reddit.com",
        "researchgate.net",  # user uploaded papers, not reviewed
        "physics.stackexchange.com",
        "nih.gov.attacker.example",  # look-alike: .gov must be the real ending
        "fake-edu.com",
        "oup.com.evil.example",
    ],
)
def test_social_media_forums_and_lookalikes_stay_unlisted(whitelist, host):
    assert classify_domain(host, whitelist) == "unlisted"


@pytest.mark.parametrize(
    ("host", "independent_domain"),
    [
        ("pubmed.ncbi.nlm.nih.gov", "nih.gov"),
        ("www.ncbi.nlm.nih.gov", "nih.gov"),
        ("tsapps.nist.gov", "nist.gov"),
        ("bio.ox.ac.uk", "ox.ac.uk"),
        ("pateklab.biology.duke.edu", "duke.edu"),
    ],
)
def test_pages_of_one_organisation_count_as_one_independent_domain(
    whitelist, host, independent_domain
):
    assert source_independence_domain(host, whitelist) == independent_domain
