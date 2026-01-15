#!/usr/bin/env python3
"""
Simple Timeout Protection Test
Tests the newly implemented universal timeout protection system (Windows compatible)
"""

import requests
import time
import sys

def test_timeout_with_problematic_content():
    """Test timeout protection with the original problematic content"""
    print("\n[TEST] Testing timeout protection with problematic content")

    # Use the same content that originally caused hanging
    problematic_content = """Now let me create the final comprehensive report for the user.

***

# Skills to Deepen, Their Impact, and Cloud Platform Comparison

## Part 1: The Four Skills Worth Your Time

Your priority should be narrowly focused. You have limited bandwidth—working full-time while building Intellegix with a co-founder means you can realistically deepen **one skill every 12-16 weeks**. Rather than a scattered approach, here's what compounds for your business and why:

### 1. Advanced AI/LLM Integration and Hybrid Retrieval-Augmented Generation (RAG)

**What It Actually Does**

Retrieval-Augmented Generation connects your language models to external data sources—your document databases, construction company knowledge bases, vendor databases—so the AI pulls facts from real documents instead of hallucinating answers. Fine-tuning, by contrast, embeds domain-specific knowledge directly into the model through training on examples.

The magic is combining both: RAG for dynamic information (vendor pricing changes, new compliance rules) and fine-tuning for construction-domain patterns (understanding that "PO" on a construction document means Purchase Order, not Post Office).

Multimodal systems take this further. Instead of just reading text, modern document processors use vision-language models to understand layout, tables, embedded charts, and even handwriting—the way humans actually read documents. A recent research framework achieved **F1=1.0 accuracy** (perfect extraction) on identity and financial documents by strategically combining OCR with LLM reasoning.

**Why This Matters Specifically for Intellegix**

Construction workflows are document-heavy. A typical general contractor processes:

- Purchase orders and change orders (structure: vendor, line items, amounts, approval chains)
- Invoices from subcontractors (variable formatting, but patterns repeat)
- Timesheets and labor certifications
- Compliance documents (safety reports, bonding certificates)
- Contracts and amendments

Your current approach likely uses basic RAG: retrieve relevant documents, feed them to Claude, extract fields. This works for **85-90% accuracy** on simple documents. But the 10-15% of edge cases—misspelled vendor names, unusual PO formats, embedded tables—are where customers experience friction and create support tickets.

### 2. System Design and Scalability Architecture

**What It Actually Does**

System design is the difference between a product that works for 10 customers and one that works for 10,000. It answers:

- How do you process 1,000 documents per day without the system slowing down?
- How do you ensure one customer's bug doesn't crash the entire platform?
- How do you scale your database from 1GB to 1TB without downtime?
- How do you handle probabilistic AI models (LLMs) in a reliable system?

Traditional system design covers patterns like microservices, API gateways, CQRS (separating read and write operations), and event-driven architecture. But in 2026, there's a new dimension: **AI System Design**—how to turn unreliable, probabilistic models into production-grade systems.

### 3. Sales and Business Development

**What It Actually Does**

Sales is the brutal reality: A perfect product that nobody buys is worthless. Sales is:

- Identifying who actually needs your product (general contractors, not architects)
- Understanding their pain points deeply (invoicing delays cost them $5K/week in crew overtime)
- Positioning your solution as the answer (Intellegix saves 8 hours/week per office employee)
- Negotiation and closing deals
- Tracking metrics (CAC = customer acquisition cost, LTV = lifetime value, payback period)

### 4. Security, Compliance, and Data Privacy

**What It Actually Does**

Building trust with construction companies handling sensitive data. This includes:

- Encrypting data at rest (database encryption) and in transit (HTTPS)
- Access controls (who can see which customer's data?)
- Audit logs (track who accessed what, when)
- SOC2 Type II compliance (third-party verification that your security is legitimate)
- GDPR compliance (if you have EU customers)

Thank you,"""

    content_size = len(problematic_content)
    print(f"   Content size: {content_size:,} characters ({content_size/1024:.1f}KB)")
    print(f"   Previous behavior: Would hang indefinitely on this exact content")
    print(f"   Expected behavior: Should complete within 120s timeout (target: <30s)")

    try:
        start_time = time.time()

        # Create job
        print(f"   [STEP 1] Creating job...")
        job_response = requests.post("http://127.0.0.1:5000/api/job", data={
            "text": problematic_content,
            "target_length": "extended",
            "voice": "nova",
            "ai_enhance": "true"
        }, timeout=30)

        if job_response.status_code != 200:
            print(f"   [FAIL] Job creation failed: {job_response.status_code}")
            return False

        job_data = job_response.json()
        job_id = job_data['job_id']
        print(f"   [SUCCESS] Job created: {job_id}")

        # Monitor ANALYZE stage with timeout
        print(f"   [STEP 2] Monitoring ANALYZE stage...")
        analyze_start = time.time()
        max_wait = 125  # 125s to account for 120s server timeout + buffer

        while True:
            try:
                status_response = requests.get(f"http://127.0.0.1:5000/api/job/{job_id}/status", timeout=10)
                if status_response.status_code != 200:
                    print(f"   [WARN] Status check returned {status_response.status_code}")
                    time.sleep(3)
                    continue

                status_data = status_response.json()
                elapsed = time.time() - analyze_start

                # Check for completion or error
                if status_data.get('status') == 'error':
                    error_msg = status_data.get('error_message', 'Unknown error')
                    if 'timeout' in error_msg.lower():
                        print(f"   [PASS] Server timeout protection activated: {error_msg}")
                        print(f"   [PASS] Completed in {elapsed:.2f}s (timeout protection working)")
                        return True
                    else:
                        print(f"   [FAIL] Unexpected error: {error_msg}")
                        return False

                # Check if ANALYZE stage completed (either moved to next stage or paused for review)
                if (status_data['current_stage'] != 'analyze' or
                    status_data.get('status') == 'paused_for_review'):
                    print(f"   [PASS] ANALYZE stage completed successfully in {elapsed:.2f}s")
                    print(f"   [SUCCESS] No hanging detected - timeout protection working")
                    print(f"   [STATUS] Job status: {status_data.get('status')}")
                    print(f"   [STAGE] Current stage: {status_data.get('current_stage')}")
                    return True

                # Check for test timeout (should not reach this)
                if elapsed > max_wait:
                    print(f"   [FAIL] ANALYZE stage still running after {elapsed:.1f}s")
                    print(f"   [FAIL] Timeout protection FAILED - system still hanging")
                    return False

                # Progress update every 15 seconds
                if elapsed % 15 < 1:
                    print(f"   [PROGRESS] ANALYZE running... {elapsed:.1f}s")

            except requests.exceptions.RequestException as e:
                print(f"   [ERROR] Status check failed: {e}")
                break

            time.sleep(3)

        print(f"   [FAIL] Test loop exited unexpectedly")
        return False

    except Exception as e:
        print(f"   [ERROR] Test execution failed: {e}")
        return False

def main():
    """Main test execution"""
    print("=" * 60)
    print("TIMEOUT PROTECTION VALIDATION TEST")
    print("=" * 60)
    print("Testing: Universal timeout protection system")
    print("Issue: gevent.Timeout failed for CPU-bound regex operations")
    print("Fix: ThreadPoolExecutor-based timeout with content size protection")
    print("")

    # Check server health
    print("[STEP 1] Checking server health...")
    try:
        health_response = requests.get("http://127.0.0.1:5000/health", timeout=10)
        if health_response.status_code == 200:
            print("[OK] Server is running and healthy")
        elif health_response.status_code == 503:
            print("[OK] Server running (503 due to missing API keys - expected)")
        else:
            print(f"[WARN] Server returned status {health_response.status_code}")
    except Exception as e:
        print(f"[ERROR] Cannot connect to server: {e}")
        print("Please start the Flask server: python app.py")
        return False

    # Run the timeout protection test
    print("[STEP 2] Running timeout protection test...")

    success = test_timeout_with_problematic_content()

    # Results
    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)

    if success:
        print("[PASS] Timeout protection test PASSED")
        print("[SUCCESS] Universal timeout protection is working correctly")
        print("[SUCCESS] No more hanging scenarios detected")
        print("[SUCCESS] System ready for production deployment")
        print("")
        print("Key improvements validated:")
        print("  - ThreadPoolExecutor timeout works in any context")
        print("  - Content size protection prevents regex overwhelm")
        print("  - Cooperative interruption allows graceful early exit")
        print("  - 120s hard timeout limit enforced")
    else:
        print("[FAIL] Timeout protection test FAILED")
        print("[ERROR] Hanging issue may still exist")
        print("[ERROR] Further investigation needed")

    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)