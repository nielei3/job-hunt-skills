"""Tests for is_us_location helper and its integration with filter_titles.classify_one."""
import filter_titles as ft
from job_scout_lib import is_us_location


CFG = {
    'location_filter': {
        'enabled': True,
        'unknown_policy': 'pass',
        'non_us_keywords': [
            'canada', 'india', 'china', 'poland', 'spain', 'brazil',
            'united kingdom', ' uk', 'mexico', 'germany', 'france',
            'netherlands', 'amsterdam', 'london', 'tokyo', 'japan',
            'bangalore', 'são paulo', 'sao paulo', 'emea', 'apac',
        ],
    },
    'filter': {
        'exclude_keywords': ['frontend'],
        'include_keywords': ['staff', 'platform'],
        'allow_override_companies': ['Anthropic'],
        'ambiguous_policy': 'pending',
    },
}


# --- helper unit tests -------------------------------------------------------

def test_explicit_us_strings_pass():
    for loc in ['Remote-US', 'Remote US', 'Remote - USA', 'Remote - United States',
                'United States', 'Remote, USA', 'USA - Remote', 'San Francisco, CA, US',
                'New York, NY']:
        is_us, reason = is_us_location(loc, CFG)
        assert is_us, f'{loc!r} should be US (reason={reason!r})'


def test_non_us_strings_filtered():
    cases = [
        ('Remote Canada', 'canada'),
        ('Bangalore, India', 'india'),
        ('China', 'china'),
        ('Remote Poland', 'poland'),
        ('Remote Spain', 'spain'),
        ('Brazil', 'brazil'),
        ('São Paulo, Brazil', 'brazil'),
        ('Amsterdam, Netherlands', 'netherlands'),
        ('Remote - Ontario, Canada', 'canada'),
        ('Remote UK', 'uk'),
    ]
    for loc, expected_kw in cases:
        is_us, reason = is_us_location(loc, CFG)
        assert not is_us, f'{loc!r} should be filtered (reason={reason!r})'
        assert reason == expected_kw, f'{loc!r}: expected {expected_kw!r} got {reason!r}'


def test_multi_region_with_us_passes():
    # If a job lists US alongside a non-US country, US wins (it's offered for US too).
    for loc in ['US, Canada', 'Remote (US, Canada, UK)', 'United States / Mexico']:
        is_us, _ = is_us_location(loc, CFG)
        assert is_us, f'{loc!r} should pass because US is listed'


def test_empty_location_passes_with_default_policy():
    is_us, _ = is_us_location('', CFG)
    assert is_us
    is_us, _ = is_us_location(None, CFG)
    assert is_us
    is_us, _ = is_us_location('   ', CFG)
    assert is_us


def test_empty_location_filtered_when_policy_filter():
    cfg = {'location_filter': {**CFG['location_filter'], 'unknown_policy': 'filter'}}
    is_us, _ = is_us_location('', cfg)
    assert not is_us


def test_unknown_country_passes_with_default_policy():
    # No US token, no non-US keyword match — defaults to pass.
    is_us, _ = is_us_location('Atlantis', CFG)
    assert is_us


def test_word_boundary_avoids_substring_false_positives():
    # "USDS" should NOT match "US" as a token (no whole-word us).
    is_us, _ = is_us_location('USDS Joint Venture', CFG)
    assert is_us
    # "Indianapolis" should NOT match "india" with word-boundary matching.
    is_us, reason = is_us_location('Indianapolis, IN', CFG)
    assert is_us, f'word boundary should reject substring match (reason={reason!r})'
    # "truck" should NOT match "uk".
    is_us, _ = is_us_location('Truckee, CA', CFG)
    assert is_us


def test_helper_no_config_uses_defaults():
    # Without cfg, no non_us list is configured → only US-token detection works.
    is_us, _ = is_us_location('Remote - USA', None)
    assert is_us
    is_us, _ = is_us_location('Bangalore, India', None)
    # No non_us_keywords in defaults → unknown → pass
    assert is_us


# --- integration with filter_titles.classify_one ----------------------------

def test_classify_one_filters_non_us_before_title_check():
    job = {'title': 'Staff Software Engineer', 'company': 'Airbnb', 'location': 'Bangalore, India'}
    out = ft.classify_one(job, CFG)
    assert out['filter_status'] == 'filtered_out'
    assert 'non-us' in out['filter_reason']
    assert 'india' in out['filter_reason']


def test_classify_one_filters_non_us_for_override_company():
    # Override is supposed to bypass title rules, but geo filter still applies.
    job = {'title': 'Software Engineer', 'company': 'Anthropic', 'location': 'Remote Canada'}
    out = ft.classify_one(job, CFG)
    assert out['filter_status'] == 'filtered_out'
    assert 'non-us' in out['filter_reason']


def test_classify_one_passes_us_with_include_keyword():
    job = {'title': 'Staff Software Engineer', 'company': 'Airbnb', 'location': 'Remote - USA'}
    out = ft.classify_one(job, CFG)
    assert out['filter_status'] == 'passes_rules'


def test_classify_one_geo_disabled_skips_filter():
    cfg = {**CFG, 'location_filter': {**CFG['location_filter'], 'enabled': False}}
    job = {'title': 'Staff Software Engineer', 'company': 'Airbnb', 'location': 'Bangalore, India'}
    out = ft.classify_one(job, cfg)
    # Title matches 'staff' → passes
    assert out['filter_status'] == 'passes_rules'


def test_classify_one_empty_location_passes_through_to_title_rules():
    # Unknown-policy=pass means location is not a blocker; title rules decide.
    job = {'title': 'Senior Frontend Engineer', 'company': 'Figma', 'location': ''}
    out = ft.classify_one(job, CFG)
    assert out['filter_status'] == 'filtered_out'
    assert 'frontend' in out['filter_reason']
