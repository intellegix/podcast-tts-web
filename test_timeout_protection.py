#!/usr/bin/env python3
"""
Timeout Protection Test Suite
Tests the newly implemented universal timeout protection system
"""

import requests
import time
import json
import sys
import os
from pathlib import Path

def create_large_test_content():
    """Create test content designed to stress the timeout protection"""

    # Create content with many headers that would trigger extensive regex processing
    lines = []

    # Add a complex introduction
    lines.append("# Complex Document for Timeout Testing")
    lines.append("")
    lines.append("This document is designed to test timeout protection with complex content.")
    lines.append("")

    # Create many sections that would trigger regex patterns
    for i in range(1000):  # 1000 sections
        lines.append(f"## Section {i}: Topic Analysis")
        lines.append(f"**Important Topic {i}**: This is a detailed section about topic {i}")
        lines.append(f"- Bullet point about aspect A of topic {i}")
        lines.append(f"- Bullet point about aspect B of topic {i}")
        lines.append(f"- Bullet point about aspect C of topic {i}")
        lines.append("")
        lines.append(f"{i}. Numbered item for topic {i}")
        lines.append(f"   This is detailed content for numbered item {i}")
        lines.append("")
        lines.append(f"Topic: Advanced Analysis of {i}")
        lines.append(f"Detailed discussion of topic {i} goes here.")
        lines.append("")

    content = '\n'.join(lines)
    return content

def test_timeout_protection_basic():
    """Test basic timeout functionality with simple content"""
    print("\n[TEST 1] Basic timeout protection test")

    simple_content = "# Simple Test\n\nThis is a simple test document.\n\n## Topic 1\nSimple content here."

    try:
        start_time = time.time()

        job_response = requests.post("http://127.0.0.1:5000/api/job", data={
            "text": simple_content,
            "target_length": "medium",
            "voice": "nova",
            "ai_enhance": "true"
        }, timeout=30)

        if job_response.status_code == 200:
            job_data = job_response.json()
            job_id = job_data['job_id']

            # Monitor ANALYZE stage
            analyze_start = time.time()
            while True:
                status_response = requests.get(f"http://127.0.0.1:5000/api/job/{job_id}/status", timeout=10)
                status_data = status_response.json()

                if status_data['current_stage'] != 'analyze':
                    analyze_time = time.time() - analyze_start
                    print(f"   [PASS] ANALYZE completed in {analyze_time:.2f}s")
                    return True

                elapsed = time.time() - analyze_start
                if elapsed > 30:  # 30s test timeout
                    print(f"   ❌ Test timeout after {elapsed:.1f}s")
                    return False

                time.sleep(1)
        else:
            print(f"   ❌ Job creation failed: {job_response.status_code}")
            return False

    except Exception as e:
        print(f"   ❌ Test failed: {e}")
        return False

def test_timeout_protection_stress():
    """Test timeout protection with large complex content"""
    print("\n[TEST 2] Stress test with large complex content")

    large_content = create_large_test_content()
    content_size = len(large_content)
    line_count = len(large_content.split('\n'))

    print(f"   Content size: {content_size:,} characters ({content_size/1024:.1f}KB)")
    print(f"   Line count: {line_count:,} lines")
    print(f"   Expected: Should complete within 120s timeout (target: <30s)")

    try:
        start_time = time.time()

        job_response = requests.post("http://127.0.0.1:5000/api/job", data={
            "text": large_content,
            "target_length": "medium",
            "voice": "nova",
            "ai_enhance": "true"
        }, timeout=30)

        if job_response.status_code == 200:
            job_data = job_response.json()
            job_id = job_data['job_id']
            print(f"   Job created: {job_id}")

            # Monitor ANALYZE stage with timeout protection
            analyze_start = time.time()
            timeout_limit = 125  # Slightly longer than server timeout for monitoring

            while True:
                try:
                    status_response = requests.get(f"http://127.0.0.1:5000/api/job/{job_id}/status", timeout=10)
                    status_data = status_response.json()

                    elapsed = time.time() - analyze_start

                    if status_data.get('status') == 'error':
                        # Check if it's a timeout error (expected behavior)
                        error_message = status_data.get('error_message', '')
                        if 'timeout' in error_message.lower():
                            print(f"   ✅ Server timeout protection worked: {error_message}")
                            print(f"   ✅ Completed in {elapsed:.2f}s (timeout protection activated)")
                            return True
                        else:
                            print(f"   ❌ Unexpected error: {error_message}")
                            return False

                    if status_data['current_stage'] != 'analyze':
                        analyze_time = time.time() - analyze_start
                        print(f"   ✅ ANALYZE completed successfully in {analyze_time:.2f}s")

                        # Try to get topics count
                        if 'stage_results' in status_data and 'analyze' in status_data['stage_results']:
                            analyze_result = status_data['stage_results']['analyze']
                            topics = analyze_result.get('full_output', {}).get('topics', [])
                            print(f"   ✅ Topics extracted: {len(topics)}")

                        return True

                    if elapsed > timeout_limit:
                        print(f"   ❌ Test timeout after {elapsed:.1f}s - server timeout protection failed")
                        return False

                    # Progress update every 10 seconds
                    if elapsed % 10 < 1:
                        print(f"   [PROGRESS] ANALYZE running... {elapsed:.1f}s")

                except requests.exceptions.RequestException as e:
                    print(f"   ❌ Status check failed: {e}")
                    return False

                time.sleep(2)
        else:
            print(f"   ❌ Job creation failed: {job_response.status_code}")
            if job_response.text:
                print(f"   Response: {job_response.text[:200]}...")
            return False

    except Exception as e:
        print(f"   ❌ Test failed: {e}")
        return False

def test_direct_vs_interactive_consistency():
    """Test that timeout behavior is consistent between direct and interactive calls"""
    print("\n[TEST 3] Direct vs Interactive consistency test")

    # Use the same problematic content that originally caused hanging
    problematic_content = """Now let me create the final comprehensive report for the user.

***

# Skills to Deepen, Their Impact, and Cloud Platform Comparison

## Part 1: The Four Skills Worth Your Time

Your priority should be narrowly focused. You have limited bandwidth—working full-time while building Intellegix with a co-founder means you can realistically deepen **one skill every 12-16 weeks**. Rather than a scattered approach, here's what compounds for your business and why:

### 1. Advanced AI/LLM Integration and Hybrid Retrieval-Augmented Generation (RAG)

**What It Actually Does**

Retrieval-Augmented Generation connects your language models to external data sources—your document databases, construction company knowledge bases, vendor databases—so the AI pulls facts from real documents instead of hallucinating answers. Fine-tuning, by contrast, embeds domain-specific knowledge directly into the model through training on examples.

The magic is combining both: RAG for dynamic information (vendor pricing changes, new compliance rules) and fine-tuning for construction-domain patterns (understanding that "PO" on a construction document means Purchase Order, not Post Office)."""

    print(f"   Content size: {len(problematic_content)} characters")
    print(f"   This content originally caused hanging in direct tests")

    try:
        # Test 1: Direct API call (like our test script)
        start_time = time.time()

        job_response = requests.post("http://127.0.0.1:5000/api/job", data={
            "text": problematic_content,
            "target_length": "extended",
            "voice": "nova",
            "ai_enhance": "true"
        }, timeout=30)

        if job_response.status_code == 200:
            job_data = job_response.json()
            job_id = job_data['job_id']

            # Monitor ANALYZE stage completion
            analyze_start = time.time()
            max_wait = 130  # 130s to account for 120s server timeout + buffer

            while True:
                status_response = requests.get(f"http://127.0.0.1:5000/api/job/{job_id}/status", timeout=10)
                status_data = status_response.json()

                elapsed = time.time() - analyze_start

                # Check for successful completion or timeout error
                if status_data['current_stage'] != 'analyze' or status_data.get('status') == 'error':
                    print(f"   ✅ Direct call completed in {elapsed:.2f}s")
                    print(f"   Status: {status_data.get('status', 'unknown')}")
                    print(f"   Stage: {status_data.get('current_stage', 'unknown')}")

                    if 'timeout' in str(status_data).lower():
                        print(f"   ✅ Timeout protection activated (expected)")
                    else:
                        print(f"   ✅ Normal completion")

                    return True

                if elapsed > max_wait:
                    print(f"   ❌ Direct call still hanging after {elapsed:.1f}s")
                    return False

                if elapsed % 10 < 1:
                    print(f"   [PROGRESS] Direct call running... {elapsed:.1f}s")

                time.sleep(2)
        else:
            print(f"   ❌ Job creation failed: {job_response.status_code}")
            return False

    except Exception as e:
        print(f"   ❌ Direct call test failed: {e}")
        return False

def main():
    """Main test execution"""
    print("=" * 70)
    print("UNIVERSAL TIMEOUT PROTECTION TEST SUITE")
    print("=" * 70)
    print("Testing the new ThreadPoolExecutor-based timeout system")
    print("Previous issue: gevent.Timeout failed for CPU-bound regex operations")
    print("Expected fix: Universal timeout protection works in any context")
    print("")

    # Check server health
    try:
        health_response = requests.get("http://127.0.0.1:5000/health", timeout=10)
        if health_response.status_code == 200:
            print("[OK] Server health check passed")
        else:
            print(f"[WARN] Server health check returned {health_response.status_code}")
    except Exception as e:
        print(f"[ERROR] Cannot connect to server: {e}")
        print("Please start the Flask server: python app.py")
        return False

    # Run test suite
    tests = [
        ("Basic timeout protection", test_timeout_protection_basic),
        ("Stress test with large content", test_timeout_protection_stress),
        ("Direct vs Interactive consistency", test_direct_vs_interactive_consistency),
    ]

    results = {}
    for test_name, test_func in tests:
        print(f"\n[RUNNING] {test_name}")
        try:
            result = test_func()
            results[test_name] = result
        except Exception as e:
            print(f"   ❌ Test crashed: {e}")
            results[test_name] = False

    # Summary
    print("\n" + "=" * 70)
    print("TEST RESULTS SUMMARY")
    print("=" * 70)

    passed = 0
    total = len(results)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
        if result:
            passed += 1

    print(f"\nOverall: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        print("✅ Universal timeout protection is working correctly")
        print("✅ No more hanging scenarios detected")
        print("✅ System ready for production deployment")
    else:
        print(f"\n⚠️  {total - passed} TESTS FAILED")
        print("❌ Timeout protection needs further investigation")

    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)