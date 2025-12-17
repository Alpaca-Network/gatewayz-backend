# GPT-4 Multi-Provider Latency Benchmarking Guide

## Overview

This guide explains how to benchmark GPT-4 models across different providers by measuring Time to First Chunk (TTFC), tokens per second, and total latency using streaming chat completions.

---

## How Latency Data Works in Streaming Requests

When you make a streaming chat completion request, the Gateway sends latency metrics as a special Server-Sent Event (SSE) **before** the final `[DONE]` event.

### Streaming Response Format

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk",...}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk",...}

data: {"type":"timing","timing":{"ttfc_ms":125.3,"total_ms":1450.2,"streaming_ms":1324.9,"tokens_per_second":45.2,"input_tokens":50,"output_tokens":60,"total_tokens":110,"server_received_at":1702934567890,"server_responded_at":1702934569140}}

data: [DONE]
```

### Timing Data Fields

| Field | Description |
|-------|-------------|
| `ttfc_ms` | Time to first chunk in milliseconds (from server's perspective) |
| `total_ms` | Total server processing time in milliseconds |
| `streaming_ms` | Time spent streaming tokens (total_ms - ttfc_ms) |
| `tokens_per_second` | Output generation speed (output_tokens / streaming_ms) |
| `input_tokens` | Number of tokens in the prompt |
| `output_tokens` | Number of tokens generated |
| `total_tokens` | Total tokens used (input + output) |
| `server_received_at` | Unix timestamp (ms) when server received request |
| `server_responded_at` | Unix timestamp (ms) when server finished processing |

### Implementation Location

The timing data is calculated and sent in:
- **File**: `src/routes/chat.py`
- **Function**: `stream_generator()`
- **Lines**: 710-824

Key implementation details:
- TTFC is measured when the first chunk arrives (line 712)
- Timing data is sent as a JSON SSE event before `[DONE]` (line 824)
- Background processing happens after stream completes to avoid blocking

---

## GPT-4 Models by Provider

Based on the Gateway's configuration, here are the available GPT-4 models:

### OpenAI (Native)
- `gpt-4` - Base GPT-4 model (8K context)
- `gpt-4-32k` - Extended context version
- `gpt-4-turbo` - Faster, cheaper GPT-4
- `gpt-4o` - Optimized GPT-4 variant

### OpenRouter (Proxy)
- `openai/gpt-4` - GPT-4 via OpenRouter
- `openai/gpt-4-32k` - GPT-4 32K via OpenRouter
- `openai/gpt-4-turbo` - GPT-4 Turbo via OpenRouter
- `openai/gpt-4o` - GPT-4o via OpenRouter

### AIHubMix
- `gpt-4o` - GPT-4o via AIHubMix

### Azure OpenAI
- `gpt-4` - Azure-hosted GPT-4 (if configured)

### Other Providers
Check with Together AI, Vercel AI Gateway, or other providers in your deployment for additional GPT-4 access.

---

## JavaScript Benchmarking Code

### Complete Benchmark Script

```javascript
/**
 * GPT-4 Multi-Provider Latency Benchmark
 *
 * Tests TTFC, tokens/sec, and total latency across different providers
 * with input prompts of 10, 100, and 500 tokens.
 */

// Configuration
const API_URL = 'http://localhost:8000/v1/chat/completions';
const API_KEY = 'your-api-key-here';

// GPT-4 models to test from different providers
const GPT4_MODELS = [
  // OpenAI direct
  { provider: 'OpenAI', model: 'gpt-4' },
  { provider: 'OpenAI', model: 'gpt-4-turbo' },
  { provider: 'OpenAI', model: 'gpt-4o' },

  // OpenRouter (proxy)
  { provider: 'OpenRouter', model: 'openai/gpt-4' },
  { provider: 'OpenRouter', model: 'openai/gpt-4-turbo' },
  { provider: 'OpenRouter', model: 'openai/gpt-4o' },

  // AIHubMix
  { provider: 'AIHubMix', model: 'gpt-4o' },
];

// Test prompts with different token counts (approximate)
const TEST_PROMPTS = {
  '10_tokens': 'Hello, how are you doing today?',

  '100_tokens': 'Write a detailed explanation of how neural networks work. ' +
                'Include information about layers, neurons, activation functions, ' +
                'backpropagation, and gradient descent. Make it accessible to beginners ' +
                'while maintaining technical accuracy in your description.',

  '500_tokens': 'Provide a comprehensive analysis of the history of artificial intelligence, ' +
                'starting from the Dartmouth Conference in 1956 through to modern deep learning. ' +
                'Cover key milestones including expert systems in the 1980s, the AI winter periods, ' +
                'the resurgence with machine learning in the 2000s, and the deep learning revolution ' +
                'that began around 2012. Discuss important breakthroughs like AlexNet, ResNet, ' +
                'Transformers, BERT, GPT models, and diffusion models. Include the impact of ' +
                'increased computational power, larger datasets, and algorithmic improvements. ' +
                'Analyze how AI has evolved from narrow task-specific systems to more general ' +
                'purpose models. Discuss current trends like large language models, multimodal AI, ' +
                'and emerging capabilities. Also address ethical considerations, bias concerns, ' +
                'and the societal implications of increasingly powerful AI systems. Finally, ' +
                'speculate on future directions including AGI research, AI safety, alignment problems, ' +
                'and potential regulatory frameworks being developed globally.'
};

/**
 * Parse SSE stream and extract timing data
 */
async function parseSSEStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let buffer = '';
  let firstChunkTime = null;
  let timingData = null;
  let chunks = [];
  const startTime = performance.now();

  try {
    while (true) {
      const { done, value } = await reader.read();

      if (done) break;

      // Record time to first chunk
      if (!firstChunkTime) {
        firstChunkTime = performance.now();
      }

      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE events (separated by \n\n)
      const events = buffer.split('\n\n');
      buffer = events.pop(); // Keep incomplete event in buffer

      for (const event of events) {
        if (!event.trim()) continue;

        // Parse SSE event
        const lines = event.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);

            // Check for [DONE]
            if (data === '[DONE]') {
              continue;
            }

            try {
              const parsed = JSON.parse(data);

              // Check if this is timing data
              if (parsed.type === 'timing' && parsed.timing) {
                timingData = parsed.timing;
              } else {
                chunks.push(parsed);
              }
            } catch (e) {
              // Not JSON, skip
            }
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }

  const endTime = performance.now();
  const clientTotalLatency = endTime - startTime;
  const clientTTFC = firstChunkTime ? (firstChunkTime - startTime) : null;

  return {
    timingData,
    clientTotalLatency,
    clientTTFC,
    chunks
  };
}

/**
 * Run a single benchmark test
 */
async function benchmarkModel(modelConfig, prompt, promptSize) {
  const { provider, model } = modelConfig;

  console.log(`  Testing ${provider}/${model} with ${promptSize} prompt...`);

  try {
    const response = await fetch(API_URL, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        model: model,
        messages: [
          { role: 'user', content: prompt }
        ],
        stream: true
      })
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    const { timingData, clientTotalLatency, clientTTFC, chunks } = await parseSSEStream(response);

    // Calculate metrics
    const result = {
      provider,
      model,
      promptSize,
      success: true,

      // Server-reported metrics
      serverTTFC: timingData?.ttfc_ms || null,
      serverTotalLatency: timingData?.total_ms || null,
      serverStreamingTime: timingData?.streaming_ms || null,
      serverTokensPerSecond: timingData?.tokens_per_second || null,

      // Client-measured metrics
      clientTTFC,
      clientTotalLatency,

      // Token usage
      inputTokens: timingData?.input_tokens || null,
      outputTokens: timingData?.output_tokens || null,
      totalTokens: timingData?.total_tokens || null,

      // Metadata
      chunkCount: chunks.length,
      timestamp: new Date().toISOString()
    };

    return result;

  } catch (error) {
    console.error(`    ❌ Error: ${error.message}`);

    return {
      provider,
      model,
      promptSize,
      success: false,
      error: error.message,
      timestamp: new Date().toISOString()
    };
  }
}

/**
 * Run all benchmarks
 */
async function runAllBenchmarks() {
  const results = [];

  console.log('🚀 Starting GPT-4 Multi-Provider Latency Benchmark\n');
  console.log(`Testing ${GPT4_MODELS.length} models with ${Object.keys(TEST_PROMPTS).length} prompt sizes\n`);

  // Test each model with each prompt size
  for (const modelConfig of GPT4_MODELS) {
    console.log(`\n📊 Testing ${modelConfig.provider}/${modelConfig.model}:`);

    for (const [promptSize, prompt] of Object.entries(TEST_PROMPTS)) {
      const result = await benchmarkModel(modelConfig, prompt, promptSize);
      results.push(result);

      if (result.success) {
        console.log(`    ✅ ${promptSize}:`);
        console.log(`       Server TTFC: ${result.serverTTFC?.toFixed(1)}ms`);
        console.log(`       Client TTFC: ${result.clientTTFC?.toFixed(1)}ms`);
        console.log(`       Total Latency: ${result.clientTotalLatency?.toFixed(1)}ms`);
        console.log(`       Tokens/sec: ${result.serverTokensPerSecond?.toFixed(1)}`);
        console.log(`       Output Tokens: ${result.outputTokens}`);
      }

      // Small delay between requests to avoid rate limiting
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }

  return results;
}

/**
 * Generate comparison report
 */
function generateReport(results) {
  console.log('\n\n' + '='.repeat(80));
  console.log('📈 BENCHMARK RESULTS SUMMARY');
  console.log('='.repeat(80) + '\n');

  // Group by prompt size
  const byPromptSize = {};
  for (const result of results) {
    if (!result.success) continue;

    if (!byPromptSize[result.promptSize]) {
      byPromptSize[result.promptSize] = [];
    }
    byPromptSize[result.promptSize].push(result);
  }

  // Compare by prompt size
  for (const [promptSize, promptResults] of Object.entries(byPromptSize)) {
    console.log(`\n📝 ${promptSize.toUpperCase().replace('_', ' ')}:\n`);

    // Sort by TTFC (fastest first)
    const sortedByTTFC = [...promptResults].sort((a, b) =>
      (a.serverTTFC || Infinity) - (b.serverTTFC || Infinity)
    );

    console.log('  🏃 Fastest TTFC (Time to First Chunk):');
    sortedByTTFC.slice(0, 3).forEach((r, i) => {
      console.log(`    ${i + 1}. ${r.provider}/${r.model}: ${r.serverTTFC?.toFixed(1)}ms`);
    });

    // Sort by tokens/sec (fastest first)
    const sortedBySpeed = [...promptResults].sort((a, b) =>
      (b.serverTokensPerSecond || 0) - (a.serverTokensPerSecond || 0)
    );

    console.log('\n  ⚡ Fastest Token Generation (tokens/sec):');
    sortedBySpeed.slice(0, 3).forEach((r, i) => {
      console.log(`    ${i + 1}. ${r.provider}/${r.model}: ${r.serverTokensPerSecond?.toFixed(1)} tokens/sec`);
    });

    // Sort by total latency (fastest first)
    const sortedByTotal = [...promptResults].sort((a, b) =>
      (a.clientTotalLatency || Infinity) - (b.clientTotalLatency || Infinity)
    );

    console.log('\n  🎯 Lowest Total Latency:');
    sortedByTotal.slice(0, 3).forEach((r, i) => {
      console.log(`    ${i + 1}. ${r.provider}/${r.model}: ${r.clientTotalLatency?.toFixed(1)}ms`);
    });
  }

  // Overall statistics
  console.log('\n\n' + '='.repeat(80));
  console.log('📊 OVERALL STATISTICS');
  console.log('='.repeat(80) + '\n');

  const successful = results.filter(r => r.success);
  const failed = results.filter(r => !r.success);

  console.log(`Total Tests: ${results.length}`);
  console.log(`Successful: ${successful.length} ✅`);
  console.log(`Failed: ${failed.length} ❌`);

  if (failed.length > 0) {
    console.log('\n❌ Failed Tests:');
    failed.forEach(f => {
      console.log(`  - ${f.provider}/${f.model} (${f.promptSize}): ${f.error}`);
    });
  }

  // Average metrics across all tests
  if (successful.length > 0) {
    const avgTTFC = successful.reduce((sum, r) => sum + (r.serverTTFC || 0), 0) / successful.length;
    const avgTokensPerSec = successful.reduce((sum, r) => sum + (r.serverTokensPerSecond || 0), 0) / successful.length;
    const avgTotalLatency = successful.reduce((sum, r) => sum + (r.clientTotalLatency || 0), 0) / successful.length;

    console.log('\n📊 Average Metrics (all successful tests):');
    console.log(`  Average TTFC: ${avgTTFC.toFixed(1)}ms`);
    console.log(`  Average Tokens/sec: ${avgTokensPerSec.toFixed(1)}`);
    console.log(`  Average Total Latency: ${avgTotalLatency.toFixed(1)}ms`);
  }

  // Export results to JSON
  console.log('\n\n💾 Saving detailed results to benchmark_results.json...');

  return {
    summary: {
      totalTests: results.length,
      successful: successful.length,
      failed: failed.length,
      timestamp: new Date().toISOString()
    },
    results: results
  };
}

/**
 * Save results to file (Node.js only)
 */
async function saveResults(reportData) {
  try {
    const fs = require('fs').promises;
    await fs.writeFile(
      'benchmark_results.json',
      JSON.stringify(reportData, null, 2)
    );
    console.log('✅ Results saved successfully!');
  } catch (error) {
    console.log('⚠️  Could not save to file (browser environment?)');
    console.log('📋 Copy results from console or use browser download');
  }
}

/**
 * Main execution
 */
async function main() {
  try {
    const results = await runAllBenchmarks();
    const report = generateReport(results);
    await saveResults(report);

    console.log('\n✨ Benchmark completed!\n');
    return report;

  } catch (error) {
    console.error('💥 Fatal error:', error);
    throw error;
  }
}

// Run if in Node.js
if (typeof module !== 'undefined' && module.exports) {
  main().catch(console.error);
}

// Export for browser/module use
if (typeof window !== 'undefined') {
  window.runGPT4Benchmark = main;
}
```

---

## Usage Instructions

### Prerequisites

1. **API Access**: You need a valid API key for the Gateway
2. **Models Available**: Ensure the GPT-4 models you want to test are accessible
3. **Environment**: Works in both Node.js and browser environments

### Configuration

Update these variables at the top of the script:

```javascript
const API_URL = 'http://localhost:8000/v1/chat/completions'; // Your Gateway URL
const API_KEY = 'your-api-key-here'; // Your API key
```

### Running in Node.js

```bash
# Save the script
curl -o gpt4_benchmark.js https://your-docs-url/benchmark-script.js

# Edit configuration
nano gpt4_benchmark.js  # Update API_URL and API_KEY

# Install dependencies (if needed)
npm install node-fetch  # Only for Node.js < 18

# Run the benchmark
node gpt4_benchmark.js
```

### Running in Browser Console

```javascript
// 1. Copy the entire script
// 2. Open browser console (F12)
// 3. Paste and press Enter
// 4. Run the benchmark:
await runGPT4Benchmark();
```

### Custom Model Selection

Edit the `GPT4_MODELS` array to test specific models:

```javascript
const GPT4_MODELS = [
  { provider: 'OpenAI', model: 'gpt-4' },
  { provider: 'MyCustomProvider', model: 'custom-gpt4' },
  // Add or remove models as needed
];
```

### Custom Prompts

Modify the `TEST_PROMPTS` object to use different input sizes:

```javascript
const TEST_PROMPTS = {
  'small': 'Your short prompt here',
  'medium': 'Your medium-length prompt here',
  'large': 'Your very long prompt here...'
};
```

---

## Output Format

### Console Output

The script provides real-time progress and a final summary:

```
🚀 Starting GPT-4 Multi-Provider Latency Benchmark

Testing 7 models with 3 prompt sizes

📊 Testing OpenAI/gpt-4:
  Testing OpenAI/gpt-4 with 10_tokens prompt...
    ✅ 10_tokens:
       Server TTFC: 125.3ms
       Client TTFC: 127.5ms
       Total Latency: 1450.2ms
       Tokens/sec: 45.2
       Output Tokens: 60
...

================================================================================
📈 BENCHMARK RESULTS SUMMARY
================================================================================

📝 10 TOKENS:

  🏃 Fastest TTFC (Time to First Chunk):
    1. OpenAI/gpt-4o: 98.2ms
    2. OpenRouter/openai/gpt-4-turbo: 112.5ms
    3. OpenAI/gpt-4-turbo: 125.3ms
...
```

### JSON Output

Results are saved to `benchmark_results.json`:

```json
{
  "summary": {
    "totalTests": 21,
    "successful": 20,
    "failed": 1,
    "timestamp": "2025-12-16T10:30:00.000Z"
  },
  "results": [
    {
      "provider": "OpenAI",
      "model": "gpt-4",
      "promptSize": "10_tokens",
      "success": true,
      "serverTTFC": 125.3,
      "serverTotalLatency": 1450.2,
      "serverStreamingTime": 1324.9,
      "serverTokensPerSecond": 45.2,
      "clientTTFC": 127.5,
      "clientTotalLatency": 1452.8,
      "inputTokens": 50,
      "outputTokens": 60,
      "totalTokens": 110,
      "chunkCount": 15,
      "timestamp": "2025-12-16T10:25:00.000Z"
    }
  ]
}
```

---

## Metrics Explained

### Time to First Chunk (TTFC)

**What it measures**: How long before the first response token arrives

**Why it matters**: Lower TTFC means faster perceived response time for users

**Typical values**:
- Excellent: < 200ms
- Good: 200-500ms
- Acceptable: 500-1000ms
- Slow: > 1000ms

### Tokens Per Second

**What it measures**: Speed of token generation during streaming

**Why it matters**: Higher tokens/sec means faster completion of responses

**Typical values**:
- Fast: > 50 tokens/sec
- Average: 30-50 tokens/sec
- Slow: < 30 tokens/sec

### Total Latency

**What it measures**: Complete time from request to final token

**Why it matters**: Overall user experience metric

**Components**:
- TTFC: Time to start
- Streaming time: Time to generate all tokens
- Network overhead: Round-trip time

---

## Troubleshooting

### Authentication Errors

```
Error: HTTP 401: Unauthorized
```

**Solution**: Check your API key is correct and has proper permissions

### Rate Limiting

```
Error: HTTP 429: Too Many Requests
```

**Solution**: Increase the delay between requests:

```javascript
// Change from 1000ms to 2000ms or more
await new Promise(resolve => setTimeout(resolve, 2000));
```

### Model Not Available

```
Error: Model 'gpt-4' not found
```

**Solution**: Check the model ID is correct and available in your deployment

### CORS Issues (Browser)

```
Error: CORS policy blocked
```

**Solution**: Use Node.js instead, or configure CORS on your Gateway

---

## Best Practices

1. **Run Multiple Times**: Latency varies; run 3-5 times and average results
2. **Same Conditions**: Test all models under similar network/load conditions
3. **Monitor Costs**: Streaming requests consume tokens; track usage
4. **Interpret Results**: Consider both TTFC and tokens/sec for user experience
5. **Document Environment**: Note date, time, location for reproducibility

---

## Integration Examples

### React Component

```javascript
import React, { useState } from 'react';

function BenchmarkRunner() {
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);

  const runBenchmark = async () => {
    setLoading(true);
    try {
      const report = await main(); // From the script
      setResults(report);
    } catch (error) {
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <button onClick={runBenchmark} disabled={loading}>
        {loading ? 'Running...' : 'Run Benchmark'}
      </button>
      {results && <pre>{JSON.stringify(results, null, 2)}</pre>}
    </div>
  );
}
```

### Next.js API Route

```javascript
// pages/api/benchmark.js
export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const results = await main(); // From the script
  res.status(200).json(results);
}
```

---

## Related Documentation

- [Latency Measurement Guide](./LATENCY_MEASUREMENT.md)
- [Streaming API Documentation](./STREAMING_API.md)
- [Provider Configuration](./PROVIDER_CONFIGURATION.md)

---

## Support

For issues or questions:
- Check existing documentation
- Review error messages carefully
- Test with a single model first
- Verify API key and permissions
