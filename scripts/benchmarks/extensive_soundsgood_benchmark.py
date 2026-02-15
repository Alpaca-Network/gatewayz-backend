#!/usr/bin/env python3
"""
Extensive benchmark for Soundsgood GLM-4.5-Air provider.

This script runs a comprehensive 20-minute benchmark covering:
- Code generation (multiple languages, complexity levels)
- Debugging and error analysis
- Algorithm design and explanation
- Refactoring and code review
- Reasoning and problem-solving
- Documentation generation
- API design
- Testing strategies

Metrics tracked:
- Latency (TTFB, TTFC, total time)
- Token usage (input, output, reasoning)
- Cost
- Response quality indicators
- Throughput (tokens per second)
"""

import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import httpx


@dataclass
class BenchmarkResult:
    """Result from a single benchmark test."""

    category: str
    test_name: str
    prompt: str

    # Response
    content: str
    reasoning: str | None
    content_length: int

    # Timing
    ttfb_seconds: float
    ttfc_seconds: float | None
    total_duration_seconds: float

    # Tokens
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int

    # Derived metrics
    tokens_per_second: float

    # Cost
    cost_usd: float

    # Status
    success: bool
    error: str | None = None

    # Iteration info
    iteration: int = 1


@dataclass
class CategoryStats:
    """Aggregated statistics for a category."""

    category: str
    test_count: int
    success_count: int
    failure_count: int

    # Latency stats
    ttfb_mean: float
    ttfb_median: float
    ttfb_p95: float
    ttfb_min: float
    ttfb_max: float

    total_time_mean: float
    total_time_median: float
    total_time_p95: float

    # Token stats
    output_tokens_mean: float
    reasoning_tokens_mean: float
    tokens_per_second_mean: float

    # Cost
    total_cost: float
    avg_cost_per_request: float


@dataclass
class BenchmarkSummary:
    """Overall benchmark summary."""

    run_id: str
    started_at: str
    completed_at: str
    total_duration_minutes: float

    total_tests: int
    successful_tests: int
    failed_tests: int
    success_rate: float

    # Overall latency
    overall_ttfb_mean: float
    overall_ttfb_p95: float
    overall_total_time_mean: float

    # Overall throughput
    overall_tokens_per_second: float

    # Cost
    total_cost: float

    # Per-category stats
    category_stats: dict[str, CategoryStats]

    # All results
    results: list[BenchmarkResult]


# Comprehensive test prompts organized by category
BENCHMARK_PROMPTS = {
    "code_generation": [
        {
            "name": "Python: Binary Search Tree",
            "prompt": "Implement a complete Binary Search Tree class in Python with insert, delete, search, and in-order traversal methods. Include type hints and docstrings.",
            "max_tokens": 2000,
        },
        {
            "name": "Python: LRU Cache",
            "prompt": "Implement an LRU (Least Recently Used) Cache in Python using OrderedDict. Support get and put operations with O(1) time complexity. Include capacity limit.",
            "max_tokens": 1500,
        },
        {
            "name": "Python: Async Web Scraper",
            "prompt": "Write an async web scraper in Python using aiohttp that can fetch multiple URLs concurrently, extract titles, and handle errors gracefully.",
            "max_tokens": 1500,
        },
        {
            "name": "JavaScript: Promise Pool",
            "prompt": "Implement a Promise pool in JavaScript that limits concurrent promise execution. Given an array of async functions and a concurrency limit, execute them respecting the limit.",
            "max_tokens": 1200,
        },
        {
            "name": "Python: Rate Limiter",
            "prompt": "Implement a token bucket rate limiter in Python that supports configurable rate and burst size. Make it thread-safe.",
            "max_tokens": 1200,
        },
        {
            "name": "SQL: Analytics Query",
            "prompt": "Write a SQL query to find the top 5 customers by total order value in the last 30 days, including their order count, average order value, and most purchased category. Use CTEs for clarity.",
            "max_tokens": 800,
        },
        {
            "name": "Python: Decorator with Args",
            "prompt": "Create a Python decorator that retries a function up to N times with exponential backoff. Support configurable max retries, base delay, and exception types to catch.",
            "max_tokens": 1000,
        },
        {
            "name": "TypeScript: Generic Repository",
            "prompt": "Implement a generic repository pattern in TypeScript with CRUD operations, pagination support, and filtering. Use proper TypeScript generics.",
            "max_tokens": 1500,
        },
        {
            "name": "Python: Event Emitter",
            "prompt": "Implement an event emitter class in Python supporting on, off, once, and emit methods. Handle multiple listeners and support async handlers.",
            "max_tokens": 1200,
        },
        {
            "name": "Go: Worker Pool",
            "prompt": "Implement a worker pool in Go using goroutines and channels. Support dynamic job submission, graceful shutdown, and configurable worker count.",
            "max_tokens": 1200,
        },
    ],
    "debugging": [
        {
            "name": "Memory Leak Analysis",
            "prompt": """Debug this Python code that causes a memory leak:
```python
class EventHandler:
    handlers = []

    def __init__(self, callback):
        self.callback = callback
        EventHandler.handlers.append(self)

    def handle(self, event):
        self.callback(event)

def process_events():
    for i in range(10000):
        handler = EventHandler(lambda e: print(e))
        handler.handle(f"event_{i}")
```
Explain the memory leak and provide a fixed version.""",
            "max_tokens": 1200,
        },
        {
            "name": "Race Condition",
            "prompt": """Debug this Python code with a race condition:
```python
import threading

counter = 0

def increment():
    global counter
    for _ in range(100000):
        counter += 1

threads = [threading.Thread(target=increment) for _ in range(4)]
for t in threads: t.start()
for t in threads: t.join()
print(counter)  # Expected: 400000, but gets random values
```
Explain the race condition and provide thread-safe solutions.""",
            "max_tokens": 1200,
        },
        {
            "name": "Async Deadlock",
            "prompt": """Debug this async Python code that causes a deadlock:
```python
import asyncio

lock1 = asyncio.Lock()
lock2 = asyncio.Lock()

async def task1():
    async with lock1:
        await asyncio.sleep(0.1)
        async with lock2:
            print("Task 1 done")

async def task2():
    async with lock2:
        await asyncio.sleep(0.1)
        async with lock1:
            print("Task 2 done")

asyncio.run(asyncio.gather(task1(), task2()))
```
Explain the deadlock and provide solutions.""",
            "max_tokens": 1200,
        },
        {
            "name": "SQL Injection",
            "prompt": """Debug this Python code vulnerable to SQL injection:
```python
def get_user(username):
    query = f"SELECT * FROM users WHERE username = '{username}'"
    cursor.execute(query)
    return cursor.fetchone()
```
Explain the vulnerability with attack examples and provide secure solutions.""",
            "max_tokens": 1000,
        },
        {
            "name": "Floating Point Error",
            "prompt": """Debug this financial calculation code:
```python
def calculate_total(items):
    total = 0.0
    for item in items:
        total += item['price'] * item['quantity']
    return total

# Test case that fails
items = [{'price': 0.1, 'quantity': 3}]
result = calculate_total(items)
print(result == 0.3)  # Returns False!
```
Explain why this fails and provide a correct implementation for financial calculations.""",
            "max_tokens": 1000,
        },
        {
            "name": "Closure Bug",
            "prompt": """Debug this JavaScript closure bug:
```javascript
for (var i = 0; i < 5; i++) {
    setTimeout(function() {
        console.log(i);
    }, 1000);
}
// Expected: 0, 1, 2, 3, 4
// Actual: 5, 5, 5, 5, 5
```
Explain why this happens and provide multiple solutions.""",
            "max_tokens": 1000,
        },
        {
            "name": "Python Import Cycle",
            "prompt": """Debug this Python import cycle:
```python
# module_a.py
from module_b import B
class A:
    def __init__(self):
        self.b = B()

# module_b.py
from module_a import A
class B:
    def __init__(self):
        self.a = A()
```
Explain why this fails and provide solutions to break the cycle.""",
            "max_tokens": 1000,
        },
        {
            "name": "Off-by-One Error",
            "prompt": """Debug this binary search implementation:
```python
def binary_search(arr, target):
    left, right = 0, len(arr)
    while left < right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid
        else:
            right = mid
    return -1
```
Find all bugs and provide a correct implementation.""",
            "max_tokens": 1000,
        },
    ],
    "algorithms": [
        {
            "name": "Dynamic Programming",
            "prompt": "Explain the Longest Common Subsequence (LCS) problem. Provide both recursive with memoization and bottom-up DP solutions in Python with time/space complexity analysis.",
            "max_tokens": 1500,
        },
        {
            "name": "Graph: Dijkstra",
            "prompt": "Implement Dijkstra's shortest path algorithm in Python. Include both the algorithm and a priority queue-based optimization. Explain time complexity.",
            "max_tokens": 1500,
        },
        {
            "name": "Sorting: Merge Sort",
            "prompt": "Implement merge sort in Python with detailed explanation. Include both recursive and iterative versions. Analyze time and space complexity.",
            "max_tokens": 1200,
        },
        {
            "name": "Tree: Balanced BST",
            "prompt": "Explain AVL tree rotations. Implement the rotation functions (left, right, left-right, right-left) in Python with visualization examples.",
            "max_tokens": 1500,
        },
        {
            "name": "String: KMP Algorithm",
            "prompt": "Explain and implement the KMP (Knuth-Morris-Pratt) string matching algorithm in Python. Include the failure function construction.",
            "max_tokens": 1200,
        },
        {
            "name": "Backtracking: N-Queens",
            "prompt": "Solve the N-Queens problem using backtracking in Python. Explain the approach and provide visualization of solutions for N=8.",
            "max_tokens": 1500,
        },
        {
            "name": "Graph: Topological Sort",
            "prompt": "Implement topological sort using both DFS and Kahn's algorithm (BFS) in Python. Explain when to use each approach.",
            "max_tokens": 1200,
        },
        {
            "name": "Trie Implementation",
            "prompt": "Implement a Trie data structure in Python with insert, search, and startsWith methods. Add autocomplete functionality.",
            "max_tokens": 1200,
        },
    ],
    "refactoring": [
        {
            "name": "Extract Method",
            "prompt": """Refactor this messy function using Extract Method pattern:
```python
def process_order(order):
    # Validate order
    if not order.get('items'):
        raise ValueError("Order must have items")
    if not order.get('customer_id'):
        raise ValueError("Order must have customer")
    total = 0
    for item in order['items']:
        if item['quantity'] < 1:
            raise ValueError("Invalid quantity")
        total += item['price'] * item['quantity']
    # Apply discounts
    if total > 100:
        total *= 0.9
    elif total > 50:
        total *= 0.95
    # Calculate tax
    tax = total * 0.08
    total += tax
    # Create receipt
    receipt = f"Customer: {order['customer_id']}\\n"
    receipt += f"Items: {len(order['items'])}\\n"
    receipt += f"Subtotal: ${total - tax:.2f}\\n"
    receipt += f"Tax: ${tax:.2f}\\n"
    receipt += f"Total: ${total:.2f}"
    return {'total': total, 'tax': tax, 'receipt': receipt}
```
Apply clean code principles and SOLID.""",
            "max_tokens": 1500,
        },
        {
            "name": "Strategy Pattern",
            "prompt": """Refactor this code to use the Strategy pattern:
```python
def calculate_shipping(weight, destination, method):
    if method == 'standard':
        if destination == 'domestic':
            return weight * 0.5
        else:
            return weight * 2.0
    elif method == 'express':
        if destination == 'domestic':
            return weight * 1.5
        else:
            return weight * 4.0
    elif method == 'overnight':
        if destination == 'domestic':
            return weight * 3.0
        else:
            return weight * 8.0
```
Make it extensible for new shipping methods.""",
            "max_tokens": 1200,
        },
        {
            "name": "Remove God Class",
            "prompt": """Refactor this God class into smaller, focused classes:
```python
class UserManager:
    def __init__(self, db):
        self.db = db
        self.email_service = EmailService()
        self.cache = {}

    def create_user(self, data): ...
    def update_user(self, id, data): ...
    def delete_user(self, id): ...
    def get_user(self, id): ...
    def authenticate(self, email, password): ...
    def reset_password(self, email): ...
    def send_welcome_email(self, user): ...
    def send_password_reset(self, user): ...
    def validate_email(self, email): ...
    def hash_password(self, password): ...
    def verify_password(self, password, hash): ...
    def generate_token(self, user): ...
    def verify_token(self, token): ...
    def cache_user(self, user): ...
    def invalidate_cache(self, user_id): ...
```
Apply Single Responsibility Principle.""",
            "max_tokens": 1500,
        },
        {
            "name": "Simplify Conditionals",
            "prompt": """Refactor these nested conditionals:
```python
def get_user_status(user):
    if user is not None:
        if user.is_active:
            if user.subscription:
                if user.subscription.is_valid():
                    if user.subscription.type == 'premium':
                        return 'premium_active'
                    else:
                        return 'basic_active'
                else:
                    return 'subscription_expired'
            else:
                return 'no_subscription'
        else:
            return 'inactive'
    else:
        return 'not_found'
```
Use guard clauses and early returns.""",
            "max_tokens": 1000,
        },
        {
            "name": "Replace Temp with Query",
            "prompt": """Refactor this code to eliminate temporary variables:
```python
def calculate_price(order):
    base_price = order.quantity * order.item_price
    quantity_discount = max(0, order.quantity - 100) * order.item_price * 0.05
    shipping = min(base_price * 0.1, 100)
    price_before_tax = base_price - quantity_discount + shipping
    tax = price_before_tax * 0.08
    final_price = price_before_tax + tax
    return final_price
```
Convert temps to methods for better encapsulation.""",
            "max_tokens": 1000,
        },
    ],
    "reasoning": [
        {
            "name": "System Design: URL Shortener",
            "prompt": "Design a URL shortening service like bit.ly. Cover: requirements, API design, database schema, encoding algorithm, scalability, caching strategy, and analytics.",
            "max_tokens": 2000,
        },
        {
            "name": "System Design: Rate Limiter",
            "prompt": "Design a distributed rate limiter for an API gateway. Compare token bucket, leaky bucket, sliding window, and fixed window algorithms. Discuss Redis-based implementation.",
            "max_tokens": 1800,
        },
        {
            "name": "Trade-offs: SQL vs NoSQL",
            "prompt": "Compare SQL and NoSQL databases for an e-commerce platform. Analyze: product catalog, user sessions, order history, search, and analytics. Recommend which to use where.",
            "max_tokens": 1500,
        },
        {
            "name": "Architecture: Microservices",
            "prompt": "You have a monolithic e-commerce app. Plan migration to microservices. Identify service boundaries, data ownership, communication patterns, and migration strategy.",
            "max_tokens": 1800,
        },
        {
            "name": "Problem Solving: Load Balancing",
            "prompt": "A service is experiencing uneven load distribution across servers. Some servers are at 90% CPU while others are at 20%. Diagnose possible causes and propose solutions.",
            "max_tokens": 1500,
        },
        {
            "name": "Security Analysis",
            "prompt": "Analyze the security of a REST API that uses JWT tokens stored in localStorage, with CORS allowing all origins, and passwords hashed with MD5. Identify vulnerabilities and fixes.",
            "max_tokens": 1500,
        },
        {
            "name": "Performance Optimization",
            "prompt": "A Python web API has 2-second average response time. The database query takes 50ms. What could cause the remaining 1950ms? Provide a systematic debugging approach.",
            "max_tokens": 1500,
        },
        {
            "name": "Scaling Strategy",
            "prompt": "Your application handles 1000 requests/second. You need to scale to 100,000 requests/second. Outline a scaling strategy covering: caching, database, compute, CDN, and architecture changes.",
            "max_tokens": 1800,
        },
    ],
    "documentation": [
        {
            "name": "API Documentation",
            "prompt": "Write comprehensive API documentation for a user authentication endpoint that supports: registration, login, password reset, and token refresh. Include request/response examples, error codes, and rate limits.",
            "max_tokens": 1500,
        },
        {
            "name": "README Template",
            "prompt": "Create a professional README.md template for an open-source Python library. Include: badges, installation, quick start, configuration, API reference, contributing guidelines, and changelog format.",
            "max_tokens": 1500,
        },
        {
            "name": "Architecture Decision Record",
            "prompt": "Write an ADR (Architecture Decision Record) for choosing PostgreSQL over MongoDB for a new fintech application. Include context, decision, consequences, and alternatives considered.",
            "max_tokens": 1200,
        },
        {
            "name": "Runbook",
            "prompt": "Create an incident response runbook for a database connection pool exhaustion issue. Include: detection, immediate response, root cause investigation, resolution steps, and prevention.",
            "max_tokens": 1200,
        },
    ],
    "testing": [
        {
            "name": "Unit Tests",
            "prompt": """Write comprehensive unit tests for this function:
```python
def parse_email(email: str) -> dict:
    if not email or '@' not in email:
        raise ValueError("Invalid email format")
    local, domain = email.rsplit('@', 1)
    if not local or not domain:
        raise ValueError("Invalid email format")
    if '.' not in domain:
        raise ValueError("Invalid domain")
    return {
        'local': local,
        'domain': domain,
        'is_subdomain': domain.count('.') > 1
    }
```
Use pytest and cover edge cases, error conditions, and boundary values.""",
            "max_tokens": 1500,
        },
        {
            "name": "Integration Tests",
            "prompt": "Write integration tests for a REST API user registration flow. Test: successful registration, duplicate email, invalid data, database rollback on failure, and email sending. Use pytest with fixtures.",
            "max_tokens": 1500,
        },
        {
            "name": "Test Strategy",
            "prompt": "Design a comprehensive test strategy for a payment processing microservice. Cover: unit tests, integration tests, contract tests, E2E tests, performance tests, and chaos engineering.",
            "max_tokens": 1500,
        },
        {
            "name": "Mocking Strategy",
            "prompt": """Explain how to properly mock these dependencies for testing:
```python
class OrderService:
    def __init__(self, db, payment_gateway, email_service, inventory_api):
        self.db = db
        self.payment_gateway = payment_gateway
        self.email_service = email_service
        self.inventory_api = inventory_api

    async def place_order(self, order):
        # Check inventory
        # Process payment
        # Save to database
        # Send confirmation email
        pass
```
Show mocking patterns for each dependency type.""",
            "max_tokens": 1500,
        },
    ],
}


async def call_soundsgood(
    prompt: str,
    api_key: str,
    max_tokens: int = 1500,
) -> dict[str, Any]:
    """Call Soundsgood GLM-4.5-Air API."""
    url = "https://soundsgood.one/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "zai-org/GLM-4.5-Air",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    start_time = time.perf_counter()
    ttfb_time = None
    ttfc_time = None

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            content_parts = []
            reasoning_parts = []
            usage_data = {}
            model = ""

            async with client.stream("POST", url, headers=headers, json=payload) as response:
                ttfb_time = time.perf_counter()
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            model = chunk.get("model", model)

                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                if "content" in delta and delta["content"]:
                                    if ttfc_time is None:
                                        ttfc_time = time.perf_counter()
                                    content_parts.append(delta["content"])
                                if "reasoning" in delta and delta["reasoning"]:
                                    reasoning_parts.append(delta["reasoning"])

                            if "usage" in chunk:
                                usage_data = chunk["usage"]
                        except json.JSONDecodeError:
                            continue

            end_time = time.perf_counter()
            content = "".join(content_parts)
            reasoning = "".join(reasoning_parts) if reasoning_parts else None

            output_tokens = usage_data.get("completion_tokens", 0)
            total_duration = end_time - start_time
            tps = output_tokens / total_duration if total_duration > 0 else 0

            return {
                "success": True,
                "content": content,
                "reasoning": reasoning,
                "ttfb_seconds": ttfb_time - start_time if ttfb_time else total_duration,
                "ttfc_seconds": ttfc_time - start_time if ttfc_time else None,
                "total_duration_seconds": total_duration,
                "input_tokens": usage_data.get("prompt_tokens", 0),
                "output_tokens": output_tokens,
                "reasoning_tokens": usage_data.get("reasoning_tokens", 0),
                "total_tokens": usage_data.get("total_tokens", 0),
                "tokens_per_second": tps,
                "cost_usd": usage_data.get("cost_usd_total", 0.0),
                "error": None,
            }

    except Exception as e:
        end_time = time.perf_counter()
        return {
            "success": False,
            "content": "",
            "reasoning": None,
            "ttfb_seconds": end_time - start_time,
            "ttfc_seconds": None,
            "total_duration_seconds": end_time - start_time,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "tokens_per_second": 0,
            "cost_usd": 0.0,
            "error": str(e),
        }


def calculate_percentile(values: list[float], percentile: float) -> float:
    """Calculate percentile of a list of values."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * percentile / 100
    lower = int(index)
    upper = lower + 1
    if upper >= len(sorted_values):
        return sorted_values[-1]
    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def calculate_category_stats(category: str, results: list[BenchmarkResult]) -> CategoryStats:
    """Calculate statistics for a category."""
    successful = [r for r in results if r.success]

    if not successful:
        return CategoryStats(
            category=category,
            test_count=len(results),
            success_count=0,
            failure_count=len(results),
            ttfb_mean=0, ttfb_median=0, ttfb_p95=0, ttfb_min=0, ttfb_max=0,
            total_time_mean=0, total_time_median=0, total_time_p95=0,
            output_tokens_mean=0, reasoning_tokens_mean=0, tokens_per_second_mean=0,
            total_cost=0, avg_cost_per_request=0,
        )

    ttfb_values = [r.ttfb_seconds for r in successful]
    total_time_values = [r.total_duration_seconds for r in successful]

    return CategoryStats(
        category=category,
        test_count=len(results),
        success_count=len(successful),
        failure_count=len(results) - len(successful),
        ttfb_mean=statistics.mean(ttfb_values),
        ttfb_median=statistics.median(ttfb_values),
        ttfb_p95=calculate_percentile(ttfb_values, 95),
        ttfb_min=min(ttfb_values),
        ttfb_max=max(ttfb_values),
        total_time_mean=statistics.mean(total_time_values),
        total_time_median=statistics.median(total_time_values),
        total_time_p95=calculate_percentile(total_time_values, 95),
        output_tokens_mean=statistics.mean([r.output_tokens for r in successful]),
        reasoning_tokens_mean=statistics.mean([r.reasoning_tokens for r in successful]),
        tokens_per_second_mean=statistics.mean([r.tokens_per_second for r in successful]),
        total_cost=sum(r.cost_usd for r in successful),
        avg_cost_per_request=sum(r.cost_usd for r in successful) / len(successful),
    )


async def run_benchmark(
    api_key: str,
    iterations: int = 2,
    delay_between_requests: float = 1.0,
) -> BenchmarkSummary:
    """Run the comprehensive benchmark."""

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    started_at = datetime.now().isoformat()

    print(f"\n{'='*70}")
    print(f"EXTENSIVE SOUNDSGOOD GLM-4.5-Air BENCHMARK")
    print(f"Run ID: {run_id}")
    print(f"Started: {started_at}")
    print(f"Iterations per test: {iterations}")
    print(f"{'='*70}")

    # Count total tests
    total_prompts = sum(len(prompts) for prompts in BENCHMARK_PROMPTS.values())
    total_tests = total_prompts * iterations
    print(f"Total prompts: {total_prompts}")
    print(f"Total tests (with iterations): {total_tests}")
    print(f"Estimated time: ~{total_tests * 10 / 60:.1f} minutes")
    print(f"{'='*70}\n")

    all_results: list[BenchmarkResult] = []
    test_number = 0

    for category, prompts in BENCHMARK_PROMPTS.items():
        print(f"\n{'#'*70}")
        print(f"# CATEGORY: {category.upper()}")
        print(f"# Tests: {len(prompts)} x {iterations} iterations = {len(prompts) * iterations}")
        print(f"{'#'*70}")

        for prompt_data in prompts:
            test_name = prompt_data["name"]
            prompt = prompt_data["prompt"]
            max_tokens = prompt_data.get("max_tokens", 1500)

            for iteration in range(1, iterations + 1):
                test_number += 1
                print(f"\n[{test_number}/{total_tests}] {category}/{test_name} (iter {iteration})")
                print(f"  Prompt: {prompt[:80]}...")

                result = await call_soundsgood(prompt, api_key, max_tokens)

                benchmark_result = BenchmarkResult(
                    category=category,
                    test_name=test_name,
                    prompt=prompt,
                    content=result["content"],
                    reasoning=result["reasoning"],
                    content_length=len(result["content"]),
                    ttfb_seconds=result["ttfb_seconds"],
                    ttfc_seconds=result["ttfc_seconds"],
                    total_duration_seconds=result["total_duration_seconds"],
                    input_tokens=result["input_tokens"],
                    output_tokens=result["output_tokens"],
                    reasoning_tokens=result["reasoning_tokens"],
                    total_tokens=result["total_tokens"],
                    tokens_per_second=result["tokens_per_second"],
                    cost_usd=result["cost_usd"],
                    success=result["success"],
                    error=result["error"],
                    iteration=iteration,
                )

                all_results.append(benchmark_result)

                # Print result summary
                if result["success"]:
                    print(f"  ✓ TTFB: {result['ttfb_seconds']:.2f}s | "
                          f"Total: {result['total_duration_seconds']:.2f}s | "
                          f"Tokens: {result['output_tokens']} out + {result['reasoning_tokens']} reasoning | "
                          f"TPS: {result['tokens_per_second']:.1f} | "
                          f"Cost: ${result['cost_usd']:.4f}")
                else:
                    print(f"  ✗ FAILED: {result['error']}")

                # Delay between requests
                await asyncio.sleep(delay_between_requests)

    completed_at = datetime.now().isoformat()

    # Calculate statistics
    print(f"\n{'='*70}")
    print("CALCULATING STATISTICS...")
    print(f"{'='*70}")

    category_stats = {}
    for category in BENCHMARK_PROMPTS.keys():
        category_results = [r for r in all_results if r.category == category]
        category_stats[category] = calculate_category_stats(category, category_results)

    successful_results = [r for r in all_results if r.success]

    # Calculate overall stats
    if successful_results:
        all_ttfb = [r.ttfb_seconds for r in successful_results]
        all_total_time = [r.total_duration_seconds for r in successful_results]
        all_tps = [r.tokens_per_second for r in successful_results]

        overall_ttfb_mean = statistics.mean(all_ttfb)
        overall_ttfb_p95 = calculate_percentile(all_ttfb, 95)
        overall_total_time_mean = statistics.mean(all_total_time)
        overall_tps = statistics.mean(all_tps)
        total_cost = sum(r.cost_usd for r in successful_results)
    else:
        overall_ttfb_mean = 0
        overall_ttfb_p95 = 0
        overall_total_time_mean = 0
        overall_tps = 0
        total_cost = 0

    # Calculate duration
    start_dt = datetime.fromisoformat(started_at)
    end_dt = datetime.fromisoformat(completed_at)
    total_duration_minutes = (end_dt - start_dt).total_seconds() / 60

    summary = BenchmarkSummary(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        total_duration_minutes=total_duration_minutes,
        total_tests=len(all_results),
        successful_tests=len(successful_results),
        failed_tests=len(all_results) - len(successful_results),
        success_rate=len(successful_results) / len(all_results) * 100 if all_results else 0,
        overall_ttfb_mean=overall_ttfb_mean,
        overall_ttfb_p95=overall_ttfb_p95,
        overall_total_time_mean=overall_total_time_mean,
        overall_tokens_per_second=overall_tps,
        total_cost=total_cost,
        category_stats=category_stats,
        results=all_results,
    )

    return summary


def print_summary(summary: BenchmarkSummary):
    """Print benchmark summary."""
    print(f"\n{'='*70}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*70}")
    print(f"Run ID: {summary.run_id}")
    print(f"Duration: {summary.total_duration_minutes:.1f} minutes")
    print(f"Total Tests: {summary.total_tests}")
    print(f"Successful: {summary.successful_tests} ({summary.success_rate:.1f}%)")
    print(f"Failed: {summary.failed_tests}")
    print(f"\n{'='*70}")
    print("OVERALL LATENCY")
    print(f"{'='*70}")
    print(f"TTFB Mean: {summary.overall_ttfb_mean:.3f}s")
    print(f"TTFB P95: {summary.overall_ttfb_p95:.3f}s")
    print(f"Total Time Mean: {summary.overall_total_time_mean:.3f}s")
    print(f"Tokens/Second: {summary.overall_tokens_per_second:.1f}")
    print(f"\n{'='*70}")
    print("COST")
    print(f"{'='*70}")
    print(f"Total Cost: ${summary.total_cost:.4f}")
    print(f"Avg Cost/Request: ${summary.total_cost / summary.successful_tests:.6f}" if summary.successful_tests else "N/A")

    print(f"\n{'='*70}")
    print("PER-CATEGORY BREAKDOWN")
    print(f"{'='*70}")

    for category, stats in summary.category_stats.items():
        print(f"\n{category.upper()}")
        print(f"  Tests: {stats.success_count}/{stats.test_count} successful")
        print(f"  TTFB: mean={stats.ttfb_mean:.3f}s, median={stats.ttfb_median:.3f}s, p95={stats.ttfb_p95:.3f}s")
        print(f"  Total Time: mean={stats.total_time_mean:.3f}s, p95={stats.total_time_p95:.3f}s")
        print(f"  Tokens: output={stats.output_tokens_mean:.0f}, reasoning={stats.reasoning_tokens_mean:.0f}")
        print(f"  TPS: {stats.tokens_per_second_mean:.1f}")
        print(f"  Cost: ${stats.total_cost:.4f} total, ${stats.avg_cost_per_request:.6f} avg")


def save_results(summary: BenchmarkSummary, output_dir: str = "benchmark_results"):
    """Save benchmark results to files."""
    import os

    os.makedirs(output_dir, exist_ok=True)

    # Save summary
    summary_file = f"{output_dir}/soundsgood_extensive_{summary.run_id}_summary.json"
    summary_dict = {
        "run_id": summary.run_id,
        "started_at": summary.started_at,
        "completed_at": summary.completed_at,
        "total_duration_minutes": summary.total_duration_minutes,
        "total_tests": summary.total_tests,
        "successful_tests": summary.successful_tests,
        "failed_tests": summary.failed_tests,
        "success_rate": summary.success_rate,
        "overall_ttfb_mean": summary.overall_ttfb_mean,
        "overall_ttfb_p95": summary.overall_ttfb_p95,
        "overall_total_time_mean": summary.overall_total_time_mean,
        "overall_tokens_per_second": summary.overall_tokens_per_second,
        "total_cost": summary.total_cost,
        "category_stats": {k: asdict(v) for k, v in summary.category_stats.items()},
    }

    with open(summary_file, "w") as f:
        json.dump(summary_dict, f, indent=2)
    print(f"\nSummary saved to: {summary_file}")

    # Save detailed results
    results_file = f"{output_dir}/soundsgood_extensive_{summary.run_id}_detailed.json"
    results_list = [asdict(r) for r in summary.results]

    with open(results_file, "w") as f:
        json.dump(results_list, f, indent=2, default=str)
    print(f"Detailed results saved to: {results_file}")


async def main():
    """Main entry point."""
    API_KEY = "sg_2e430d39e6d2f1ecbc898e4d136dc31d133e7e577b68c9d4e82f5e314c852639"

    # Run with 2 iterations per test, ~1s delay between requests
    # This should take approximately 20 minutes
    summary = await run_benchmark(
        api_key=API_KEY,
        iterations=2,  # 2 iterations per test
        delay_between_requests=1.0,  # 1 second between requests
    )

    print_summary(summary)
    save_results(summary)


if __name__ == "__main__":
    asyncio.run(main())
