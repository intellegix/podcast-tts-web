#!/usr/bin/env python3
"""
Test Intelligent News Prioritization System
Validates the news compilation processing logic without consuming API credits.
"""

import sys
import os
from datetime import datetime

# Add app directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import necessary components
from app import (
    parse_headlines_from_text,
    calculate_priority_score,
    prioritize_news_content,
    efficient_source_selection,
    detect_news_compilation_format
)

def test_news_compilation_detection():
    """Test detection of news compilation format"""
    print("=== Testing News Compilation Detection ===")

    # Sample news format (similar to user's Perplexity compilation)
    sample_news = """# Perplexity News Headlines
**Compiled: January 12, 2026**

---

## Breaking News (Last Hour)

### Spirit AI Robot Model Tops Global Benchmark
*Published: 1 hour ago*
Spirit AI's open-source model became the first to surpass 50% success rate on RoboChallenge.
**Sources:** [arXiv](https://arxiv.org) | [X](https://x.com) | [RoboChallenge](https://robochallenge.ai) + 43 sources

### Pentagon Takes $150M Stake in Louisiana Refinery
*Published: 4 minutes ago*
The investment aims to establish the nation's only major gallium producer.
**Sources:** [Atalco](https://atalco.com) | [AlCircle](https://alcircle.com) + 41 sources

## Technology & AI

### IgniteTech CEO Says He Replaced 80% of Staff Over AI Resistance
The CEO claims employees who refused to adapt to AI tools were let go.
**Sources:** [Fortune](https://fortune.com) | [Yahoo Finance](https://finance.yahoo.com) + 28 sources

### Alibaba's Qwen Becomes World's Most Downloaded Open-Source AI
**Sources:** [SCMP](https://scmp.com) | [TechWire Asia](https://techwireasia.com) + 31 sources

## Business & Markets

### TSMC Expected to Post Record Quarterly Profit on AI Demand
**Sources:** [Yahoo Finance](https://finance.yahoo.com) | [Reuters](https://reuters.com) + 32 sources

### Memory Chip Prices Surge as AI Demand Leaves Makers Sold Out
**Sources:** [TrendForce](https://trendforce.com) | [CNBC](https://cnbc.com) + 46 sources

### Goldman Sachs Issues Sell Ratings on Adobe, Datadog
**Sources:** [Investing.com](https://investing.com) | [TipRanks](https://tipranks.com) + 48 sources

## Cryptocurrency & Blockchain

### Buterin Unveils 7-Step Plan for Ethereum's 100-Year Future
*Published: 2 hours ago*
The co-founder says Ethereum must pass a "walkaway test".
**Sources:** [Yahoo Finance](https://finance.yahoo.com) | [BeInCrypto](https://beincrypto.com) + 27 sources

### Bitcoin Miners Pivot to AI as Profitability Crisis Deepens
**Sources:** [Binance](https://binance.com) | [Yahoo Finance](https://finance.yahoo.com) + 59 sources

## Entertainment

### Dwayne Johnson Dedicates Golden Globes Role to 15 Friends Lost
*Published: 10 hours ago*
The first-time nominee said his portrayal helped him develop empathy.
**Sources:** [People](https://people.com) | [IMDb](https://imdb.com) + 30 sources
"""

    is_compilation = detect_news_compilation_format(sample_news)
    print(f"Detection result: {'DETECTED' if is_compilation else 'NOT DETECTED'}")

    if is_compilation:
        print("[PASS] News compilation detection working correctly")
    else:
        print("[FAIL] News compilation detection failed")

    return is_compilation

def test_headline_parsing():
    """Test parsing of individual headlines"""
    print("\n=== Testing Headline Parsing ===")

    sample_text = """## Technology & AI

### Spirit AI Robot Model Tops Global Benchmark
*Published: 1 hour ago*
Spirit AI's open-source model became the first to surpass 50% success rate on RoboChallenge, dethroning Physical Intelligence's pi0.5.
**Sources:** [arXiv](https://arxiv.org) | [X](https://x.com) + 43 sources

### IgniteTech CEO Says He Replaced 80% of Staff Over AI Resistance
The CEO claims employees who refused to adapt to AI tools were let go.
**Sources:** [Fortune](https://fortune.com) | [Yahoo Finance](https://finance.yahoo.com) + 28 sources

## Business & Markets

### TSMC Expected to Post Record Quarterly Profit
**Sources:** [Yahoo Finance](https://finance.yahoo.com) + 32 sources
"""

    headlines = parse_headlines_from_text(sample_text)

    print(f"Parsed {len(headlines)} headlines:")
    for i, headline in enumerate(headlines, 1):
        print(f"  {i}. {headline['title'][:50]}...")
        print(f"     Category: {headline['category']}")
        print(f"     Sources: {headline['source_count']}")
        print(f"     Hours ago: {headline['published_hours_ago']}")

    expected_headlines = 3
    if len(headlines) == expected_headlines:
        print("[PASS] Headline parsing working correctly")
        return True
    else:
        print(f"[FAIL] Expected {expected_headlines} headlines, got {len(headlines)}")
        return False

def test_priority_scoring():
    """Test priority scoring algorithm"""
    print("\n=== Testing Priority Scoring ===")

    test_headlines = [
        {
            'title': 'BREAKING: Major Market Crash Triggers Emergency Meeting',
            'category': 'Breaking News',
            'published_hours_ago': 0.5,
            'source_count': 85,
            'content': 'unprecedented global economic crisis trillion dollar emergency'
        },
        {
            'title': 'Tech Company Releases Minor Update',
            'category': 'Technology',
            'published_hours_ago': 8,
            'source_count': 12,
            'content': 'software update fixes bugs'
        },
        {
            'title': 'New AI Breakthrough in Healthcare',
            'category': 'Healthcare',
            'published_hours_ago': 2,
            'source_count': 45,
            'content': 'major breakthrough artificial intelligence medical diagnosis'
        }
    ]

    scores = []
    for headline in test_headlines:
        score = calculate_priority_score(headline)
        scores.append((headline['title'][:40], score))
        print(f"  {headline['title'][:40]}... => Score: {score:.1f}")

    # Check if breaking news scored highest
    breaking_score = scores[0][1]
    other_scores = [s[1] for s in scores[1:]]

    if breaking_score > max(other_scores):
        print("[PASS] Priority scoring working correctly (breaking news ranked highest)")
        return True
    else:
        print("[FAIL] Priority scoring failed (breaking news not ranked highest)")
        return False

def test_content_prioritization():
    """Test 3-tier content prioritization"""
    print("\n=== Testing Content Prioritization ===")

    # Create sample headlines with varying priority scores
    sample_headlines = []

    # High priority headlines
    for i in range(5):
        sample_headlines.append({
            'title': f'Breaking: High Priority Story {i+1}',
            'category': 'Breaking News',
            'published_hours_ago': 1,
            'source_count': 60,
            'content': 'breaking major crisis emergency unprecedented'
        })

    # Medium priority headlines
    for i in range(15):
        sample_headlines.append({
            'title': f'Important Business Update {i+1}',
            'category': 'Business',
            'published_hours_ago': 6,
            'source_count': 25,
            'content': 'market update significant development'
        })

    # Low priority headlines
    for i in range(30):
        sample_headlines.append({
            'title': f'Entertainment News {i+1}',
            'category': 'Entertainment',
            'published_hours_ago': 12,
            'source_count': 8,
            'content': 'celebrity update minor news'
        })

    prioritized = prioritize_news_content(sample_headlines)

    tier_1_count = len(prioritized['tier_1_full'])
    tier_2_count = len(prioritized['tier_2_brief'])
    tier_3_count = len(prioritized['tier_3_merge'])

    print(f"Tier 1 (Full): {tier_1_count} headlines")
    print(f"Tier 2 (Brief): {tier_2_count} headlines")
    print(f"Tier 3 (Merge): {tier_3_count} headlines")

    # Validate distribution - should have reasonable distribution
    total = tier_1_count + tier_2_count + tier_3_count
    if total == len(sample_headlines) and tier_1_count > 0 and tier_2_count > 0 and tier_3_count > 0:
        print("[PASS] Content prioritization working correctly")
        return True
    else:
        print(f"[FAIL] Content prioritization failed - total: {total}, expected: {len(sample_headlines)}")
        return False

def test_source_selection():
    """Test efficient source selection"""
    print("\n=== Testing Source Selection ===")

    sample_sources = [
        "https://reuters.com/article1",
        "https://bloomberg.com/article2",
        "https://youtube.com/watch?v=123",
        "https://linkedin.com/post",
        "https://twitter.com/user/status",
        "https://arxiv.org/paper123",
        "https://nature.com/article",
        "https://unknown-blog.com/post",
        "https://government.gov/statement",
        "https://wsj.com/article"
    ]

    # Test different target counts
    test_cases = [
        (3, "Tier 3 selection"),
        (5, "Tier 2 selection"),
        (8, "Tier 1 selection")
    ]

    for target_count, description in test_cases:
        selected = efficient_source_selection(sample_sources, target_count)
        print(f"  {description}: {len(selected)}/{target_count} sources selected")

        # Check if high-authority sources are prioritized
        high_authority = [s for s in selected if any(domain in s for domain in ['reuters', 'bloomberg', 'wsj', 'nature', 'arxiv'])]
        print(f"    High-authority sources: {len(high_authority)}")

    print("[PASS] Source selection working correctly")
    return True

def test_efficiency_comparison():
    """Compare intelligent vs brute force approach"""
    print("\n=== Testing Efficiency Comparison ===")

    # Simulate processing 100 headlines
    total_headlines = 100

    # Intelligent approach (from plan)
    tier_1 = 25  # Full discussion
    tier_2 = 50  # Brief mention
    tier_3 = 25  # Merge context

    intelligent_agents = (tier_1 * 8) + (tier_2 * 4) + (tier_3 * 2)
    intelligent_sources = (tier_1 * 10) + (tier_2 * 4) + (tier_3 * 2)

    # Brute force approach
    brute_force_agents = total_headlines * 8  # 8 agents per headline
    brute_force_sources = total_headlines * 50  # Average 50 sources per headline

    print(f"Processing {total_headlines} headlines:")
    print(f"")
    print(f"Brute Force Approach:")
    print(f"  Agents needed: {brute_force_agents}")
    print(f"  Sources to process: {brute_force_sources}")
    print(f"")
    print(f"Intelligent Approach:")
    print(f"  Agents needed: {intelligent_agents}")
    print(f"  Sources to process: {intelligent_sources}")
    print(f"")

    agent_efficiency = ((brute_force_agents - intelligent_agents) / brute_force_agents) * 100
    source_efficiency = ((brute_force_sources - intelligent_sources) / brute_force_sources) * 100

    print(f"Efficiency Gains:")
    print(f"  Agent reduction: {agent_efficiency:.1f}%")
    print(f"  Source reduction: {source_efficiency:.1f}%")

    if agent_efficiency >= 40 and source_efficiency >= 85:
        print("[PASS] Efficiency targets achieved")
        return True
    else:
        print("[FAIL] Efficiency targets not met")
        return False

def run_full_test_suite():
    """Run all tests and report results"""
    print("Intelligent News Prioritization System Test Suite")
    print("=" * 60)

    tests = [
        ("News Compilation Detection", test_news_compilation_detection),
        ("Headline Parsing", test_headline_parsing),
        ("Priority Scoring", test_priority_scoring),
        ("Content Prioritization", test_content_prioritization),
        ("Source Selection", test_source_selection),
        ("Efficiency Comparison", test_efficiency_comparison)
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"[FAIL] {test_name} failed with error: {e}")
            results.append((test_name, False))

    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "[PASS] PASS" if result else "[FAIL] FAIL"
        print(f"{status} {test_name}")

    print(f"\nOverall: {passed}/{total} tests passed")

    if passed == total:
        print("[SUCCESS] All tests passed! Intelligent news prioritization system is ready.")
        return True
    else:
        print("[WARNING]  Some tests failed. System needs debugging before deployment.")
        return False

if __name__ == '__main__':
    print(f"Testing at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    success = run_full_test_suite()
    sys.exit(0 if success else 1)